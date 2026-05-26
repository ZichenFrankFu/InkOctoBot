"""
Seed a test data directory with sample data for the test-only app mode.

Usage:
    python test_seed.py [target_dir]

If target_dir is omitted, seeds into data_test/ under the repo root.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path


def _nid() -> str:
    return f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def seed(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)

    # ── Projects ──
    projects_dir = target / "projects"
    projects_dir.mkdir(exist_ok=True)
    pid = "test_project_001"
    _write(projects_dir / f"{pid}.json", {
        "id": pid,
        "name": "测试项目：星辰大海",
        "description": "一个用于测试的示例项目",
        "created_at": time.time(),
        "updated_at": time.time(),
    })

    # ── Characters ──
    chars_dir = target / "characters"
    chars_dir.mkdir(exist_ok=True)
    char_id = _nid()
    _write(chars_dir / f"{char_id}.json", {
        "id": char_id,
        "project_id": pid,
        "name": "李星河",
        "role": "主角",
        "description": "二十五岁，星际考古学研究生。性格沉稳但内心热血。拥有感知古代遗物情感残留的特殊能力。",
        "personality": "沉稳、好奇心旺盛、重情义",
        "background": "出身普通家庭，凭奖学金进入银河联邦大学。童年一次意外中获得了感知能力。",
        "appearance": "黑发，深邃的眼睛，中等身材，常穿旧款考古学院夹克",
        "tags": ["主角", "考古学", "超能力"],
        "created_at": time.time(),
    })
    char_id2 = _nid()
    _write(chars_dir / f"{char_id2}.json", {
        "id": char_id2,
        "project_id": pid,
        "name": "苏晚",
        "role": "女主角",
        "description": "联邦安全局特工，精通格斗和黑客技术。外表冷酷，实际上有着柔软的内心。",
        "personality": "冷静、果断、外冷内热",
        "background": "特工世家出身，从小接受精英训练。在一次任务中失去了搭档，心中留下阴影。",
        "appearance": "银色短发，灰蓝色眼睛，身材高挑，常穿黑色战术服",
        "speech_style": "言简意赅，多用陈述句，几乎不用感叹号",
        "tags": ["女主角", "特工", "格斗"],
        "relationships": [
            {
                "target_id": char_id,
                "target_name": "李星河",
                "affinity": 30,
                "priority": 3,
                "chapter": "第3章",
                "notes": "被迫合作的搭档，逐渐建立信任",
            },
        ],
        "created_at": time.time(),
    })
    # ── 3rd character (反派) — gives the RAG sample a clear antagonist
    # so prompt-context tests can exercise the knowledge-isolation filter
    # ("李星河不知道格里芬是黑石商会的内线").
    char_id3 = _nid()
    _write(chars_dir / f"{char_id3}.json", {
        "id": char_id3,
        "project_id": pid,
        "name": "格里芬",
        "role": "反派",
        "description": "黑石商会的高级顾问，表面身份是星际历史学家。真实身份是远古文明残党，活了三百年。",
        "personality": "儒雅、深谋远虑、为达目的不择手段",
        "background": "三百年前最后一批接触过星门的古代守门人之一。亲眼目睹古代文明因星门误用而毁灭。",
        "appearance": "中年男性外貌，银白短发，金丝眼镜，常穿剪裁考究的西装",
        "speech_style": "用词典雅，引用古代谚语，从不直接拒绝任何人",
        "tags": ["反派", "黑石商会", "远古文明"],
        "relationships": [
            {
                "target_id": char_id,
                "target_name": "李星河",
                "affinity": -10,
                "priority": 2,
                "chapter": "第3章",
                "notes": "暗中观察李星河的能力，伺机利用",
            },
        ],
        "created_at": time.time(),
    })

    # Update first character with relationship to second
    import copy
    char1_data = json.loads((chars_dir / f"{char_id}.json").read_text("utf-8"))
    char1_data["relationships"] = [
        {
            "target_id": char_id2,
            "target_name": "苏晚",
            "affinity": 25,
            "priority": 2,
            "chapter": "第3章",
            "notes": "联邦特工，让人捉摸不透",
        },
    ]
    _write(chars_dir / f"{char_id}.json", char1_data)

    # ── Editor (volumes + chapters) ──
    editor_dir = target / "editor"
    editor_dir.mkdir(exist_ok=True)
    # Fixed chapter IDs so debug endpoints / curl commands can address
    # them by stable name across re-seeds. Same convention for volume.
    vol_id = "vol_test_001"
    ch1_id = "ch_test_001"
    ch2_id = "ch_test_002"
    ch3_id = "ch_test_003"

    # ── Chapter content ── 3 chapters, all with real text so RAG /
    # memory / 续接上下文 have something to operate on. Each is short
    # (~600 字) to keep token budgets low for testing.
    ch1_text = (
        "矿星 K-7 的地表覆盖着一层细密的灰色沙尘，在微弱的恒星光下泛着暗淡的金属光泽。"
        "李星河蹲在一处裸露的岩层前，手持便携式光谱分析仪对着岩石表面缓慢扫描。\n\n"
        "「读数偏差超出 12%。」他对着耳机里的同事低声说道，「这一带不像是天然矿脉。」\n\n"
        "回应他的只有沙沙的静电——通讯卫星上一次过顶已经是六小时前。在银河边缘的废弃矿区，"
        "这是常态。\n\n"
        "他伸手拨开覆盖在岩层裂缝上的沙尘，露出一块拳头大小的青色晶体。当指尖触到晶体表面的瞬间，"
        "李星河的脑海里突然涌入大量陌生的影像：宏伟的银白色拱门、漂浮在虚空中的几何符号、"
        "以及一段陌生的语言低声重复着同一个坐标——「天蝎座 ζ-79，第三行星」。\n\n"
        "他猛地缩回手，呼吸急促。这不是普通的考古发现。\n\n"
        "「这是一段……记忆。」李星河小声说，「来自一个早已灭亡的文明。」"
    )
    ch2_text = (
        "三天后，李星河刚把水晶样本封装好准备运回学院，矿星上空就出现了三艘没有标识的灰色飞船。\n\n"
        "「这里是联邦安全局，请保持原地不动。」一个冷静的女声从扩音器里传出。\n\n"
        "舱门打开，一位银发女子率先跳下，黑色战术服紧贴她精瘦的身躯。"
        "她从腰间抽出 ID 卡，简短地亮了一下：\n\n"
        "「苏晚，少校。我们收到了来自这颗星球的远古信号。」她目光落在李星河手中的密封盒，"
        "「你发现了什么？」\n\n"
        "李星河犹豫了一瞬。按照学院的规定，所有考古发现必须先经过学术委员会审议；但眼前这位"
        "少校的眼神告诉他，今天的事情不会按学院的规定走。\n\n"
        "「一块晶体。」他选择了不说全部，「可能是远古文明的存储介质。」\n\n"
        "苏晚没有立刻接话。她从口袋里摸出一支烟，但又放了回去——这是她在思考时的小动作。"
        "李星河注意到她的左手无名指上有一道浅浅的旧伤疤。"
    )
    ch3_text = (
        "「考古许可被联邦安全局征用了。」第二天清晨，苏晚把一份冰冷的电子文件推到李星河面前。\n\n"
        "「你接下来三个月归我管。报酬按少校副官标准发放。」\n\n"
        "李星河抬眼盯着她：「我可以拒绝吗？」\n\n"
        "「可以。」苏晚回得干脆，「然后你的研究经费会在 24 小时内被冻结。」\n\n"
        "李星河没再说话。他知道这种规模的征用文件，背后多半是更高层的人在推动。\n\n"
        "三天后，他们一起下到矿星地下三百米处的远古遗迹。在第一道防御机制启动时，"
        "苏晚反应迅捷地把李星河压到墙边，一道激光从两人之间不到十厘米的位置擦过。\n\n"
        "「你欠我一次。」苏晚低声说，没有看他。\n\n"
        "李星河深吸一口气：「……谢谢。」\n\n"
        "这是他们第一次没有用职位称呼对方。"
    )

    _write(editor_dir / f"{pid}.json", {
        "project_id": pid,
        "volumes": [
            {
                "id": vol_id,
                "project_id": pid,
                "title": "第一卷：遗迹之谜",
                "order": 0,
                "chapters": [
                    {
                        "id": ch1_id,
                        "volume_id": vol_id,
                        "title": "第一章：意外发现",
                        "order": 0,
                        "synopsis": "李星河在银河边缘的废弃矿星上进行例行考古调查时，触碰到一块异常的水晶，感知到了远古文明的最后记忆——一段关于「星门」位置的信息。",
                        "content": ch1_text,
                        "word_count": len(ch1_text),
                        "status": "done",
                        "time": "银河历2847年·秋",
                        "location": "废弃矿星K-7",
                        "characters": ["李星河"],
                    },
                    {
                        "id": ch2_id,
                        "volume_id": vol_id,
                        "title": "第二章：不速之客",
                        "order": 1,
                        "synopsis": "李星河正准备将发现上报学院时，苏晚带领特工小队突然出现在矿星上。她奉命调查最近频繁出现的远古信号源，而信号恰好来自李星河发现的水晶。",
                        "content": ch2_text,
                        "word_count": len(ch2_text),
                        "status": "done",
                        "time": "银河历2847年·秋",
                        "location": "废弃矿星K-7",
                        "characters": ["李星河", "苏晚"],
                    },
                    {
                        "id": ch3_id,
                        "volume_id": vol_id,
                        "title": "第三章：被迫同行",
                        "order": 2,
                        "synopsis": "联邦安全局征用了李星河的考古许可，强制他加入苏晚的调查小队担任顾问。两人性格冲突不断，但在共同面对矿星深处的古代防御机制时，开始建立信任。",
                        "content": ch3_text,
                        "word_count": len(ch3_text),
                        "status": "done",
                        "time": "银河历2847年·秋",
                        "location": "废弃矿星K-7·地下遗迹",
                        "characters": ["李星河", "苏晚"],
                    },
                ],
            },
        ],
        "saved_at": time.time(),
    })

    # Save chapter texts to the chapter_summaries / project DB so the
    # memory + RAG layers have real data to operate on (see
    # _seed_rag_calibration_data below for L2/L4/Truth seeding).
    _RAG_SEED_CHAPTERS = [
        (1, "第一章：意外发现", ch1_text),
        (2, "第二章：不速之客", ch2_text),
        (3, "第三章：被迫同行", ch3_text),
    ]

    # ── Worldbook ──
    wb_dir = target / "worldbook"
    wb_dir.mkdir(exist_ok=True)
    wb_id1 = _nid()
    _write(wb_dir / f"{wb_id1}.json", {
        "id": wb_id1,
        "project_id": pid,
        "title": "银河联邦",
        "category": "social_structure",
        "content": "人类在2500年建立的星际政府，管辖超过200个恒星系。联邦首都位于地球的轨道空间站「新日内瓦」。",
        "tags": ["设定", "政治"],
        "created_at": time.time(),
    })
    wb_id2 = _nid()
    _write(wb_dir / f"{wb_id2}.json", {
        "id": wb_id2,
        "project_id": pid,
        "title": "星门",
        "category": "hard_rules",
        "content": "远古文明留下的空间传送装置，可在瞬间连接两个遥远的星系。目前已知的星门均处于休眠状态，联邦科学家尚未成功激活任何一座。",
        "tags": ["设定", "科技", "核心"],
        "created_at": time.time(),
    })
    wb_id3 = _nid()
    _write(wb_dir / f"{wb_id3}.json", {
        "id": wb_id3,
        "project_id": pid,
        "title": "黑石商会",
        "category": "factions",
        "content": "横跨多个星系的商业巨头，表面经营星际能源贸易，暗中收购远古文明遗物。在联邦境内有数十家空壳子公司作掩护。会长身份至今不明，相传与远古文明有血脉关联。",
        "tags": ["设定", "组织", "反派"],
        "created_at": time.time(),
    })

    # ── Storyline ──
    sl_dir = target / "storylines"
    sl_dir.mkdir(exist_ok=True)
    _write(sl_dir / f"{pid}.json", {
        "project_id": pid,
        "nodes": [
            {"id": "n1", "title": "发现水晶", "chapter_num": 1, "summary": "李星河发现远古水晶", "x": 100, "y": 100, "time": "银河历2847年·秋", "location": "矿星K-7"},
            {"id": "n2", "title": "特工介入", "chapter_num": 2, "summary": "苏晚率队抵达", "x": 300, "y": 100, "time": "银河历2847年·秋", "location": "矿星K-7"},
            {"id": "n3", "title": "深入遗迹", "chapter_num": 3, "summary": "共同探索地下遗迹", "x": 500, "y": 100, "time": "银河历2847年·秋", "location": "矿星K-7·地下"},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "label": "引来关注"},
            {"id": "e2", "from": "n2", "to": "n3", "label": "被迫合作"},
        ],
        "saved_at": time.time(),
    })

    # ── Chat history (sample with AI proactive questions) ──
    ch_dir = target / "chat_history"
    ch_dir.mkdir(exist_ok=True)
    _write(ch_dir / f"{pid}_outline_chat.json", {
        "project_id": pid,
        "scope": "outline_chat",
        "messages": [
            {"role": "user", "content": "我想写一个星际考古的故事，主角能感知古代遗物的情感。", "ts": time.time() - 3600},
            {"role": "assistant", "content": "这个设定很有潜力！「感知遗物情感」可以作为推动剧情的核心能力。几个建议：\n\n1. 这种能力的代价是什么？（比如每次使用后会短暂失去自己的情感记忆）\n2. 远古文明灭亡的原因可以和这种能力有关\n3. 反派可以是想利用这种能力来操控古代武器的人\n\n你想先从哪个方向深入？", "ts": time.time() - 3500},
            {"role": "assistant", "content": "在审阅你目前的大纲和角色设定后，我发现几个值得深入思考的问题：\n\n**1. 关于主角能力的边界**\n李星河的感知能力目前没有明确的使用限制。如果不加以约束，可能会在后续情节中导致「万能钥匙」问题。\n- 你打算给这种能力设定什么样的代价或冷却机制？\n- 是否有某些类型的信息是他无法感知的？\n\n**2. 反派势力的动机**\n目前大纲中提到了「神秘组织」，但他们追求星门的具体目的还不明确。\n\n你想先讨论哪个问题？", "ts": time.time() - 3400, "proactive": True},
        ],
        "saved_at": time.time(),
    })

    # ── Settings (mock provider as default) ──
    _write(target / "settings.json", {
        "auto_save": True,
        "auto_save_interval": 30,
        "cost_confirm": False,
        "export_format": "txt",
        "providers": {
            "mock": {"enabled": True, "models": ["mock-test-v1"]},
            "ollama": {"enabled": False, "base_url": "http://localhost:11434", "models": []},
        },
        "pipeline": {
            "scene_planner": {"provider": "mock", "model": "mock-test-v1"},
            "scene_director": {"provider": "mock", "model": "mock-test-v1"},
            "actor_default": {"provider": "mock", "model": "mock-test-v1"},
            "actor_protagonist": {"provider": "mock", "model": "mock-test-v1"},
            "editor_stylist": {"provider": "mock", "model": "mock-test-v1"},
            "editor_agent": {"provider": "mock", "model": "mock-test-v1"},
            "evaluator": {"provider": "mock", "model": "mock-test-v1"},
            "analyzer": {"provider": "mock", "model": "mock-test-v1"},
        },
    })

    # ── Usage (empty) ──
    _write(target / "usage.json", {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_calls": 0,
        "by_provider": {},
        "by_model": {},
        "by_role": {},
        "recent": [],
    })

    # ── Crawler DB (simulated InkOctoBot_Crawler.db) ──
    _seed_crawler_db(target / "InkOctoBot_Crawler.db")

    # ── RAG calibration data — fills L2 ChapterBuffer, L4 EpisodicTimeline,
    # Truth Files, and project memory for the 3 seeded chapters so the
    # user can immediately inspect them via /api/debug/rag-preview ,
    # /api/debug/truth-files, /api/debug/memory.
    _seed_rag_calibration_data(target / "novels.db", pid, _RAG_SEED_CHAPTERS)

    # ── Reference DB — 参考作品库 (data/reference.db) ──
    _seed_reference_db(target / "reference.db")

    # ── Idea DB — 灵感库 (data/idea.db) ──
    _seed_idea_db(target / "idea.db")

    # ── v2 schema migration: copy the JSON characters/worldbook/
    # project_memory/storyline files we just wrote into the new DB
    # tables so the DB-backed routers (project_store) see them too.
    # See docs/SCHEMA_REDESIGN.md.
    from scripts.migrate_to_v2_schema import run_migration
    run_migration(target, target / "novels.db")

    print(f"Test data seeded into: {target}")


def _seed_rag_calibration_data(novels_db_path: Path, pid: str,
                                chapters: list) -> None:
    """Populate L2 ChapterBuffer + L4 EpisodicTimeline + Truth Files +
    project memory so the test-mode user can immediately inspect RAG
    state for manual calibration.

    ``chapters`` is a list of ``(chapter_num, title, full_text)`` tuples.
    For each one we synthesize:
      - L2 summary (3-4 sentence per-chapter recap)
      - L4 key_events (1-3 events flagged with foreshadow_status)
      - Truth-File deltas (current_state, hooks, character_matrix)
      - 2-3 project_memory entries the user confirmed
    """
    # Ensure the project schema exists (creates the file if missing).
    from storage.project_schema import ensure_creation_tables
    con = sqlite3.connect(str(novels_db_path))
    try:
        ensure_creation_tables(con)
        # Insert the project row so memory FKs resolve.
        # status CHECK constraint: planning/writing/paused/completed
        con.execute(
            "INSERT OR IGNORE INTO projects (project_id, title, genre, status) "
            "VALUES (?, ?, ?, ?)",
            (pid, "测试项目：星辰大海", "科幻悬疑", "writing"),
        )
        con.commit()
        # Per-chapter pre-canned summaries + key events. Each row is what
        # the Consolidator WOULD have produced; user can compare against
        # raw text via /api/debug/memory?project_id=...&layer=L2.
        chapter_seeds = [
            {
                "chapter_num": 1,
                "summary": (
                    "李星河在矿星 K-7 的考古调查中触碰到一块青色水晶，"
                    "通过他的感知能力获得了关于「天蝎座 ζ-79」星门坐标的远古记忆。"
                    "他意识到这远超普通考古发现的级别。"
                ),
                "key_events": [
                    {"type": "revelation",
                     "description": "李星河发现远古水晶并感知到星门坐标",
                     "characters": ["李星河"],
                     "foreshadow_status": "planted",
                     "importance": 5},
                ],
            },
            {
                "chapter_num": 2,
                "summary": (
                    "联邦安全局少校苏晚率队抵达 K-7，自称在追踪远古信号源。"
                    "李星河选择隐瞒水晶内容的全部细节，只承认发现了存储介质。"
                    "苏晚的旧伤暗示她过去经历过一场任务失败。"
                ),
                "key_events": [
                    {"type": "character_change",
                     "description": "苏晚率特工小队抵达 K-7",
                     "characters": ["苏晚"], "importance": 4},
                    {"type": "foreshadowing",
                     "description": "李星河对苏晚隐瞒了星门坐标的具体内容",
                     "characters": ["李星河", "苏晚"],
                     "foreshadow_status": "planted",
                     "importance": 3},
                ],
            },
            {
                "chapter_num": 3,
                "summary": (
                    "联邦征用了李星河的考古许可，强制他加入苏晚的小队。"
                    "在矿星地下三百米的远古遗迹中，苏晚救了李星河一命，"
                    "两人之间的关系第一次出现裂缝——开始信任。"
                ),
                "key_events": [
                    {"type": "relationship_change",
                     "description": "苏晚在防御机制启动时救了李星河",
                     "characters": ["李星河", "苏晚"], "importance": 4},
                    {"type": "relationship_change",
                     "description": "李星河首次不用职位称呼苏晚",
                     "characters": ["李星河", "苏晚"], "importance": 3},
                ],
            },
        ]
        # L2: chapter_summaries rows (active = 1 → live in buffer)
        for s in chapter_seeds:
            con.execute(
                """INSERT INTO chapter_summaries
                   (summary_id, project_id, chapter_num, summary_text,
                    key_events_json, character_states_json,
                    is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)""",
                (f"sum_test_{s['chapter_num']:03d}", pid, s["chapter_num"],
                 s["summary"],
                 json.dumps(s["key_events"], ensure_ascii=False),
                 json.dumps({}, ensure_ascii=False)),
            )
        # L4: episodic_events
        evt_id = 0
        for s in chapter_seeds:
            for ev in s["key_events"]:
                evt_id += 1
                con.execute(
                    """INSERT INTO episodic_events
                       (event_id, project_id, chapter_num, scene_index,
                        event_type, description, characters_json,
                        importance, foreshadow_status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (f"evt_test_{evt_id:03d}", pid, s["chapter_num"], 0,
                     ev["type"], ev["description"],
                     json.dumps(ev.get("characters", []), ensure_ascii=False),
                     ev.get("importance", 3),
                     ev.get("foreshadow_status", "")),
                )
        # information_events: knowledge isolation — 李星河不知道格里芬的真实身份
        con.execute(
            """INSERT INTO information_events
               (info_id, project_id, character_name, fact_key,
                knowledge_state, believed_value, true_value,
                source_chapter, source_description)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("info_test_001", pid, "李星河",
             "格里芬的真实身份",
             "known_false",
             "格里芬是中年的星际历史学家",
             "格里芬是活了300年的远古守门人，是黑石商会内线",
             3,
             "李星河此时还没有任何线索能拆穿格里芬"),
        )
        con.commit()

        # ── Truth Files ── Use the canonical apply_deltas path so
        # validators run and the apply_log is populated. This gives
        # /api/debug/truth-files real rows to display.
        from knowledge.truth.store import TruthFileStore
        from knowledge.truth.schemas import (
            TruthDeltas, StatePatch, HookDelta, HookImportance,
            RelationUpdate, EmotionArcEntry, ChapterSummaryDelta,
        )
        store = TruthFileStore(project_id=pid, db_path=str(novels_db_path))

        # Chapter 1 — 李星河 first encounter with crystal
        store.apply_deltas(TruthDeltas(
            chapter_num=1,
            current_state_patches=[
                StatePatch(subject="李星河", predicate="位置",
                           object="废弃矿星K-7", valid_from_chapter=1),
                StatePatch(subject="李星河", predicate="状态",
                           object="持有远古水晶", valid_from_chapter=1),
            ],
            hook_deltas=[
                HookDelta(
                    hook_id="h_starport_zeta79",
                    action="new",
                    description="李星河获知「天蝎座 ζ-79」星门坐标，但还未告知任何人",
                    importance=HookImportance.A,
                ),
            ],
            chapter_summary=ChapterSummaryDelta(
                summary=chapter_seeds[0]["summary"],
                key_events=[e["description"] for e in chapter_seeds[0]["key_events"]],
                pov_character="李星河",
                mood="紧张+震撼",
            ),
            emotion_arc_entries=[
                EmotionArcEntry(
                    character="李星河",
                    from_state="平静",
                    to_state="震撼",
                    trigger="首次感知到远古文明的完整记忆",
                ),
            ],
        ))

        # Chapter 2 — Su Wan enters
        store.apply_deltas(TruthDeltas(
            chapter_num=2,
            current_state_patches=[
                StatePatch(subject="苏晚", predicate="位置",
                           object="废弃矿星K-7", valid_from_chapter=2),
            ],
            hook_deltas=[
                HookDelta(
                    hook_id="h_su_old_wound",
                    action="new",
                    description="苏晚左手无名指有道旧伤疤，背后另有故事",
                    importance=HookImportance.B,
                ),
            ],
            chapter_summary=ChapterSummaryDelta(
                summary=chapter_seeds[1]["summary"],
                key_events=[e["description"] for e in chapter_seeds[1]["key_events"]],
                pov_character="李星河",
                mood="警惕+试探",
            ),
            character_relation_updates=[
                RelationUpdate(
                    character_a="李星河", character_b="苏晚",
                    relation_type="警惕的同行者",
                    sentiment_score=-10, trust_level=5,
                ),
                RelationUpdate(
                    character_a="苏晚", character_b="李星河",
                    relation_type="可利用的资源",
                    sentiment_score=0, trust_level=15,
                ),
            ],
        ))

        # Chapter 3 — first trust beat
        store.apply_deltas(TruthDeltas(
            chapter_num=3,
            current_state_patches=[
                StatePatch(subject="李星河", predicate="身份",
                           object="联邦少校副官", valid_from_chapter=3),
            ],
            character_relation_updates=[
                RelationUpdate(
                    character_a="李星河", character_b="苏晚",
                    relation_type="信任的开端",
                    sentiment_score=15, trust_level=25,
                ),
                RelationUpdate(
                    character_a="苏晚", character_b="李星河",
                    relation_type="值得保护的同伴",
                    sentiment_score=10, trust_level=30,
                ),
            ],
            chapter_summary=ChapterSummaryDelta(
                summary=chapter_seeds[2]["summary"],
                key_events=[e["description"] for e in chapter_seeds[2]["key_events"]],
                pov_character="李星河",
                mood="对抗→缓和",
            ),
            emotion_arc_entries=[
                EmotionArcEntry(
                    character="李星河",
                    from_state="抗拒",
                    to_state="动摇",
                    trigger="第一次承认欠苏晚一份人情",
                ),
            ],
        ))
    finally:
        con.close()

    # ── Project memory ── persistent facts the user has confirmed
    pm_dir = novels_db_path.parent / "project_memory"
    pm_dir.mkdir(exist_ok=True)
    _write(pm_dir / f"{pid}.json", {
        "project_id": pid,
        "memories": [
            {"id": "mem_001",
             "content": "李星河的感知能力每次使用后会让他短暂失去三十分钟内的自身情感记忆，这是后续重要伏笔",
             "category": "rule",
             "ts": time.time()},
            {"id": "mem_002",
             "content": "故事时间线设定在银河历 2847 年秋季，全卷只覆盖这一个季节",
             "category": "setting",
             "ts": time.time()},
            {"id": "mem_003",
             "content": "苏晚不知道李星河获得了星门坐标——这个秘密要保留到第二卷揭晓",
             "category": "secret",
             "ts": time.time()},
        ],
        "saved_at": time.time(),
    })


def _seed_idea_db(db_path: Path) -> None:
    """Seed data/idea.db with a few sample 灵感 entries."""
    if db_path.exists():
        db_path.unlink()
    from storage.idea_schema import ensure_idea_tables
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        ensure_idea_tables(con)
        for iid, cat, title, content in [
            ("insp_test_001", "scene",
             "深夜独白", "主角在深夜书房，对着窗外月光自言自语，回忆童年。"),
            ("insp_test_002", "plot_device",
             "被掉包的信物", "传家玉佩在主角不知情时被反派替换为赝品。"),
            ("insp_test_003", "character",
             "笑面虎师叔", "永远笑眯眯但每次出现都带来更深一层危险的角色。"),
        ]:
            con.execute(
                "INSERT INTO inspirations (id, category, title, content) "
                "VALUES (?, ?, ?, ?)",
                (iid, cat, title, content),
            )
        con.commit()
    finally:
        con.close()


def _seed_crawler_db(db_path: Path) -> None:
    """Create a mock InkOctoBot_Crawler.db with sample novels and rankings."""
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")

    # Create tables (from database/db_schema.py)
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS novels (
            novel_uid INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL, platform_novel_id TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT '', author_norm TEXT NOT NULL DEFAULT '',
            intro TEXT NOT NULL DEFAULT '', intro_norm TEXT NOT NULL DEFAULT '',
            main_category TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ongoing', total_words INTEGER NOT NULL DEFAULT 0,
            url TEXT NOT NULL DEFAULT '', signature_json TEXT NOT NULL DEFAULT '{}',
            created_date DATE DEFAULT NULL, last_seen_date DATE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS novel_titles (
            title_id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_uid INTEGER NOT NULL, title TEXT NOT NULL, title_norm TEXT NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            first_seen_date DATE NOT NULL, last_seen_date DATE NOT NULL,
            FOREIGN KEY(novel_uid) REFERENCES novels(novel_uid) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tags (
            tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_name TEXT NOT NULL, tag_norm TEXT NOT NULL, UNIQUE(tag_norm)
        );
        CREATE TABLE IF NOT EXISTS novel_tag_map (
            novel_uid INTEGER NOT NULL, tag_id INTEGER NOT NULL,
            PRIMARY KEY(novel_uid, tag_id)
        );
        CREATE TABLE IF NOT EXISTS rank_lists (
            rank_list_id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL, rank_family TEXT NOT NULL,
            rank_sub_cat TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
            UNIQUE(platform, rank_family, rank_sub_cat)
        );
        CREATE TABLE IF NOT EXISTS rank_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            rank_list_id INTEGER NOT NULL, snapshot_date DATE NOT NULL,
            item_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(rank_list_id) REFERENCES rank_lists(rank_list_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS rank_entries (
            snapshot_id INTEGER NOT NULL, novel_uid INTEGER NOT NULL,
            rank INTEGER NOT NULL, total_recommend INTEGER DEFAULT NULL,
            reading_count INTEGER DEFAULT NULL, extra_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(snapshot_id, novel_uid)
        );
        CREATE TABLE IF NOT EXISTS first_n_chapters (
            chapter_id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_uid INTEGER NOT NULL, chapter_num INTEGER NOT NULL,
            chapter_title TEXT NOT NULL, chapter_content TEXT NOT NULL DEFAULT '',
            chapter_url TEXT NOT NULL DEFAULT '', word_count INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT NOT NULL DEFAULT '', publish_date DATE NOT NULL,
            FOREIGN KEY(novel_uid) REFERENCES novels(novel_uid) ON DELETE CASCADE
        );
    """)

    today = "2026-03-12"

    # Sample novels
    novels = [
        ("qidian", "1001", "张三", "玄幻", "ongoing", 1500000, "一个少年的修仙之路"),
        ("qidian", "1002", "李四", "都市", "ongoing", 800000, "都市中的超能力者"),
        ("qidian", "1003", "王五", "科幻", "completed", 2000000, "星际战争年代记"),
        ("fanqie", "2001", "赵六", "言情", "ongoing", 600000, "穿越之锦绣良缘"),
        ("fanqie", "2002", "钱七", "悬疑", "ongoing", 450000, "午夜诡谈"),
        ("fanqie", "2003", "孙八", "玄幻", "ongoing", 1200000, "万界之主"),
        ("qidian", "1004", "周九", "历史", "completed", 3000000, "大唐风云录"),
        ("qidian", "1005", "吴十", "游戏", "ongoing", 900000, "全球进化时代"),
    ]
    for plat, pid_n, author, cat, status, words, intro in novels:
        cur.execute(
            "INSERT INTO novels (platform, platform_novel_id, author, author_norm, "
            "intro, main_category, status, total_words, last_seen_date) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (plat, pid_n, author, author.lower(), intro, cat, status, words, today),
        )

    # Titles
    titles = [
        (1, "修仙大帝"), (2, "都市异能王"), (3, "星际编年史"),
        (4, "锦绣良缘"), (5, "午夜诡谈录"), (6, "万界至尊"),
        (7, "大唐双龙传"), (8, "进化狂潮"),
    ]
    for uid, title in titles:
        cur.execute(
            "INSERT INTO novel_titles (novel_uid, title, title_norm, is_primary, "
            "first_seen_date, last_seen_date) VALUES (?,?,?,1,?,?)",
            (uid, title, title.lower(), today, today),
        )

    # Tags
    tag_names = ["玄幻", "都市", "科幻", "言情", "悬疑", "热血", "系统", "穿越", "历史", "游戏"]
    for t in tag_names:
        cur.execute("INSERT INTO tags (tag_name, tag_norm) VALUES (?,?)", (t, t.lower()))

    # Tag map
    tag_map = [(1, 1), (1, 6), (2, 2), (2, 7), (3, 3), (4, 4), (4, 8),
               (5, 5), (6, 1), (6, 6), (7, 9), (8, 10), (8, 7)]
    for nuid, tid in tag_map:
        cur.execute("INSERT INTO novel_tag_map VALUES (?,?)", (nuid, tid))

    # Rank lists
    rank_lists = [
        ("qidian", "畅销榜", ""), ("qidian", "推荐榜", ""),
        ("qidian", "新书榜", "玄幻"), ("qidian", "月票榜", ""),
        ("fanqie", "阅读榜", ""), ("fanqie", "新书榜", ""),
    ]
    for plat, fam, sub in rank_lists:
        cur.execute(
            "INSERT INTO rank_lists (platform, rank_family, rank_sub_cat) VALUES (?,?,?)",
            (plat, fam, sub),
        )

    # Snapshots (3 days of data)
    dates = ["2026-03-10", "2026-03-11", "2026-03-12"]
    snap_id = 0
    for rl_id in range(1, 7):
        for d in dates:
            snap_id += 1
            cur.execute(
                "INSERT INTO rank_snapshots (rank_list_id, snapshot_date, item_count) "
                "VALUES (?,?,?)", (rl_id, d, 5),
            )

    # Rank entries
    import random
    random.seed(42)
    for sid in range(1, snap_id + 1):
        novel_pool = list(range(1, 9))
        random.shuffle(novel_pool)
        for rank, nuid in enumerate(novel_pool[:5], 1):
            cur.execute(
                "INSERT INTO rank_entries (snapshot_id, novel_uid, rank, "
                "total_recommend, reading_count) VALUES (?,?,?,?,?)",
                (sid, nuid, rank, random.randint(1000, 50000), random.randint(5000, 200000)),
            )

    # First N chapters
    chapter_samples = [
        (1, 1, "第一章 少年出山", "少年站在山巅，眺望着远方连绵不绝的群山...", 3200),
        (1, 2, "第二章 初入仙门", "仙门坐落于云端之上，仙雾缭绕间...", 3500),
        (2, 1, "第一章 觉醒之日", "林默在一个平凡的早晨醒来，却发现世界变了...", 2800),
        (3, 1, "第一章 星际纪元", "人类进入星际时代已经三百年...", 4000),
        (4, 1, "第一章 魂穿异世", "睁开眼的那一刻，李清歌发现自己躺在一张古色古香的木床上...", 3000),
        (6, 1, "第一章 万界降临", "天空裂开了一道缝隙，无数碎片从中坠落...", 3100),
    ]
    for nuid, cnum, ctitle, content, wc in chapter_samples:
        cur.execute(
            "INSERT INTO first_n_chapters (novel_uid, chapter_num, chapter_title, "
            "chapter_content, word_count, publish_date) VALUES (?,?,?,?,?,?)",
            (nuid, cnum, ctitle, content, wc, today),
        )

    con.commit()
    con.close()


def _seed_reference_db(db_path: Path) -> None:
    """Create a mock reference DB (novels.db) with sample reference works and entries."""
    # ``target`` is the data dir (parent of the .db file); used below to
    # create sibling JSON folders (preferences/, skill_learning_log/).
    # ``pid`` matches the project_id seeded in seed() so the prefs +
    # skill-learning log line up with the example project.
    target = db_path.parent
    pid = "test_project_001"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS reference_works (
            ref_id TEXT PRIMARY KEY, title TEXT NOT NULL, creator TEXT,
            media_type TEXT NOT NULL DEFAULT 'web_novel',
            genre TEXT, tags_json TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            platform TEXT, novel_uid INTEGER, file_path TEXT,
            user_rating INTEGER, user_summary TEXT,
            user_why_i_like TEXT, learning_dimensions_json TEXT,
            has_full_text INTEGER NOT NULL DEFAULT 0,
            preprocessing_status TEXT NOT NULL DEFAULT 'not_applicable',
            style_fingerprint_json TEXT, narrative_structure_json TEXT,
            extracted_characters_json TEXT, rhythm_template_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reference_entries (
            entry_id TEXT PRIMARY KEY, ref_id TEXT NOT NULL,
            entry_type TEXT NOT NULL DEFAULT 'other',
            title TEXT, content TEXT,
            content_source TEXT DEFAULT 'user_written',
            position_label TEXT, user_notes TEXT,
            learning_dimensions_json TEXT,
            user_rating INTEGER, tags_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ref_id) REFERENCES reference_works (ref_id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS project_reference_links (
            link_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, ref_id TEXT NOT NULL,
            dimension TEXT NOT NULL,
            entry_ids_json TEXT, reference_character_name TEXT, notes TEXT,
            FOREIGN KEY (ref_id) REFERENCES reference_works (ref_id) ON DELETE CASCADE
        );
    """)

    # Sample reference works
    works = [
        ("ref_test_001", "诛仙", "萧鼎", "web_novel", "仙侠", '["经典","仙侠"]',
         "manual", None, None, 5, "仙侠经典之作", "世界观宏大，感情线细腻",
         '["style","world"]'),
        ("ref_test_002", "斗破苍穹", "天蚕土豆", "web_novel", "玄幻", '["热血","升级"]',
         "manual", None, None, 4, "爽文标杆", "节奏把控出色，爽点密集",
         '["plot","style"]'),
        ("ref_test_003", "三体", "刘慈欣", "literature", "科幻", '["硬科幻","哲学"]',
         "manual", None, None, 5, "中国科幻巅峰", "硬科幻设定与哲学思考的完美融合",
         '["world","style","mood"]'),
    ]
    for w in works:
        cur.execute(
            "INSERT INTO reference_works (ref_id, title, creator, media_type, genre, "
            "tags_json, source, platform, novel_uid, user_rating, user_summary, "
            "user_why_i_like, learning_dimensions_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", w,
        )

    # Sample reference entries
    entries = [
        ("ent_test_001", "ref_test_001", "style_sample", "碧瑶的自我牺牲",
         "天地不仁，以万物为刍狗。她站在诛仙剑下，嘴角带着温柔的笑意...",
         "original_text", "第84章", "经典感情高潮场景", None, 5, '["感情","牺牲"]'),
        ("ent_test_002", "ref_test_001", "worldbuilding", "青云门体系",
         "青云门分七脉，各有所长。大竹峰擅剑术，小竹峰善法术...",
         "user_written", None, "完善的门派体系参考", None, 4, '["设定","门派"]'),
        ("ent_test_003", "ref_test_002", "hook", "开篇废材设定",
         "三年之约，萧炎从天才跌落为废物。纳兰嫣然的退婚更是雪上加霜...",
         "user_written", "第1章", "经典废材流开篇hook", None, 5, '["开篇","hook"]'),
        ("ent_test_004", "ref_test_003", "atmosphere", "三体世界描写",
         "三个太阳无规律地出现，文明在一次次毁灭中轮回...",
         "original_text", "第一部", "极致的末日氛围营造", None, 5, '["氛围","科幻"]'),
    ]
    for e in entries:
        cur.execute(
            "INSERT INTO reference_entries (entry_id, ref_id, entry_type, title, "
            "content, content_source, position_label, user_notes, "
            "learning_dimensions_json, user_rating, tags_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", e,
        )

    # Link to test project
    cur.execute(
        "INSERT INTO project_reference_links (link_id, project_id, ref_id, dimension, notes) "
        "VALUES (?,?,?,?,?)",
        ("lnk_test_001", "test_project_001", "ref_test_003", "world", "科幻世界观参考"),
    )

    con.commit()
    con.close()

    # ── Preferences (偏好记忆) ──
    pref_dir = target / "preferences"
    pref_dir.mkdir(exist_ok=True)
    _write(pref_dir / f"{pid}.json", {
        "entries": [
            {"id": "pref_001", "timestamp": "2026-03-13 10:00", "action": "用户输入 (头脑风暴)", "detail": "帮我构思一个玄幻小说的核心设定和卖点", "scope": "studio_brainstorm", "role": "user"},
            {"id": "pref_002", "timestamp": "2026-03-13 10:05", "action": "用户输入 (头脑风暴)", "detail": "我喜欢轻松幽默的文风，不要太严肃", "scope": "studio_brainstorm", "role": "user"},
            {"id": "pref_003", "timestamp": "2026-03-13 09:50", "action": "用户输入 (风格校准)", "detail": "我想要偏向轻松幽默的文风", "scope": "studio_calibration", "role": "user"},
            {"id": "pref_004", "timestamp": "2026-03-13 09:30", "action": "用户输入 (热点讨论)", "detail": "玄幻题材目前市场竞争大吗？", "scope": "studio_trending", "role": "user"},
            {"id": "pref_005", "timestamp": "2026-03-13 09:20", "action": "用户输入 (大纲助手)", "detail": "根据这一章的定位，帮我生成详细的章节大纲", "scope": "outline_chat", "role": "user"},
        ],
        "summary": "（测试数据）共收集到 5 条用户交互记录。涉及 4 个对话场景。",
        "extracted_memories": [
            {"id": "mem_pref_002", "content": "偏好轻松幽默的文风，不喜欢过于严肃的写法", "source": "头脑风暴", "timestamp": "2026-03-13 10:05"},
            {"id": "mem_pref_003", "content": "希望文风偏向轻松幽默", "source": "风格校准", "timestamp": "2026-03-13 09:50"},
        ],
        "deleted_ids": [],
        "deleted_entries": [],
        "saved_at": time.time(),
    })

    # ── Skill Learning Log ──
    sl_dir = target / "skill_learning_log"
    sl_dir.mkdir(exist_ok=True)
    _write(sl_dir / "log.json", {
        "entries": [
            {
                "id": "sl_test_001",
                "skill_name": "style_tone_adjuster",
                "display_name": "风格语气调整器",
                "trigger": "用户多次修改AI生成文本的语气和基调",
                "need_description": "自动检测并调整输出文风以匹配用户偏好的轻松幽默风格",
                "project_id": pid,
                "created_at": "2026-03-12 14:30",
            },
            {
                "id": "sl_test_002",
                "skill_name": "dialogue_naturalizer",
                "display_name": "对话自然化处理",
                "trigger": "评估器多次标记对话不自然",
                "need_description": "优化角色对话的口语化程度和个性化表达",
                "project_id": pid,
                "created_at": "2026-03-11 09:15",
            },
            {
                "id": "sl_test_003",
                "skill_name": "pacing_optimizer",
                "display_name": "节奏优化器",
                "trigger": "用户反复调整段落长度和场景切换节奏",
                "need_description": "根据场景类型自动调整叙事节奏和段落密度",
                "project_id": pid,
                "created_at": "2026-03-10 16:45",
            },
        ],
    })


def _write(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = Path(__file__).resolve().parent / "data_test"
    seed(target)
