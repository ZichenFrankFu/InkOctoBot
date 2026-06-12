"""Materialise an accepted domain suggestion into a knowledge skill.

The spec calls this "gate 2 accept": only at this point does the
compiled content move from ``domain_suggestions.compiled_content``
(soft, in-DB) to a real filesystem skill in
``agents/knowledge_skills/<slug>/`` that the ``skills`` loader can
recall by embedding similarity.

Critically, anything in ``status='needs_review'`` lives ONLY in the
DB and never reaches ``SkillRegistry`` — that's the safety property
the spec asks us to enforce. We never write SKILL.md/skill.py for a
suggestion in any other state."""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.agents.learning.knowledge_skill_writer")


_KNOWLEDGE_DIR = Path(__file__).resolve().parents[1] / "knowledge_skills"


def _slugify(name: str) -> str:
    """Lower-snake-case ascii slug for filesystem use."""
    s = re.sub(r"[^a-zA-Z0-9一-鿿]+", "_", name.strip()).strip("_")
    s = s.lower() or "domain"
    return s[:48]


def _skill_name_for(domain: str) -> str:
    return f"domain_{_slugify(domain)}"


# ─────────── SKILL.md / skill.py templates ───────────


_SKILL_MD_TEMPLATE = """---
name: {skill_name}
description: {description}
---
{body}

<!-- meta: source=web_compiled domain={domain} authoritative=false source_mode={mode} compiled_at={ts} -->
"""


_SKILL_PY_TEMPLATE = '''"""
{skill_name} — AI-compiled domain knowledge (Part B).

This file is a no-op pass-through: the entire payload of a knowledge
skill lives in SKILL.md, which the prompt-context skills loader pulls
straight into the model prompt. The Python module exists only because
``SkillRegistry`` requires a skill.py alongside every SKILL.md.
"""
from __future__ import annotations

from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    """Knowledge-only skill — body lives in SKILL.md, no LLM logic here."""

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="{skill_name}",
            display_name="{display_name}",
            description="{description}",
            version="1.0.0",
            tags=["domain_knowledge", "web_compiled"],
        )

    def build_prompt(self, payload: dict) -> str:
        return ""

    def parse_output(self, raw: str) -> dict:
        return {{"raw": raw}}
'''


def _short_description(domain: str) -> str:
    return (
        f"{domain} 领域知识——AI 综合 web 资料编译，"
        "非权威，仅供网络小说创作参考质感。"
    )


def _annotated_body(body: str) -> str:
    """Prepend the "AI-compiled, non-authoritative" disclaimer that the
    spec wants visible inside the prompt as well as in the metadata."""
    header = (
        "> 本文为 AI 基于联网搜索综合编译的领域知识，"
        "**非权威来源**，仅供创作质感参考。如有冲突以专业资料为准。\n\n"
    )
    return header + (body or "").strip()


# ─────────── writer ───────────


def write_knowledge_skill(
    *, domain: str, body: str, source_mode: str = "api",
    overwrite: bool = True,
) -> dict[str, Any]:
    """Write the SKILL.md + skill.py pair for a domain.

    Returns ``{path, skill_name, slug}``. Caller is expected to invoke
    ``register_with_index`` afterwards to make the skills loader see
    it. We keep them separate because tests want to assert "the file
    on disk exists" independently of registry/embedding plumbing."""
    slug = _slugify(domain)
    skill_name = _skill_name_for(domain)
    target_dir = _KNOWLEDGE_DIR / slug
    if target_dir.exists() and not overwrite:
        raise FileExistsError(f"knowledge skill {slug!r} already exists")
    target_dir.mkdir(parents=True, exist_ok=True)

    description = _short_description(domain)
    md = _SKILL_MD_TEMPLATE.format(
        skill_name=skill_name, description=description,
        body=_annotated_body(body),
        domain=domain, mode=source_mode, ts=int(time.time()),
    )
    py = _SKILL_PY_TEMPLATE.format(
        skill_name=skill_name,
        display_name=f"{domain}（领域知识）",
        description=description,
    )
    (target_dir / "SKILL.md").write_text(md, encoding="utf-8")
    (target_dir / "skill.py").write_text(py, encoding="utf-8")
    return {"path": str(target_dir), "skill_name": skill_name, "slug": slug}


def remove_knowledge_skill(*, domain: str) -> bool:
    slug = _slugify(domain)
    target_dir = _KNOWLEDGE_DIR / slug
    if not target_dir.exists():
        return False
    for f in target_dir.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        target_dir.rmdir()
    except OSError:
        pass
    return True


# ─────────── registry + skill_index plumbing ───────────


def register_with_index(
    *, db_path: str, skill_name: str, body: str, recompute_embedding: bool = True,
) -> dict[str, Any]:
    """Force a registry rescan, sync the DB mirror, and (re)compute
    this skill's embedding so the skills loader can rank it.

    Safe to call from a sync handler. Embedding recomputation is best
    effort — failures are logged but never raise."""
    # 1) Hot-reload registry so it picks up the new SKILL.md / skill.py.
    try:
        from ui.backend.app.routers.skill_api import _get_registry
        registry = _get_registry()
        registry.scan_all()
    except Exception as e:
        logger.debug("registry rescan failed: %s", e)

    # 2) Sync DB mirror.
    try:
        from ui.backend.app.services import skill_index
        skill_index.sync_from_registry(db_path, force=True)
    except Exception as e:
        logger.warning("skill_index sync failed: %s", e)

    # 3) Recompute embedding for this row.
    if recompute_embedding:
        try:
            _recompute_embedding(db_path, skill_name, body)
        except Exception as e:
            logger.warning("embedding recompute failed for %s: %s",
                          skill_name, e)

    return {"skill_name": skill_name}


def _recompute_embedding(db_path: str, skill_id: str, body: str) -> None:
    from ui.backend.app.services import skill_index
    from ui.backend.app.services.prompt_context.utils import (
        current_embedding_model_key, embed_sync,
    )
    row = skill_index.get_skill(db_path, skill_id, include_embedding=True)
    if not row:
        logger.debug("recompute_embedding: %s not in index yet", skill_id)
        return
    text = skill_index.skill_embedding_text({
        "display_name": row.get("display_name") or "",
        "description":  row.get("description") or "",
        "body_snippet": (body or "")[:1200],
    })
    h = skill_index.hash_embedding_text(text)
    vecs = embed_sync([text])
    vec = vecs[0] if vecs else []
    if vec:
        skill_index.set_embedding(
            db_path, skill_id, vec, h,
            model_key=current_embedding_model_key(),
        )


# ─────────── accept (gate 2) ───────────


def _open(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def accept_suggestion(
    *, db_path: str, suggestion_id: str,
    edited_content: str | None = None,
) -> dict[str, Any]:
    """Gate-2 accept handler.

    - Requires the row to be in ``needs_review`` — accepting anything
      else is a bug (e.g. accepting a ``proposed`` row would publish
      compiled_content='' as a knowledge skill).
    - If ``edited_content`` is provided, that's what's stored / written;
      otherwise we use the original ``compiled_content``.
    - Writes SKILL.md/skill.py, registers, recomputes embedding,
      flips status to ``accepted`` + records ``skill_name``."""
    with _open(db_path) as con:
        row = con.execute(
            "SELECT * FROM domain_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
    if not row:
        return {"status": "missing"}
    if row["status"] != "needs_review":
        return {
            "status": "rejected",
            "reason": f"cannot accept from status={row['status']!r}",
        }

    domain = row["domain"]
    body = (edited_content if edited_content is not None
            else row["compiled_content"]) or ""
    if not body.strip():
        return {"status": "rejected", "reason": "empty_content"}

    written = write_knowledge_skill(
        domain=domain, body=body,
        source_mode=row["source_mode"] or "api",
    )
    register_with_index(
        db_path=db_path, skill_name=written["skill_name"], body=body,
    )

    with _open(db_path) as con:
        con.execute(
            """UPDATE domain_suggestions
               SET status='accepted',
                   compiled_content=?,
                   skill_name=?,
                   updated_at=?
               WHERE suggestion_id=?""",
            (body, written["skill_name"], time.time(), suggestion_id),
        )
        con.commit()

    return {
        "status": "accepted",
        "skill_name": written["skill_name"],
        "path": written["path"],
    }


def update_accepted_content(
    *, db_path: str, suggestion_id: str, new_content: str,
) -> dict[str, Any]:
    """Edit the body of an already-accepted knowledge skill.

    Rewrites SKILL.md, recomputes embedding (otherwise the loader
    would keep recalling by the old content) and updates the DB row.
    """
    with _open(db_path) as con:
        row = con.execute(
            "SELECT * FROM domain_suggestions WHERE suggestion_id = ?",
            (suggestion_id,),
        ).fetchone()
    if not row:
        return {"status": "missing"}
    if row["status"] != "accepted":
        return {
            "status": "rejected",
            "reason": f"not accepted (status={row['status']!r})",
        }

    written = write_knowledge_skill(
        domain=row["domain"], body=new_content,
        source_mode=row["source_mode"] or "api",
    )
    register_with_index(
        db_path=db_path, skill_name=written["skill_name"],
        body=new_content, recompute_embedding=True,
    )
    with _open(db_path) as con:
        con.execute(
            "UPDATE domain_suggestions SET compiled_content=?, updated_at=? "
            "WHERE suggestion_id=?",
            (new_content, time.time(), suggestion_id),
        )
        con.commit()
    return {"status": "accepted", "skill_name": written["skill_name"]}
