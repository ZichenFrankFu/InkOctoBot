"""Real-shape seed project: "轨道挽歌" (5-chapter hard sci-fi micro-arc).

Why this shape: every chapter's ``special_requirement`` progressively
fixates on aerospace technical realism. By chapter 5, batch-extracted
preferences should flag a domain gap (航天动力学 / 轨道力学 / EVA)
because the user is consistently demanding the same kind of detail.
This gives Part 6 (self-learning) a concrete, falsifiable trigger to
test against.

Returned shape::

    {
        "project_id":      "rt_proj",
        "chapter_ids":     ["rt_ch1", ..., "rt_ch5"],
        "character_names": ["林越", "邓星"],
    }
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any


PROJECT_ID = "rt_proj"
CHARACTER_NAMES = ["林越", "邓星"]
CHAPTER_IDS = [f"rt_ch{n}" for n in range(1, 6)]


_CHAPTERS = [
    {
        "chapter_id": "rt_ch1",
        "chapter_num": 1,
        "title": "结构警报",
        "synopsis": (
            "启明号空间站凌晨突发结构警报。结构工程师林越被叫到主控舱，"
            "通过应力传感器数据排查异常，初步怀疑是补给舱段连接处的疲劳裂痕，"
            "但裂痕扩展速度异常，与已有的轨道力学模型不符。"
        ),
        "location": "启明号·主控舱",
        "time_label": "近未来 · 凌晨 03:42",
        "special_requirements": (
            "强调舱段结构与轨道环境的技术真实感：传感器读数、应力描述、"
            "微重力下的人物动作要符合空间站日常。"
        ),
    },
    {
        "chapter_id": "rt_ch2",
        "chapter_num": 2,
        "title": "残响同步",
        "synopsis": (
            "林越复查裂痕扩展记录时，发现其时间戳与上周一系列未识别的窄带"
            "信号（船员私下称之为\"曲率残响\"）严格同步。他向通信主管申请"
            "调取原始 SDR 频谱，准备做联合相干分析。"
        ),
        "location": "启明号·通信舱",
        "time_label": "次日 · 14:20",
        "special_requirements": (
            "强调信号探测与通信链路的技术细节：采样率、相干积分、深空"
            "测控网命名约定。"
        ),
    },
    {
        "chapter_id": "rt_ch3",
        "chapter_num": 3,
        "title": "压力曲线",
        "synopsis": (
            "指令长邓星权衡是否将异常上报地面任务控制中心。上报意味着任务"
            "降级、补给船改派、整个轨道窗口重排；不上报则承担结构失效风险。"
            "她与林越在指令舱开了一场十二分钟的私下会议。"
        ),
        "location": "启明号·指令舱",
        "time_label": "第三日 · 08:00",
        "special_requirements": (
            "强调指令决策的压力与权衡：任务降级流程、补给链断点、KSP 式"
            "现实主义而非太空歌剧。"
        ),
    },
    {
        "chapter_id": "rt_ch4",
        "chapter_num": 4,
        "title": "舱外",
        "synopsis": (
            "邓星批准了一次预防性 EVA。林越穿上 EMU 抵达裂痕位置，准备贴附"
            "应力贴片。中途生命维持系统出现 CO2 清除单元的告警，他必须在"
            "完成任务和保命之间做出实时判断。"
        ),
        "location": "启明号·舱外（节点二）",
        "time_label": "第四日 · 11:30",
        "special_requirements": (
            "强调 EVA 真实流程与风险：检查清单、束缚带、PLSS 告警、"
            "回舱协议。"
        ),
    },
    {
        "chapter_id": "rt_ch5",
        "chapter_num": 5,
        "title": "轨道机动",
        "synopsis": (
            "补给船因发射场天气延迟 36 小时。林越提出用启明号自身的轨道"
            "维持推力做一次小幅相位调整，与补给船在新轨位交会。邓星召集"
            "全员推演 Δv 预算与交会窗口。"
        ),
        "location": "启明号·会议舱",
        "time_label": "第五日 · 19:00",
        "special_requirements": (
            "强调轨道力学与交会对接的真实感：相位角、Hohmann 转移、Δv"
            "预算、近地轨道摄动。"
        ),
    },
]


_CHARACTERS = [
    {
        "name": "林越",
        "role": "结构工程师",
        "description": "启明号空间站随船结构工程师，34 岁，背景：清华航院。",
        "personality": "理性细致，习惯用数据说话；不擅长与地面外交；高压下沉默寡言。",
        "background": "本科结构力学，硕士轨道工程；曾在 CMG 标定团队工作过两年。",
        "appearance": "中等身材，左手腕有一道旧伤；微重力下习惯把工具固定在大腿带上。",
        "speech_style": "短句，多名词，少形容词；遇到不确定先说\"我再算一次。\"",
        "layer_a": {
            "appearance": "中等身材，左手腕旧伤",
            "personality": "理性细致，沉默",
            "background": "结构 + 轨道工程双背景",
            "speech_style": "短句多名词",
        },
        "layer_b": {
            "rational": 78, "ambitious": 45, "social": 30, "courage": 65,
        },
    },
    {
        "name": "邓星",
        "role": "指令长",
        "description": "启明号空间站第七任指令长，41 岁，前空军试飞员。",
        "personality": "果断务实，承受地面与船员双重压力；不擅长安慰人但绝不会丢下人。",
        "background": "前歼-20 试飞员，转入空间站指挥岗位已六年；持有两次危机处置嘉奖。",
        "appearance": "短发，颧骨高，左袖始终别着一支老式机械笔。",
        "speech_style": "命令式短句，结尾常用\"清楚吗？\"；私下会用\"老林\"称呼林越。",
        "layer_a": {
            "appearance": "短发颧骨高",
            "personality": "果断务实",
            "background": "前试飞员",
            "speech_style": "命令式短句",
        },
        "layer_b": {
            "rational": 70, "ambitious": 60, "social": 55, "courage": 85,
        },
    },
]


_WORLDBOOK = [
    {
        "title": "启明号空间站",
        "category": "技术",
        "content": (
            "近地轨道（约 415 km），六舱段：主控、通信、生活、生命维持、"
            "节点一、节点二。主结构服役 11 年，已老化；靠每 90 天一次的"
            "无人货运补给维持。设计寿命再剩 4 年。"
        ),
    },
    {
        "title": "曲率残响",
        "category": "技术",
        "content": (
            "船员对一组窄带未识别信号的私下称呼。频率漂移 ±15 Hz，时长 800 ms"
            "左右，常在主结构应力波动事件前 2-5 分钟出现。来源未知；地面"
            "怀疑是 RFI（射频干扰）而非外源。"
        ),
    },
]


_SUBPLOT_MAIN = {
    "name": "结构危机",
    "description": (
        "启明号主结构出现非典型疲劳裂痕，裂痕扩展速度与所谓\"曲率残响\""
        "信号高度同步。林越须在下一次补给抵达前查清原因；邓星须在不让"
        "地面接管的前提下守住空间站。"
    ),
    "kind": "main",
}


# ─────────── insert helpers ───────────


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _insert_project(con: sqlite3.Connection) -> None:
    con.execute(
        """INSERT OR REPLACE INTO projects
           (project_id, title, genre, logline)
           VALUES (?, ?, ?, ?)""",
        (
            PROJECT_ID, "轨道挽歌", "科幻",
            "近未来近地轨道科幻：启明号空间站的一次结构危机。",
        ),
    )


def _stable_id(prefix: str, *parts: str) -> str:
    """Deterministic ID so re-running the seed updates existing rows
    rather than inserting a new uuid every time (which would silently
    accumulate duplicates because INSERT OR REPLACE only resolves
    PRIMARY KEY / UNIQUE collisions)."""
    import hashlib
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _insert_characters(con: sqlite3.Connection) -> None:
    for i, ch in enumerate(_CHARACTERS):
        con.execute(
            """INSERT OR REPLACE INTO characters
               (character_id, project_id, name, role, description,
                personality, background, appearance, speech_style,
                layer_a_json, layer_b_json, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _stable_id("char", PROJECT_ID, ch["name"]), PROJECT_ID,
                ch["name"], ch["role"], ch["description"],
                ch["personality"], ch["background"], ch["appearance"],
                ch["speech_style"],
                json.dumps(ch["layer_a"], ensure_ascii=False),
                json.dumps(ch["layer_b"], ensure_ascii=False),
                i,
            ),
        )


def _insert_worldbook(con: sqlite3.Connection) -> None:
    for i, w in enumerate(_WORLDBOOK):
        con.execute(
            """INSERT OR REPLACE INTO worldbook_entries
               (entry_id, project_id, title, category, content, sort_order)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                _stable_id("wb", PROJECT_ID, w["title"]), PROJECT_ID,
                w["title"], w["category"], w["content"], i,
            ),
        )


def _insert_chapters(db_path: str) -> None:
    """Write chapters via ``save_editor_doc`` so BOTH the editor blob
    (``project_blobs(scope='editor')``, source of truth for the UI
    chapter tree) AND the denormalized ``chapters`` rows (read by
    the RAG/generation loaders) are populated atomically.

    A direct INSERT into ``chapters`` is not enough: the UI editor
    page reads from ``GET /api/data/editor?project_id=...`` which
    queries ``project_blobs``. With an empty blob the chapter tree
    is empty, and several React components downstream of it call
    ``.length`` on undefined nested arrays and crash."""
    from ui.backend.app.services import project_store

    editor_doc = {
        "volumes": [{
            # Frontend tree components key off various id fields
            # depending on which screen — provide all the common ones.
            "id":          f"{PROJECT_ID}_v1",
            "volume_id":   f"{PROJECT_ID}_v1",
            "order":       0,
            "title":       "第一卷",
            "chapters": [
                {
                    # The two keys ARE intentional. ``chapter_id`` is the
                    # column save_editor_doc writes into the ``chapters``
                    # table. ``id`` is what ``chapter_fields.py`` (and the
                    # React frontend) look up when reading the editor doc
                    # JSON. The two paths use different keys; missing
                    # either silently breaks the prompt-context loaders
                    # (worldbook/storyland/special_req all key off the
                    # synopsis that's only reachable via the ``id`` key).
                    "id":           ch["chapter_id"],
                    "chapter_id":   ch["chapter_id"],
                    "order":        ch["chapter_num"] - 1,
                    "title":        ch["title"],
                    "synopsis":     ch["synopsis"],
                    "outline":      ch["synopsis"],
                    "content":      "",
                    "word_count":   0,
                    "time":         ch["time_label"],
                    "location":     ch["location"],
                    "characters":   list(CHARACTER_NAMES),
                    "pov_character": "林越",
                    "special_requirements": ch["special_requirements"],
                    # Defensive defaults — the React editor touches
                    # these arrays without nullish guards in several
                    # places (referenced_events.length, etc.).
                    "referenced_events":       [],
                    "referenced_inspirations": [],
                    "scenes":                  [],
                    "scene_plans":             [],
                    "beats":                   [],
                    "tags":                    [],
                    "skills":                  [],
                    "rag_excludes":            [],
                    "on_stage_entities": {
                        "characters":    list(CHARACTER_NAMES),
                        "locations":     [],
                        "items":         [],
                        "organizations": [],
                    },
                }
                for ch in _CHAPTERS
            ],
        }],
    }
    project_store.save_editor_doc(db_path, PROJECT_ID, editor_doc)


def _insert_subplots(con: sqlite3.Connection) -> None:
    """Subplots may live in different tables across versions; do a
    best-effort insert and swallow OperationalError if the table
    isn't present in this schema."""
    for ddl_name, payload in (
        ("subplot_threads", _SUBPLOT_MAIN),
    ):
        try:
            con.execute(
                f"""INSERT OR REPLACE INTO {ddl_name}
                    (thread_id, project_id, name, description,
                     start_chapter)
                    VALUES (?, ?, ?, ?, ?)""",
                (_stable_id("sp", PROJECT_ID, payload["name"]),
                 PROJECT_ID, payload["name"],
                 payload["description"], 1),
            )
        except sqlite3.Error:
            # Table absent or schema-mismatched — subplots are
            # supplemental seed data; skip silently.
            pass


# ─────────── public entry ───────────


def seed_project(db_path: str) -> dict[str, Any]:
    """Insert the full "轨道挽歌" seed into ``db_path``.

    The DB is expected to already have the v2 schema applied
    (``ensure_creation_tables``). Returns a dict the callers can
    feed back into other fixtures."""
    with sqlite3.connect(db_path) as con:
        _insert_project(con)
        _insert_characters(con)
        _insert_worldbook(con)
        _insert_subplots(con)
        con.commit()
    # Chapters must go through save_editor_doc so the project_blobs
    # editor blob the UI reads is populated alongside the chapters
    # table — opens its own connection internally.
    _insert_chapters(db_path)
    return {
        "project_id":      PROJECT_ID,
        "chapter_ids":     list(CHAPTER_IDS),
        "character_names": list(CHARACTER_NAMES),
    }
