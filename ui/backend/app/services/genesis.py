"""Storyland 创世 (spec 3.3.3.4).

Single LLM call (Storyland·机制7) ingests the user's character cards +
worldbook and proposes the stage's full picture — entities and SPO
facts — following the five-step order INSIDE the prompt (物质基础 →
结构骨架 → 已有实体落位 → 宏观状态 → 文化风气, 机制 of one shot so a
step's hallucination can't be amplified by the next call).

Hard rules carried into the prompt:
- 机制5: every output must be derivable from the user's given
  material; nothing contradicting it, nothing invented from thin air
- 机制2: explicitly-documented characters / named worldbook places /
  factions / items register directly as entities
- 机制4: the stage is the largest-scope location entity

The proposal NEVER writes directly: it lands in genesis_proposals for
the user's review surface (机制6 — AI生成内容供用户审阅、修改、删除)，
and only the (possibly edited) reviewed lists are applied. Facts apply
at chapter 0 (变动表: source_chapter 0 = 初始生成); entities carry
``metadata_json.origin = 'genesis'`` + ``auto_created=1`` so the UI
can label them "AI生成".
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.services.genesis")


_ALLOWED_ENTITY_TYPES = {"character", "location", "item", "organization",
                         "concept"}
_ALLOWED_PREDICATES = {
    "位于", "相邻", "持有", "领导", "合并", "包含", "是...的...",
    "生死", "身体状况", "处境", "知晓", "具有",
    # state_extractor 词表兼容（loader 查询用）
    "在", "属于", "状态",
}

_SYSTEM = (
    "你是小说世界初始化助手（创世）。依据用户给定的角色卡与世界书，"
    "补全故事开始时舞台的全貌，产出实体与事实。\n"
    "你必须在一次输出内按以下五步顺序推导（先硬后软，后一步以前一步为约束）：\n"
    "步骤一·物质基础：依据用户给定的地理，推出舞台的生计、资源、经济形态，"
    "记为舞台地点实体的「具有」属性（如 具有(经济=海贸)）。\n"
    "步骤二·结构骨架：依据用户给定的社会形态，生成舞台的主要区域、内部势力、"
    "关键机构，新增实体并用「包含」「位于」「领导」记录归属。\n"
    "步骤三·已有实体落位：把用户已有的角色、势力放入舞台，补全它们之间"
    "缺失的关系（「位于」「包含」「领导」「是...的...」）。\n"
    "步骤四·宏观状态：由前三步推出舞台整体状态，记为舞台地点实体的「具有」"
    "标量（稳定度、繁荣度、治安等，0-10）。\n"
    "步骤五·文化风气（仅在材料足够时）：由物质基础+社会形态推出风俗，"
    "优先级最低的「具有」描述。\n\n"
    "铁律：\n"
    "1. 每一条产出必须能从用户给定的信息推出；与用户描述无关、或与用户"
    "给定事实矛盾的设定，一律不生成\n"
    "2. 用户角色卡中的每个角色、世界书中有名字的地点/势力/物品，直接登记为实体\n"
    "3. 舞台 = 当前范围最大的地点实体，必须存在且 entity_type=location\n"
    "4. 谓词只能从：位于/相邻/持有/领导/合并/包含/是...的.../生死/身体状况/"
    "处境/知晓/具有 中选\n"
    "5. entity_type 只能是 character/location/item/organization/concept\n"
    "6. 禁止使用 emoji\n\n"
    "输出 JSON：\n"
    "{\n"
    '  "stage": "舞台地点实体名",\n'
    '  "entities": [{"name":..., "entity_type":..., "description":..., "aliases":[...], "from_user_material": true|false}],\n'
    '  "facts": [{"subject":..., "predicate":..., "object":..., "step": 1-5, "basis": "推导依据（引用用户材料）"}]\n'
    "}"
)

_USER_TMPL = (
    "## 用户角色卡\n{characters}\n\n"
    "## 用户世界书\n{worldbook}\n\n"
    "请按五步顺序完成创世，输出 JSON。"
)


def _gather_material(db_path: str, project_id: str) -> tuple[str, str]:
    """Render character cards + worldbook entries as prompt material."""
    from ui.backend.app.services import project_store

    chars: list[str] = []
    for c in project_store.list_characters(db_path, project_id):
        parts = [f"### {c.get('name')}"]
        for key, label in (("role", "定位"), ("personality", "性格"),
                           ("background", "背景"), ("appearance", "外貌"),
                           ("speech_style", "口癖")):
            v = (c.get(key) or "").strip()
            if v:
                parts.append(f"{label}: {v}")
        chars.append("\n".join(parts))

    wb: list[str] = []
    for e in project_store.list_worldbook(db_path, project_id):
        title = (e.get("title") or "").strip()
        content = (e.get("content") or "").strip()
        if not title and not content:
            continue
        wb.append(f"### [{e.get('category') or '其他'}] {title}\n{content}")

    return ("\n\n".join(chars) or "（无角色卡）",
            "\n\n".join(wb) or "（无世界书条目）")


def _extract_json(raw: str) -> dict:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        raise ValueError(f"no JSON object in genesis response: {raw[:200]}")
    return json.loads(m.group(0))


def _normalize(parsed: dict) -> dict:
    """Validate / clean the LLM proposal before it reaches the review
    surface."""
    stage = str(parsed.get("stage") or "").strip()
    entities: list[dict] = []
    seen: set[str] = set()
    for e in (parsed.get("entities") or []):
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        etype = str(e.get("entity_type") or "").strip()
        if not name or name in seen or etype not in _ALLOWED_ENTITY_TYPES:
            continue
        seen.add(name)
        aliases = e.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        entities.append({
            "name": name,
            "entity_type": etype,
            "description": str(e.get("description") or "").strip()[:500],
            "aliases": [str(a).strip() for a in aliases if str(a).strip()],
            "from_user_material": bool(e.get("from_user_material")),
        })
    facts: list[dict] = []
    for f in (parsed.get("facts") or []):
        if not isinstance(f, dict):
            continue
        subject = str(f.get("subject") or "").strip()
        predicate = str(f.get("predicate") or "").strip()
        obj = str(f.get("object") or "").strip()
        if not subject or not obj or predicate not in _ALLOWED_PREDICATES:
            continue
        try:
            step = max(1, min(5, int(f.get("step") or 3)))
        except (TypeError, ValueError):
            step = 3
        facts.append({
            "subject": subject, "predicate": predicate, "object": obj,
            "step": step,
            "basis": str(f.get("basis") or "").strip()[:300],
        })
    return {"stage": stage, "entities": entities, "facts": facts}


def build_genesis_prompt(db_path: str, project_id: str) -> tuple[str, str]:
    """(user_prompt, system_prompt) — the exact text either execution
    mode sends, so API / 手动模式 (LLM交互·机制4) stay byte-identical."""
    chars, wb = _gather_material(db_path, project_id)
    if chars.startswith("（无") and wb.startswith("（无"):
        raise ValueError("项目还没有角色卡或世界书条目，无法创世")
    return _USER_TMPL.format(characters=chars, worldbook=wb), _SYSTEM


async def generate_raw(db_path: str, project_id: str) -> str:
    """API mode: ONE LLM call (Storyland·机制7), returns the raw text."""
    prompt, system = build_genesis_prompt(db_path, project_id)
    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="storyland.genesis",
        primary_role="creative",
        parsed_target_table="truth_current_state",
        default_max_tokens=4000, default_temperature=0.4,
    )
    return await cs.invoke(
        prompt=prompt, system=system,
        project_id=project_id, db_path=db_path,
    )


def submit_raw(db_path: str, project_id: str, raw: str) -> dict[str, Any]:
    """Shared landing point for BOTH modes (LLM交互·机制4 手动模式:
    用户粘贴网页版回复 → 自动解析并存为待审提案). Parses + normalizes
    the response text and stores it as the pending genesis proposal —
    审阅区确认后才入库 (机制6)."""
    proposal = _normalize(_extract_json(raw))
    pid = f"gen_{uuid.uuid4().hex[:12]}"
    with sqlite3.connect(db_path) as con:
        con.execute(
            "UPDATE pending_state_extractions SET status='discarded', "
            "resolved_at=CURRENT_TIMESTAMP "
            "WHERE project_id=? AND chapter_id='__genesis__' "
            "AND status='pending'",
            (project_id,),
        )
        con.execute(
            "INSERT INTO pending_state_extractions "
            "(proposal_id, project_id, chapter_id, chapter_num, "
            " parsed_json, llm_model) "
            "VALUES (?, ?, '__genesis__', 0, ?, 'genesis')",
            (pid, project_id, json.dumps(proposal, ensure_ascii=False)),
        )
        con.commit()
    return {"proposal_id": pid, **proposal}


async def run_genesis(db_path: str, project_id: str) -> dict[str, Any]:
    """One-shot genesis call → pending proposal for review. Returns
    {proposal_id, stage, entities, facts}."""
    raw = await generate_raw(db_path, project_id)
    return submit_raw(db_path, project_id, raw)


def get_pending_genesis(db_path: str, project_id: str) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT proposal_id, parsed_json, created_at "
            "FROM pending_state_extractions "
            "WHERE project_id=? AND chapter_id='__genesis__' "
            "AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    if not row:
        return None
    try:
        parsed = json.loads(row["parsed_json"] or "{}")
    except Exception:
        parsed = {}
    return {"proposal_id": row["proposal_id"],
            "created_at": row["created_at"], **parsed}


def apply_genesis(
    db_path: str, project_id: str, proposal_id: str,
    *, entities: list[dict] | None = None, facts: list[dict] | None = None,
) -> dict[str, Any]:
    """Apply the user-reviewed genesis lists in ONE transaction.

    ``entities`` / ``facts`` are the (possibly edited / pruned) lists
    from the review surface; ``None`` means "apply the stored proposal
    verbatim". Existing entities with the same name are skipped
    (正文/用户已有内容覆盖 AI 生成 — 机制6).
    """
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM pending_state_extractions WHERE proposal_id=?",
            (proposal_id,),
        ).fetchone()
        if not row:
            raise ValueError("genesis proposal not found")
        if row["status"] != "pending":
            raise ValueError(f"genesis proposal already {row['status']}")
        stored = json.loads(row["parsed_json"] or "{}")

    payload = _normalize({
        "stage": stored.get("stage", ""),
        "entities": entities if entities is not None
                    else stored.get("entities", []),
        "facts": facts if facts is not None else stored.get("facts", []),
    })

    created_entities = 0
    skipped_entities = 0
    created_facts = 0
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys = ON;")
        con.execute("BEGIN")
        existing_names = {
            r[0] for r in con.execute(
                "SELECT name FROM storyland_entities WHERE project_id=?",
                (project_id,),
            )
        }
        for e in payload["entities"]:
            if e["name"] in existing_names:
                skipped_entities += 1
                continue
            con.execute(
                "INSERT INTO storyland_entities "
                "(entity_id, project_id, name, entity_type, description, "
                " introduced_chapter, aliases_json, metadata_json, source, "
                " auto_created) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?, 'manual', 1)",
                (f"ent_{uuid.uuid4().hex[:12]}", project_id, e["name"],
                 e["entity_type"], e["description"],
                 json.dumps(e["aliases"], ensure_ascii=False),
                 json.dumps({"origin": "genesis"}, ensure_ascii=False)),
            )
            existing_names.add(e["name"])
            created_entities += 1

        # subject_type lookup: proposal entities first, then the
        # entity registry; unknown subjects default to character.
        type_by_name = {e["name"]: e["entity_type"]
                        for e in payload["entities"]}
        for r in con.execute(
            "SELECT name, entity_type FROM storyland_entities "
            "WHERE project_id=?", (project_id,),
        ):
            type_by_name.setdefault(r[0], r[1])

        for f in payload["facts"]:
            # 机制6: 已有同 (subject, predicate) 的活动事实优先（用户/
            # 正文写过的不被创世覆盖）。
            dup = con.execute(
                "SELECT 1 FROM truth_current_state "
                "WHERE project_id=? AND subject=? AND predicate=? "
                "AND valid_to_chapter IS NULL",
                (project_id, f["subject"], f["predicate"]),
            ).fetchone()
            if dup and f["predicate"] != "具有":
                continue
            subject_type = type_by_name.get(f["subject"], "character")
            con.execute(
                "INSERT INTO truth_current_state "
                "(triple_id, project_id, subject, subject_type, predicate, "
                " object, valid_from_chapter) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (f"tcs_{uuid.uuid4().hex[:12]}", project_id, f["subject"],
                 subject_type, f["predicate"], f["object"]),
            )
            con.execute(
                "INSERT INTO state_change_log "
                "(log_id, project_id, subject, subject_type, predicate, "
                " old_value, new_value, change_chapter, trigger, source, "
                " change_type, llm_model_used) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, 0, ?, "
                " 'llm_auto', 'state_change', 'genesis')",
                (f"sl_{uuid.uuid4().hex[:12]}", project_id, f["subject"],
                 subject_type, f["predicate"], f["object"],
                 f"创世·步骤{f['step']}: {f['basis']}"[:300]),
            )
            created_facts += 1

        con.execute(
            "UPDATE pending_state_extractions SET status='applied', "
            "resolved_at=CURRENT_TIMESTAMP WHERE proposal_id=?",
            (proposal_id,),
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    return {
        "stage": payload["stage"],
        "entities_created": created_entities,
        "entities_skipped": skipped_entities,
        "facts_created": created_facts,
    }
