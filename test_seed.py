"""
Seed a test data directory with sample data for the test-only app mode.

Usage:
    python test_seed.py [target_dir]

If target_dir is omitted, seeds into data_test/ under the repo root.
"""
from __future__ import annotations

import json
import os
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
    _write(wb_dir / f"{_nid()}.json", {
        "id": _nid(),
        "project_id": pid,
        "name": "银河联邦",
        "content": "人类在2500年建立的星际政府，管辖超过200个恒星系。联邦首都位于地球的轨道空间站「新日内瓦」。",
        "tags": ["设定", "政治"],
        "created_at": time.time(),
    })
    _write(wb_dir / f"{_nid()}.json", {
        "id": _nid(),
        "project_id": pid,
        "name": "星门",
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

    # ── Settings ──
    _write(target / "settings.json", {
        "auto_save": True,
        "auto_save_interval": 30,
        "cost_confirm": False,
        "export_format": "txt",
        "providers": {},
        "pipeline": {},
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

    print(f"Test data seeded into: {target}")


def _write(p: Path, data: dict) -> None:
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = Path(__file__).resolve().parent / "data_test"
    seed(target)
