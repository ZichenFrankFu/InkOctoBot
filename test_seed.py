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
        "tags": ["女主角", "特工", "格斗"],
        "created_at": time.time(),
    })

    # ── Editor (volumes + chapters) ──
    editor_dir = target / "editor"
    editor_dir.mkdir(exist_ok=True)
    _write(editor_dir / f"{pid}.json", {
        "project_id": pid,
        "volumes": [
            {
                "id": _nid(),
                "project_id": pid,
                "title": "第一卷：遗迹之谜",
                "order": 0,
                "chapters": [
                    {
                        "id": _nid(),
                        "volume_id": "v1",
                        "title": "第一章：意外发现",
                        "order": 0,
                        "synopsis": "李星河在银河边缘的废弃矿星上进行例行考古调查时，触碰到一块异常的水晶，感知到了远古文明的最后记忆——一段关于「星门」位置的信息。",
                        "content": "测试章节内容...\n\n矿星的地表覆盖着一层细密的灰色沙尘，在微弱的恒星光下泛着暗淡的金属光泽。李星河蹲在一处裸露的岩层前，手持便携式光谱分析仪对着岩石表面缓慢扫描。",
                        "word_count": 1200,
                        "status": "draft",
                        "time": "银河历2847年·秋",
                        "location": "废弃矿星K-7",
                        "characters": ["李星河"],
                    },
                    {
                        "id": _nid(),
                        "volume_id": "v1",
                        "title": "第二章：不速之客",
                        "order": 1,
                        "synopsis": "李星河正准备将发现上报学院时，苏晚带领特工小队突然出现在矿星上。她奉命调查最近频繁出现的远古信号源，而信号恰好来自李星河发现的水晶。",
                        "content": "",
                        "word_count": 0,
                        "status": "draft",
                        "time": "银河历2847年·秋",
                        "location": "废弃矿星K-7",
                        "characters": ["李星河", "苏晚"],
                    },
                    {
                        "id": _nid(),
                        "volume_id": "v1",
                        "title": "第三章：被迫同行",
                        "order": 2,
                        "synopsis": "联邦安全局征用了李星河的考古许可，强制他加入苏晚的调查小队担任顾问。两人性格冲突不断，但在共同面对矿星深处的古代防御机制时，开始建立信任。",
                        "content": "",
                        "word_count": 0,
                        "status": "draft",
                        "time": "银河历2847年·秋",
                        "location": "废弃矿星K-7·地下遗迹",
                        "characters": ["李星河", "苏晚"],
                    },
                ],
            },
        ],
        "saved_at": time.time(),
    })

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

    # ── Chat history (sample) ──
    ch_dir = target / "chat_history"
    ch_dir.mkdir(exist_ok=True)
    _write(ch_dir / f"{pid}_outline_chat.json", {
        "project_id": pid,
        "scope": "outline_chat",
        "messages": [
            {"role": "user", "content": "我想写一个星际考古的故事，主角能感知古代遗物的情感。", "ts": time.time() - 3600},
            {"role": "assistant", "content": "这个设定很有潜力！「感知遗物情感」可以作为推动剧情的核心能力。几个建议：\n\n1. 这种能力的代价是什么？（比如每次使用后会短暂失去自己的情感记忆）\n2. 远古文明灭亡的原因可以和这种能力有关\n3. 反派可以是想利用这种能力来操控古代武器的人\n\n你想先从哪个方向深入？", "ts": time.time() - 3500},
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

    # ── Reference DB (novels.db with reference tables) ──
    _seed_reference_db(target / "novels.db")

    print(f"Test data seeded into: {target}")


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


def _write(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = Path(__file__).resolve().parent / "data_test"
    seed(target)
