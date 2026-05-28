"""End-to-end demo of the market extractor pipeline.

Seeds a realistic mock crawler DB (10 novels × 5 chapters of believable
xianxia content) + mocks the post_commit LLM to return realistic 15-dim
features + neologism classifications + profile synthesis. Runs all 6
phases and prints:
  - Phase 4 per-call audit rows + token counts (proxy for cost)
  - Phase 5 holdout similarity score + confidence label
  - Final loader_payload first 300 chars
  - llm_outputs audit summary by call_site_id

Use this to validate the pipeline data flow without needing real
crawler data or network access.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sqlite3
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.market_extractor import (
    job_runner, llm_extractor, neologism_extractor, profile_synthesizer,
)


# ─────────── seed data ───────────


_OPENING_TEMPLATES = [
    "夜，黑得诡异。{name}独自坐在{location}的山巅之上，望着远处翻涌的云海。"
    "三日前，宗门突遭剧变，他从内门弟子一夜之间沦为通缉对象。\n\n"
    "“{quote}”他喃喃自语，眼中怒火渐起。\n\n"
    "灵气在他周身流转，{system}的玉简在掌中泛起微光——这是他唯一的依靠。",

    "“滚开。”{name}冷冷地丢下一句话，背影消失在{location}的尽头。\n\n"
    "身后的弟子们面面相觑——谁也没想到，这个昨天还被人嘲笑的废柴，"
    "今天突然以一招{system}打败了所谓的天才。\n\n"
    "“他...他是怎么做到的？”有人颤声问道。\n\n"
    "没有人回答。{name}的传说，从这一刻开始悄然展开。",

    "雷劫终于停了。{name}从破碎的{location}中走出，浑身浴血，眼神却前所未有的坚定。"
    "他抬手一翻，那枚刻着古字的{system}静静悬浮，散发出温润的光。\n\n"
    "“原来如此。”他喃喃道。三年了，他终于明白祖父留下的那句遗言。\n\n"
    "山下传来杂乱的脚步声——追兵到了。{name}轻轻一笑：这一次，逃的人不会是他。",
]


_NOVEL_NAMES = [
    "万古第一神", "诡秘修真路", "玄阴录", "九霄剑诀", "青云传说",
    "斗破苍穹之子", "灵境守门人", "万妖始祖", "天命剑神", "鸿蒙归墟",
]
_LOCATIONS = ["幽冥谷", "青云山", "九重天", "玄阴峰", "万剑宗", "鸿蒙界"]
_MC_NAMES = ["林天", "苏离", "陆远", "叶凡", "萧炎", "韩立"]
_SYSTEMS = ["序列玉简", "焚天诀", "九转金身", "万劫不灭体", "鸿蒙印记"]
_QUOTES = [
    "我若成神，必踏破这天",
    "宗门待我如此，便莫怪我无情",
    "三年河东，三年河西",
    "走着瞧——我会让你们后悔的",
]


def _gen_chapter(num: int, novel_name: str, rng: random.Random) -> str:
    """Realistic ~1500-char xianxia chapter."""
    tmpl = rng.choice(_OPENING_TEMPLATES)
    body = tmpl.format(
        name=rng.choice(_MC_NAMES),
        location=rng.choice(_LOCATIONS),
        system=rng.choice(_SYSTEMS),
        quote=rng.choice(_QUOTES),
    )
    # Tack on filler paragraphs to hit a realistic length.
    filler_pool = [
        "他闭目调息，丹田中的真气一圈圈翻涌。修炼之路，从来不是平坦的。",
        "远处山林间隐隐传来鸟鸣，仿佛在嘲笑他的狼狈，又像是给他无声的鼓励。",
        "宗门内的恩怨，是非曲直，本就难以言说。这一次，他终于选择了不再退让。",
        "三天前的那一战，至今历历在目。师姐倒下的那一刻，他第一次明白什么叫做绝望。",
        "玉简里的字符越来越清晰，那是上古的传承，是真正能让他踏上巅峰之路的根基。",
        "妖兽的吼声划破长空，可他面色不改——这点小角色，已经不足以让他停下脚步了。",
        "光柱从天而降，整座山头被笼罩在金色的光辉中。这是某种古老仪式的征兆。",
    ]
    for _ in range(8):
        body += "\n\n" + rng.choice(filler_pool)
    return body


def seed_crawler_db() -> str:
    """Build a temp crawler DB with 10 novels × 5 chapters."""
    rng = random.Random(42)
    path = os.path.join(tempfile.mkdtemp(), "Crawler.db")
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE novels (novel_id TEXT PRIMARY KEY, title TEXT, "
            "platform TEXT, category TEXT, "
            "collection_count INTEGER, comment_count INTEGER, "
            "monthly_ticket INTEGER, word_count INTEGER, "
            "last_updated_at TEXT)"
        )
        con.execute(
            "CREATE TABLE chapters (novel_id TEXT, chapter_num INTEGER, "
            "content TEXT, PRIMARY KEY(novel_id, chapter_num))"
        )
        for i in range(10):
            nid = f"n{i:03d}"
            con.execute(
                "INSERT INTO novels VALUES (?, ?, 'qidian', 'xianxia', "
                "?, ?, ?, ?, '2025-05-01')",
                (nid, _NOVEL_NAMES[i], 800 + i * 300, 200 + i * 50,
                 80 + i * 12, 250_000 + i * 30_000),
            )
            for k in range(1, 6):
                con.execute(
                    "INSERT INTO chapters VALUES (?, ?, ?)",
                    (nid, k, _gen_chapter(k, _NOVEL_NAMES[i], rng)),
                )
        con.commit()
    print(f"[seed] crawler DB: 10 novels × 5 chapters @ {path}")
    return path


def init_project_db() -> str:
    path = os.path.join(tempfile.mkdtemp(), "novels.db")
    con = sqlite3.connect(path)
    ensure_creation_tables(con)
    con.commit()
    con.close()
    print(f"[seed] project DB: {path}")
    return path


# ─────────── realistic LLM mocks ───────────


_FAKE_15DIM = {
    "A1_protagonist_first_appearance": {
        "chapter": 1, "position": "第一段", "context": "动作",
        "is_first_sentence": False, "screen_time_ratio": 0.85,
    },
    "A2_protagonist_profile": {
        "strength_position": "隐藏强势", "personality_keywords": ["冷酷", "坚定", "复仇"],
        "appearance_detail": "1句", "age_group": "青年", "contrast_point": "看似废柴实则强大",
    },
    "A3_protagonist_cheat": {
        "type": "神秘传承", "source": "先天", "revealed_chapter": 1,
        "reveal_level": "暗示", "description": "祖父遗物中的古老玉简",
    },
    "A4_protagonist_agency": {"mode": "Proactive", "first_decision_chapter": 1, "decision_cost": "高"},
    "A5_drive_conflict": {
        "type": "复仇", "urgency": "致命紧迫", "object": "群体",
        "method": "武力", "description": "被宗门陷害，立志报仇",
    },
    "B1_social_network": {
        "characters_in_5ch": ["主角", "师姐", "宗主", "二长老"],
        "key_relationships": [{"name": "师姐", "relation": "亡故"}],
    },
    "B2_focus_strategy": {
        "mode": "单主角", "character_count_5ch": 4,
        "antagonist_first_chapter": 1, "antagonist_type": "明面",
    },
    "C1_worldview": {"power_system": "修仙", "time_space": "异界", "hybrid_type": "单一题材", "novelty_level": "经典"},
    "C2_setting_unfold": {
        "word_ratio": 0.15, "style": "渐进揭露",
        "first_chapter_density": "中", "core_revealed": "部分",
    },
    "C3_setting_contrast": {"point": "玉简继承设定"},
    "D1_opening_hook": {
        "type": "开篇即衰落", "description": "主角昨日还是天才，今日沦为通缉",
        "trigger_position": "第一段",
    },
    "D2_pleasure_points": {
        "has_early_face_slap": True, "first_face_slap_chapter": 2,
        "type": "装逼爽点", "density": 3,
    },
    "E1_writing_style": {
        "keywords": ["冷酷", "热血"],
        "representative_sentences": ["“我若成神，必踏破这天”"],
    },
    "E2_emotional_tone": {"dominant": "复仇", "volatility": "持续高强度", "darkness": "灰暗"},
    "F1_info_disclosure": {
        "strategy": "渐进揭露",
        "early_foreshadows": ["祖父遗言", "师姐死因", "宗门内乱"],
        "revealed_vs_planted_ratio": 0.4,
    },
    "F2_first_arc": {"concept": "复仇 + 揭露真相", "locked_chapter": 1},
    "G1_chapter_end_hook": {"type": "悬念", "strength": "强"},
}

_FAKE_NEOLOGISMS = [
    {"term": "序列玉简", "is_work_specific": True, "type": "item",
     "definition_hint": "祖传神秘玉简，蕴含上古传承"},
    {"term": "万剑宗", "is_work_specific": True, "type": "organization",
     "definition_hint": "主角所在的修仙门派"},
    {"term": "玄阴峰", "is_work_specific": True, "type": "location",
     "definition_hint": "宗门的修炼圣地之一"},
]

_FAKE_PROFILE = {
    "profile_summary": (
        "起点-玄幻类作品以「开篇即衰落 + 复仇驱动」为主流开局结构。"
        "前 5 章普遍配置：装逼打脸（chapter 2 出现）+ 渐进揭露世界观 +"
        "强悬念章末钩子。情绪基调倾向灰暗，主角多为「隐藏强势」型青年。"
    ),
    "recommended_openings": [
        {"name": "落难复仇流", "structure": "主角昨日辉煌→今日沦落→誓言反击",
         "example_template": "夜深，{主角}独立{圣地之巅}，望着遥远的{敌方阵营}..."},
        {"name": "雷劫渡尽流", "structure": "天劫→突破→新身份揭示",
         "example_template": "雷停了。{主角}从{破碎之地}走出，眼神{形容词}..."},
        {"name": "冷面打脸流", "structure": "嘲讽场景→反手碾压→众人愕然",
         "example_template": "“滚开。”{主角}冷冷丢下一句..."},
    ],
    "style_baseline": {
        "sentence_style": "短句为主，对话冷峻干脆",
        "dialogue_ratio": "约 30-40%",
        "paragraph_rhythm": "短段落 + 单句段（爽点强调），中段叙述长 80-150 字",
        "punctuation_habit": "感叹号、破折号高频使用，营造节奏感",
    },
    "signature_devices_description": (
        "金手指多为「祖辈遗物 / 神秘传承」类（避免直接系统流），"
        "打脸场景在 chapter 2-3 必出现，章末钩子强度均为「悬念」或「揭示」。"
    ),
    "pacing_guidance": {
        "first_chapter": "第一句即冲突，10 段内出现核心金手指线索",
        "first_5_chapters": "至少 1 次打脸 + 3 个伏笔埋设 + 1 次世界观揭露",
        "first_arc": "20-30 章内完成首次实力突破 + 揭示主角真实身份",
    },
    "loader_payload": (
        "【起点·玄幻 平台风格基线】\n"
        "开局：「开篇即衰落 + 复仇驱动」为主流。第一段即冲突，10 段内引入金手指线索（祖辈遗物/神秘传承类，避免系统流）。\n"
        "前 5 章节奏：必出至少 1 次打脸（chapter 2-3），埋 3 个伏笔，1 次世界观揭露。\n"
        "句式：短句为主，对话冷峻干脆。段落短 + 单句段强调爽点。感叹号、破折号高频。\n"
        "情绪：复仇基调，持续高强度，灰暗色调。主角多为「隐藏强势」型青年。\n"
        "章末钩子：强悬念或揭示，承接到下章。\n"
        "首卷：20-30 章内完成首次实力突破 + 揭示主角真实身份。"
    ),
}


# Mock at the FallbackRouter level — preserves LLMCallSite layer so
# every call still writes one row to llm_outputs. The mock chooses
# what to return based on the call_site_id encoded in the prompt.
class _FakeFR:
    def __init__(self):
        self.call_count = 0

    async def invoke(self, *, primary_role, prompt, system=None,
                     max_tokens=2000, temperature=0.3, **kw):
        self.call_count += 1
        # Route by most-specific marker first.
        p = prompt or ""
        if "网文市场分析专家" in p or "平台风格档案" in p:
            return json.dumps(_FAKE_PROFILE, ensure_ascii=False)
        if "网文专有名词分析专家" in p or "候选词清单" in p:
            return json.dumps({"neologisms": _FAKE_NEOLOGISMS},
                                ensure_ascii=False)
        if "15 项特征" in p or "网文分析专家" in p:
            return json.dumps(_FAKE_15DIM, ensure_ascii=False)
        return "{}"


# ─────────── run ───────────


async def main() -> None:
    crawler_db = seed_crawler_db()
    project_db = init_project_db()

    print("\n[run] starting 6-phase pipeline (with LLMCallSite audit)...")
    fr = _FakeFR()
    # Patch project_paths.get_db_path so LLMCallSite knows where to write
    # llm_outputs; patch FallbackRouter so the auto path resolves to our
    # fake. ModelRouter.resolve_role returns ("ollama", "") by default —
    # we override that too so the audit row records a meaningful model.
    from llm import router as router_mod

    class _FakeRouter:
        def resolve_role(self, role):
            return ("anthropic", "claude-haiku-4-5-20251001")
        async def invoke(self, *, role, prompt, system=None, **kw):
            return await fr.invoke(primary_role=role, prompt=prompt,
                                    system=system, **kw)
        async def generate(self, *, agent_role, messages, **kw):
            from llm.base import LLMResponse
            return LLMResponse(content="(generate path not used)",
                                model="claude-haiku-4-5", input_tokens=0,
                                output_tokens=0, raw={})

    with mock.patch.object(router_mod, "ModelRouter", _FakeRouter), \
         mock.patch(
             "llm.fallback_router.get_fallback_router", return_value=fr,
         ), \
         mock.patch(
             "ui.backend.app.services.project_paths.get_db_path",
             return_value=project_db,
         ):
        job_id = await job_runner.run_job_async(
            project_db, "qidian", "xianxia", crawler_db=crawler_db,
        )

    # ── Job summary ──
    job = job_runner.get_job_status(project_db, job_id)
    print(f"\n[job] {job_id} state={job['state']} progress_pct={job['progress_pct']}")
    print(f"[job] chapters_processed={job['chapters_processed']} "
          f"cloud_llm_calls={job['cloud_llm_calls']}")
    print(f"[job] created_profile_id={job['created_profile_id']}")

    # ── Phase 4: per-call audit (cost proxy) ──
    print("\n──────── Phase 4 — LLM audit rows by call_site ────────")
    with sqlite3.connect(project_db) as con:
        for row in con.execute(
            "SELECT call_site_id, COUNT(*), SUM(token_count_prompt), "
            "SUM(token_count_response), SUM(duration_ms) "
            "FROM llm_outputs GROUP BY call_site_id "
            "ORDER BY COUNT(*) DESC"
        ):
            cs, n, tp, tr, dur = row
            print(f"  {cs:50s}  calls={n:3d}  in_tok={tp:6d}  out_tok={tr:5d}  total_ms={dur:.1f}")

    # ── Phase 5: holdout confidence ──
    print("\n──────── Phase 5 — Holdout evaluation ────────")
    with sqlite3.connect(project_db) as con:
        prof = con.execute(
            "SELECT profile_id, source_works_count, holdout_similarity_score, "
            "confidence_label FROM platform_profiles WHERE profile_id = ?",
            (job["created_profile_id"],),
        ).fetchone()
        print(f"  profile_id:            {prof[0]}")
        print(f"  source_works_count:    {prof[1]}")
        print(f"  holdout_similarity:    {prof[2]}")
        print(f"  confidence_label:      {prof[3]}")

    # ── loader_payload preview ──
    print("\n──────── Final platform_profile.loader_payload (first 300 chars) ────────")
    with sqlite3.connect(project_db) as con:
        payload = con.execute(
            "SELECT loader_payload FROM platform_profiles WHERE profile_id = ?",
            (job["created_profile_id"],),
        ).fetchone()[0]
        print(payload[:300])
        print(f"\n  [full payload length: {len(payload)} chars]")


if __name__ == "__main__":
    asyncio.run(main())
