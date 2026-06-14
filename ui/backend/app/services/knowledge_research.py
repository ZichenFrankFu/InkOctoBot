"""专业知识自学习 (spec 4.2).

User-confirmed research on a professional domain (机制1: 触发前由用户
确认是否需要研究), executed with the provider's hosted web-search tool
when available; the compiled knowledge lands DIRECTLY in the skill
index as a ``kind='knowledge'`` skill — no second confirmation
(机制2: 用户可能无专业背景，不要求查验真伪，但可查看/修改). Every
compiled body is labeled "AI编译 / 非权威" (机制3). Knowledge skills
are recalled into prompts by the skills loader via chapter-embedding
similarity, the same path as every other skill (机制4).
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

logger = logging.getLogger("inkoctobot.services.knowledge_research")


NON_AUTHORITATIVE_LABEL = "［AI编译 / 非权威］"

_RESEARCH_PROMPT = (
    "你在为一位网文作者编译「{domain}」领域的写作参考知识。\n"
    "{context_part}"
    "请整理出对小说写作最有用的内容：\n"
    "1. 该领域的关键事实、流程、规则（写错会被读者识破的部分）\n"
    "2. 常见的外行错误（小说中容易写错的细节）\n"
    "3. 行话/术语表（内行的说法）\n"
    "4. 2-3 个可直接用于场景描写的具体细节\n"
    "要求：只输出整理后的知识本体（Markdown，小节分明），"
    "不要寒暄、不要免责声明、禁止使用 emoji。"
)

_SYSTEM = (
    "你是专业领域研究助手，为小说创作编译准确、可执行的领域知识。"
    "优先使用检索到的真实资料；不确定的内容明确标注「存疑」。"
)


def _slug(domain: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "_", domain.strip())[:24]
    return s or uuid.uuid4().hex[:8]


async def research_domain(
    db_path: str, project_id: str, domain: str, *, context: str = "",
) -> dict[str, Any]:
    """Run one research call and store the result as a knowledge skill.

    Returns the stored skill payload plus ``used_web_search``.
    """
    domain = (domain or "").strip()
    if not domain:
        raise ValueError("domain is required")

    context_part = (
        f"作者正在写的相关内容（供聚焦）：{context.strip()[:600]}\n\n"
        if context.strip() else ""
    )
    prompt = _RESEARCH_PROMPT.format(domain=domain, context_part=context_part)

    from llm.router import ModelRouter
    router = ModelRouter()
    used_web = True
    try:
        body = await router.invoke_with_web_search(
            role="evaluator", prompt=f"{_SYSTEM}\n\n{prompt}",
            max_tokens=3000, temperature=0.3,
        )
    except NotImplementedError:
        # 当前模型不支持联网搜索 — degrade to model knowledge with an
        # extra-prominent caveat in the stored body.
        used_web = False
        from llm.call_site import LLMCallSite
        cs = LLMCallSite(
            call_site_id="knowledge.research",
            primary_role="evaluator",
            parsed_target_table="skill_index",
            default_max_tokens=3000, default_temperature=0.3,
        )
        body = await cs.invoke(
            prompt=prompt, system=_SYSTEM,
            project_id=project_id, db_path=db_path,
        )
    body = (body or "").strip()
    if not body:
        raise RuntimeError("research returned empty content")

    # 机制3: 标注 AI编译 / 非权威 (web 不可用时额外注明来源局限)。
    source_note = (
        "来源：联网搜索编译" if used_web
        else "来源：模型内置知识（当前模型不支持联网搜索，准确性更需留意）"
    )
    labeled = (
        f"{NON_AUTHORITATIVE_LABEL} 本知识由 AI 编译，未经权威校验。\n"
        f"{source_note}\n\n{body}"
    )

    skill_id = f"knowledge_{_slug(domain)}"
    from ui.backend.app.services import skill_index
    stored = skill_index.upsert_skill(db_path, {
        "skill_id":     skill_id,
        "display_name": f"专业知识：{domain}",
        "description":  f"{domain} 领域写作参考知识 {NON_AUTHORITATIVE_LABEL}",
        "kind":         "knowledge",
        "body_snippet": labeled,
    })
    return {**stored, "used_web_search": used_web}


def list_knowledge_skills(db_path: str) -> list[dict]:
    from ui.backend.app.services import skill_index
    return [
        s for s in skill_index.list_skills(db_path)
        if s.get("kind") == "knowledge"
    ]


def update_knowledge_skill(
    db_path: str, skill_id: str, *,
    display_name: str | None = None, body: str | None = None,
) -> dict:
    """机制2: 用户可查看、修改所学知识 — body edits keep the
    non-authoritative label so provenance can't silently disappear."""
    from ui.backend.app.services import skill_index
    existing = skill_index.get_skill(db_path, skill_id)
    if existing is None or existing.get("kind") != "knowledge":
        raise ValueError("knowledge skill not found")
    new_body = existing["body_snippet"]
    if body is not None:
        new_body = body.strip()
        if NON_AUTHORITATIVE_LABEL not in new_body:
            new_body = (
                f"{NON_AUTHORITATIVE_LABEL} 本知识由 AI 编译，"
                f"未经权威校验。\n\n{new_body}"
            )
    return skill_index.upsert_skill(db_path, {
        **existing,
        "display_name": (display_name or existing["display_name"]).strip(),
        "body_snippet": new_body,
        "kind": "knowledge",
    })


def delete_knowledge_skill(db_path: str, skill_id: str) -> None:
    from ui.backend.app.services import skill_index
    existing = skill_index.get_skill(db_path, skill_id)
    if existing is None or existing.get("kind") != "knowledge":
        raise ValueError("knowledge skill not found")
    skill_index.delete_skill(db_path, skill_id)
