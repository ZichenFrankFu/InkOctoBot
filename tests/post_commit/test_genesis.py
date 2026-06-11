"""Storyland 创世 (spec 3.3.3.4) — proposal / review / apply."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import genesis


_LLM_OUTPUT = {
    "stage": "临海城",
    "entities": [
        {"name": "临海城", "entity_type": "location",
         "description": "故事开始的港口城市", "aliases": ["海城"],
         "from_user_material": True},
        {"name": "码头区", "entity_type": "location",
         "description": "步骤二生成的区域", "aliases": [],
         "from_user_material": False},
        {"name": "盐商会", "entity_type": "organization",
         "description": "控制盐贸的商会", "aliases": [],
         "from_user_material": False},
        {"name": "张远", "entity_type": "character",
         "description": "主角", "aliases": [], "from_user_material": True},
        {"name": "", "entity_type": "location", "description": "无名实体",
         "aliases": []},                       # invalid → dropped
        {"name": "幽灵", "entity_type": "ghost", "description": "非法类型",
         "aliases": []},                       # invalid → dropped
    ],
    "facts": [
        {"subject": "临海城", "predicate": "具有", "object": "经济=海贸",
         "step": 1, "basis": "世界书: 港口城市"},
        {"subject": "临海城", "predicate": "包含", "object": "码头区",
         "step": 2, "basis": "港口城应有码头"},
        {"subject": "张远", "predicate": "位于", "object": "码头区",
         "step": 3, "basis": "角色卡: 在码头讨生活"},
        {"subject": "临海城", "predicate": "具有", "object": "治安=4",
         "step": 4, "basis": "鱼龙混杂"},
        {"subject": "张远", "predicate": "飞升", "object": "上界",
         "step": 3, "basis": "非法谓词"},      # invalid → dropped
    ],
}


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "genesis.db"
    con = sqlite3.connect(str(path))
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    con.execute(
        "INSERT INTO characters (character_id, project_id, name, role, "
        "background) VALUES ('c1', 'p1', '张远', '主角', '在临海城码头讨生活')",
    )
    con.execute(
        "INSERT INTO worldbook_entries (entry_id, project_id, category, "
        "title, content) "
        "VALUES ('w1', 'p1', '地点', '临海城', '海贸港口城市，鱼盐之利')",
    )
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def llm(monkeypatch):
    async def fake_invoke(self, *, prompt, system, **kw):
        # Material must reach the prompt (机制5 derivability).
        assert "张远" in prompt and "临海城" in prompt
        return json.dumps(_LLM_OUTPUT, ensure_ascii=False)
    monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)


class TestRun:
    @pytest.mark.asyncio
    async def test_run_normalizes_and_stores_proposal(self, db, llm) -> None:
        out = await genesis.run_genesis(db, "p1")
        assert out["stage"] == "临海城"
        names = [e["name"] for e in out["entities"]]
        assert names == ["临海城", "码头区", "盐商会", "张远"]
        assert len(out["facts"]) == 4          # illegal predicate dropped
        pending = genesis.get_pending_genesis(db, "p1")
        assert pending["proposal_id"] == out["proposal_id"]
        # Nothing written yet (机制6 审阅先行).
        with sqlite3.connect(db) as con:
            assert con.execute(
                "SELECT COUNT(*) FROM storyland_entities "
                "WHERE project_id='p1'").fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_empty_project_rejected(self, db, llm, tmp_path) -> None:
        path = tmp_path / "empty.db"
        con = sqlite3.connect(str(path))
        ensure_creation_tables(con)
        con.execute("INSERT INTO projects(project_id, title) VALUES('p2','y')")
        con.commit()
        con.close()
        with pytest.raises(ValueError):
            await genesis.run_genesis(str(path), "p2")

    @pytest.mark.asyncio
    async def test_rerun_supersedes_previous_proposal(self, db, llm) -> None:
        first = await genesis.run_genesis(db, "p1")
        second = await genesis.run_genesis(db, "p1")
        with sqlite3.connect(db) as con:
            rows = dict(con.execute(
                "SELECT proposal_id, status FROM pending_state_extractions "
                "WHERE chapter_id='__genesis__'",
            ).fetchall())
        assert rows[first["proposal_id"]] == "discarded"
        assert rows[second["proposal_id"]] == "pending"


class TestApply:
    @pytest.mark.asyncio
    async def test_apply_writes_entities_and_facts_at_chapter_zero(
        self, db, llm,
    ) -> None:
        out = await genesis.run_genesis(db, "p1")
        result = genesis.apply_genesis(db, "p1", out["proposal_id"])
        assert result["entities_created"] == 4
        assert result["facts_created"] == 4
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            ent = con.execute(
                "SELECT * FROM storyland_entities WHERE name='码头区'",
            ).fetchone()
            assert ent["introduced_chapter"] == 0
            assert ent["auto_created"] == 1
            assert json.loads(ent["metadata_json"])["origin"] == "genesis"
            fact = con.execute(
                "SELECT * FROM truth_current_state "
                "WHERE subject='临海城' AND predicate='包含'",
            ).fetchone()
            assert fact["valid_from_chapter"] == 0
            assert fact["subject_type"] == "location"
            char_fact = con.execute(
                "SELECT subject_type FROM truth_current_state "
                "WHERE subject='张远' AND predicate='位于'",
            ).fetchone()
            assert char_fact["subject_type"] == "character"
            log = con.execute(
                "SELECT trigger FROM state_change_log "
                "WHERE subject='临海城' AND predicate='包含'",
            ).fetchone()
            assert log["trigger"].startswith("创世·步骤2")

    @pytest.mark.asyncio
    async def test_apply_respects_user_edited_subset(self, db, llm) -> None:
        """审阅区删掉的条目不入库 (机制6)."""
        out = await genesis.run_genesis(db, "p1")
        kept_entities = [e for e in out["entities"] if e["name"] != "盐商会"]
        kept_facts = [f for f in out["facts"] if f["step"] != 4]
        result = genesis.apply_genesis(
            db, "p1", out["proposal_id"],
            entities=kept_entities, facts=kept_facts,
        )
        assert result["entities_created"] == 3
        assert result["facts_created"] == 3
        with sqlite3.connect(db) as con:
            assert con.execute(
                "SELECT COUNT(*) FROM storyland_entities "
                "WHERE name='盐商会'").fetchone()[0] == 0

    @pytest.mark.asyncio
    async def test_existing_user_facts_not_overwritten(self, db, llm) -> None:
        """机制6: 用户已写的事实优先于 AI 生成."""
        with sqlite3.connect(db) as con:
            con.execute(
                "INSERT INTO truth_current_state (triple_id, project_id, "
                "subject, subject_type, predicate, object, "
                "valid_from_chapter) "
                "VALUES ('t_user', 'p1', '张远', 'character', '位于', "
                "'青云山', 0)",
            )
            con.commit()
        out = await genesis.run_genesis(db, "p1")
        genesis.apply_genesis(db, "p1", out["proposal_id"])
        with sqlite3.connect(db) as con:
            rows = con.execute(
                "SELECT object FROM truth_current_state "
                "WHERE subject='张远' AND predicate='位于' "
                "AND valid_to_chapter IS NULL",
            ).fetchall()
        assert [r[0] for r in rows] == ["青云山"]

    @pytest.mark.asyncio
    async def test_double_apply_blocked(self, db, llm) -> None:
        out = await genesis.run_genesis(db, "p1")
        genesis.apply_genesis(db, "p1", out["proposal_id"])
        with pytest.raises(ValueError):
            genesis.apply_genesis(db, "p1", out["proposal_id"])
