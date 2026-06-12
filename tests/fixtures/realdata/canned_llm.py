"""Schema-legal canned LLM responses for Layer A sandbox tests.

Why this exists: ``WN_TEST_MODE`` already mocks the model layer, but
its default mock returns ``{"result": "mocked"}`` — which is
schema-illegal for every downstream parser and forces the pipeline
into a fallback path. To actually test the *happy path* (the one the
production pipeline normally runs), we need responses that pass each
agent's JSON schema.

This module patches at two layers:

1. ``LLMCallSite._invoke_auto`` — every audit-tracked call site goes
   through here. We dispatch on ``call_site_id``.
2. ``BaseAgent.invoke`` (and ``invoke_with_web_search``) — agents that
   don't go through LLMCallSite. We dispatch on agent name + system
   prompt heuristics.

Use as a context manager::

    with install_canned_llm(seed_info):
        # whole pipeline runs, every LLM call returns a canned, schema-
        # legal response that advances state by one chapter.
"""
from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Iterator
from unittest import mock


# ─────────── per-chapter canned content ───────────


_CHAPTER_BODY = {
    1: (
        "凌晨三点四十二分，林越被一声短促的警报拉出休眠舱。\n\n"
        "他抓住扶手让自己稳住，目光直接落到舱壁上的红色指示灯上。"
        "主控显示器一片密密的数字——节点二与补给舱段连接处的应力读数"
        "正在以一种他没见过的曲线攀升。\n\n"
        "“老林。”邓星的声音从通讯里压下来，"
        "“你过来一趟。”\n\n"
        "他没说话，只是用脚一蹬，沿着舱内绳索一路向主控滑过去。微重力"
        "里他的呼吸放得很轻——这是十一年老站，他知道什么动作会让结构"
        "回声。\n\n"
        "二十分钟后，他看着那条扩展速度并不正常的裂痕曲线，第一次开口："
        "“这条线不该长成这样。我再算一次。”"
        + "（更多正文……）" * 40
    ),
    2: (
        "通信舱比主控冷一些。林越把自己卡在角落里，手边漂着两块平板。"
        "他把上周所有标了“未识别窄带”的事件时间戳列出来，"
        "再叠上节点二的应力峰值。\n\n"
        "两条线几乎咬合。\n\n"
        "“不可能是巧合。”他对通信主管说，“调原始 SDR。”"
        + "（更多正文……）" * 40
    ),
    3: (
        "指令舱的门关上之后，邓星先开了口。“十二分钟，老林。说重点。"
        "”\n\n林越没绕弯：“要么上报，要么我们自己扛。”"
        + "（更多正文……）" * 40
    ),
    4: (
        "EMU 套头压在他后颈上的那一刻，林越听见自己的呼吸放在头盔里回响。"
        "节点二外侧的太阳直射让金属反光刺眼，他把面罩遮光层拨到第二档。"
        + "（更多正文……）" * 40
    ),
    5: (
        "会议舱的白板上写满了 Δv 数字。林越用记号笔在角落画出 Hohmann"
        "转移的两段椭圆弧，又把当前轨位与补给船预定轨位的相位差圈出来。"
        + "（更多正文……）" * 40
    ),
}


_CHAPTER_SCENE_PLAN = {
    1: {
        "scenes": [{
            "scene_index": 0,
            "location": "启明号·主控舱",
            "time": "凌晨 03:42",
            "characters": ["林越", "邓星"],
            "summary": "林越被警报叫醒，赶到主控舱排查异常应力读数。",
            "beats": ["警报响起", "林越赶到主控", "初步排查裂痕"],
            "character_instructions": {
                "林越": {
                    "emotional_state": "警觉而克制",
                    "must": ["用数据说话"],
                    "must_not": ["夸张抒情"],
                },
                "邓星": {
                    "emotional_state": "冷静下达指令",
                    "must": ["简短", "聚焦决策"],
                },
            },
            "narrator_instructions": "强调微重力日常 + 老化空间站的金属回声。",
        }],
        "chapter_arc": "从警报到初步识别异常。",
    },
}


def _scene_plan_for(chapter_num: int) -> dict[str, Any]:
    plan = _CHAPTER_SCENE_PLAN.get(chapter_num) or _CHAPTER_SCENE_PLAN[1]
    return dict(plan)


# ─────────── canned JSON payloads ───────────


_DEFAULT_EVALUATOR = {
    "passed": True, "score": 0.82,
    "issues": [], "strengths": ["技术细节真实", "节奏紧凑"],
}


_DEFAULT_SUMMARY = (
    "林越发现启明号主结构出现非典型疲劳裂痕；邓星接到报告，开始权衡"
    "上报与不上报的代价。"
)


_DEFAULT_EVENTS = {
    "events": [{
        "event_type": "discovery",
        "summary": "结构裂痕异常被识别",
        "actors": ["林越"],
        "location": "启明号·主控舱",
    }],
}


def _default_state_delta(chapter_num: int) -> dict[str, Any]:
    """Schema-legal state-delta JSON. Each chapter advances the same
    林越 / 邓星 / 启明号 actors with slightly different SPO triples
    so successive chapters compound rather than overwrite."""
    return {
        "truth_current_state": {
            "facts": [
                {"subject": "林越", "predicate": "位于",
                 "object": "启明号·主控舱"
                            if chapter_num <= 2
                            else "启明号·节点二"
                            if chapter_num == 4
                            else "启明号·会议舱"},
                {"subject": "启明号", "predicate": "处于",
                 "object": f"结构警报{chapter_num}级"},
            ],
        },
        "character_ledger": {
            "林越": {"emotional_state":
                    ["警觉", "专注", "压力下", "紧张", "冷静"][chapter_num - 1],
                    "knowledge_acquired":
                    [f"知晓ch{chapter_num}的新事实"]},
            "邓星": {"emotional_state": "果断"},
        },
        "pending_hooks": (
            [{"hook_id": f"h{chapter_num}",
              "description": "曲率残响来源",
              "introduced_chapter": chapter_num}]
            if chapter_num <= 3 else []
        ),
        "emotion_arc": {
            "林越": {"valence": -0.2 - 0.1 * chapter_num,
                    "arousal": 0.3 + 0.1 * chapter_num},
        },
    }


_DEFAULT_EDIT_BATCH = {
    "style_preferences": [
        {"description": "偏好短句、名词为主、不抒情",
         "source": "edit"},
        {"description": "强调航天技术真实感，包括传感器读数与现实流程",
         "source": "special_req"},
    ],
    "content_preferences": [
        {"description": "聚焦舱段结构与轨道环境细节",
         "source": "special_req"},
    ],
    "pacing_preferences": [
        {"description": "短小章节内多次切换视角推进",
         "source": "edit"},
    ],
    "domain_gap": {
        "needs_domain_knowledge": True,
        "candidate_domains": [{
            "domain": "航天动力学",
            "sub_domain": "轨道力学/EVA",
            "rationale": "作者在 5 章里反复要求航天技术真实感（传感器、"
                         "EVA 流程、轨道机动 Δv 预算），需要补充该领域"
                         "知识以提升专业质感。",
        }],
    },
}


_DEFAULT_WEB_COMPILED = (
    "> 本文为 AI 基于联网搜索综合编译的领域知识，**非权威来源**。\n\n"
    "## 核心概念\n"
    "近地轨道（LEO）航天器姿态与轨道控制依赖 Δv 预算管理；任何"
    "相位调整都需考虑摄动与机动窗口。EVA（舱外活动）依赖 EMU 套装"
    "+ PLSS（便携式生命维持系统）。\n\n"
    "## 常见误区\n"
    "- 误以为空间站\"漂浮\"在轨道上；实际上它在自由落体。\n"
    "- 误以为太空是绝对寂静的——舱内噪声其实很大。\n"
    "- 误以为 EVA 可以\"出去走两步\"；事前 prebreath + checklist 长达数小时。\n\n"
    "## 关键术语 (中英对照)\n"
    "- Δv (delta-v) — 速度增量预算\n"
    "- Hohmann transfer — 霍曼转移轨道\n"
    "- PLSS — 便携式生命维持系统\n"
    "- CMG — 控制力矩陀螺\n\n"
    "## 写作可用细节\n"
    "- 工具用束缚带系在大腿；松手不等于掉落，但等于丢失。\n"
    "- 头盔内有声学回响，长 EVA 中可以听见自己呼吸的间隔。\n"
    "- CO2 清除单元告警是 EVA 中最严重的中止级告警之一。\n\n"
    "## 参考来源\n"
    "- NASA Spaceflight ISS Lessons Learned (访问于 2025)\n"
    "- Curtis, Orbital Mechanics for Engineering Students\n"
    "- ESA EVA Handbook (公开摘要)\n"
)


# ─────────── dispatch ───────────


_CHAPTER_NUM_RE = re.compile(r"chapter[_\s]?num[\"']?\s*[:=]\s*(\d+)")


def _detect_chapter_num(prompt: str) -> int:
    m = _CHAPTER_NUM_RE.search(prompt)
    if m:
        try:
            n = int(m.group(1))
            if 1 <= n <= 5:
                return n
        except ValueError:
            pass
    # Heuristic: look for synopsis keywords from each chapter.
    if "Δv" in prompt or "霍曼" in prompt or "交会" in prompt:
        return 5
    if "EMU" in prompt or "舱外" in prompt or "EVA" in prompt:
        return 4
    if "邓星" in prompt and "上报" in prompt:
        return 3
    if "残响" in prompt or "SDR" in prompt:
        return 2
    return 1


def _canned_for_call_site(call_site_id: str, prompt: str) -> str | None:
    """Dispatch by call_site_id. Returns the canned JSON/text string,
    or None for sites we don't override (those go to the default mock)."""
    if call_site_id == "post_commit.summarizer":
        return _DEFAULT_SUMMARY
    if call_site_id == "post_commit.event_extractor":
        return json.dumps(_DEFAULT_EVENTS, ensure_ascii=False)
    if call_site_id == "storyland_state.settlement":
        ch = _detect_chapter_num(prompt)
        return json.dumps(_default_state_delta(ch), ensure_ascii=False)
    if call_site_id == "edit_learning.batch_extract":
        return json.dumps(_DEFAULT_EDIT_BATCH, ensure_ascii=False)
    if call_site_id == "post_commit.validator_layer3":
        return json.dumps({"results": []}, ensure_ascii=False)
    if call_site_id == "snapshot_auto_detector":
        return json.dumps({"is_snapshot": False}, ensure_ascii=False)
    return None


def _canned_for_agent(system: str, prompt: str, role: str = "") -> str:
    """Dispatch for plain BaseAgent / router calls that don't go
    through LLMCallSite. Pattern-match on system prompt + role."""
    s = (system or "") + "\n" + (role or "")
    if "scene" in s.lower() or "场景" in s:
        ch = _detect_chapter_num(prompt)
        return json.dumps(_scene_plan_for(ch), ensure_ascii=False)
    if "evaluat" in s.lower() or "评估" in s:
        return json.dumps(_DEFAULT_EVALUATOR, ensure_ascii=False)
    if "actor" in s.lower() or "角色表演" in s or "narrator" in s.lower():
        ch = _detect_chapter_num(prompt)
        return f"[旁白]\n{_CHAPTER_BODY[ch][:400]}"
    if "writer" in s.lower() or "assemble" in s.lower() or "编辑" in s:
        ch = _detect_chapter_num(prompt)
        return _CHAPTER_BODY[ch]
    # Default: a generic "ok" wrapped as JSON so json-parsing callers
    # don't choke.
    return '{"result": "ok"}'


# ─────────── patcher ───────────


@contextlib.contextmanager
def install_canned_llm() -> Iterator[dict[str, list]]:
    """Patch the LLM execution layers with the canned dispatcher.

    Yields a ``calls`` dict the test can inspect: ``calls['call_sites']``
    is a list of ``(call_site_id, prompt_preview)`` tuples, etc."""
    calls: dict[str, list] = {"call_sites": [], "agents": [], "web": []}

    async def _patched_invoke_auto(self, prompt, system, *, max_tokens, temperature):
        canned = _canned_for_call_site(self.call_site_id, prompt or "")
        if canned is None:
            canned = _canned_for_agent(system or "", prompt or "",
                                        self.primary_role)
        calls["call_sites"].append((self.call_site_id, (prompt or "")[:120]))
        return canned

    async def _patched_base_agent_invoke(self, user_content, *args, **kwargs):
        """Mimic BaseAgent.invoke return shape (dict with .content/.raw/etc)."""
        system = kwargs.get("system", "")
        role = getattr(self, "agent_name", "")
        canned = _canned_for_agent(system, user_content or "", role)
        calls["agents"].append((role, (user_content or "")[:120]))
        try:
            parsed = json.loads(canned)
        except Exception:
            parsed = None

        class _Resp:
            content = canned
            input_tokens = 100
            output_tokens = 50
            raw = {"parsed": parsed} if parsed is not None else {}
        return _Resp()

    async def _patched_invoke_with_web_search(
        self, *, role, prompt, max_tokens=4096, temperature=0.3,
    ):
        calls["web"].append((role, (prompt or "")[:120]))
        return _DEFAULT_WEB_COMPILED

    # Patch compile_via_web_search directly too — Part B's knowledge
    # acquisition goes through reference_pipeline-style web search
    # which lives outside ModelRouter for some configurations.
    async def _patched_compile_web(
        *, domain, sub_domain="", rationale="", router_inst=None,
    ):
        calls["web"].append((domain, sub_domain))
        return _DEFAULT_WEB_COMPILED

    patches = [
        mock.patch("llm.call_site.LLMCallSite._invoke_auto",
                   new=_patched_invoke_auto),
        mock.patch("agents.base_agent.BaseAgent.invoke",
                   new=_patched_base_agent_invoke),
        mock.patch("llm.router.ModelRouter.invoke_with_web_search",
                   new=_patched_invoke_with_web_search),
        mock.patch(
            "agents.learning.knowledge_acquisition.compile_via_web_search",
            new=_patched_compile_web,
        ),
    ]
    for p in patches:
        p.start()
    try:
        yield calls
    finally:
        for p in patches:
            p.stop()
