"""专业知识自学习 (spec 4.2) — research, label, recall survival, CRUD."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services import knowledge_research as kr
from ui.backend.app.services import skill_index


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "knowledge.db"
    con = sqlite3.connect(str(path))
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects(project_id, title) VALUES('p1','x')")
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def web_llm(monkeypatch):
    """Provider DOES support web search."""
    async def fake_web(self, *, role, prompt, max_tokens=4096, temperature=0.3):
        assert "刑侦" in prompt
        return "## 办案流程\n先勘查现场，再走访摸排。"
    monkeypatch.setattr("llm.router.ModelRouter.invoke_with_web_search", fake_web)


@pytest.fixture
def no_web_llm(monkeypatch):
    """Provider does NOT support web search → LLMCallSite fallback."""
    async def fake_web(self, **kw):
        raise NotImplementedError("no web search")
    monkeypatch.setattr("llm.router.ModelRouter.invoke_with_web_search", fake_web)

    async def fake_invoke(self, *, prompt, system, **kw):
        return "## 模型内置知识\n仅供参考的流程描述。"
    monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)


class TestResearch:
    @pytest.mark.asyncio
    async def test_web_research_stores_labeled_knowledge_skill(
        self, db, web_llm,
    ) -> None:
        out = await kr.research_domain(db, "p1", "刑侦")
        assert out["used_web_search"] is True
        assert out["kind"] == "knowledge"
        # 机制3: AI编译 / 非权威 标注
        assert kr.NON_AUTHORITATIVE_LABEL in out["body_snippet"]
        assert "联网搜索编译" in out["body_snippet"]
        assert "办案流程" in out["body_snippet"]
        # 机制2: 直接入库，无需确认 — row exists immediately.
        assert skill_index.get_skill(db, out["skill_id"]) is not None

    @pytest.mark.asyncio
    async def test_no_web_fallback_carries_extra_caveat(
        self, db, no_web_llm,
    ) -> None:
        out = await kr.research_domain(db, "p1", "刑侦")
        assert out["used_web_search"] is False
        assert "不支持联网搜索" in out["body_snippet"]

    @pytest.mark.asyncio
    async def test_empty_domain_rejected(self, db, web_llm) -> None:
        with pytest.raises(ValueError):
            await kr.research_domain(db, "p1", "  ")


class TestSyncSurvival:
    @pytest.mark.asyncio
    async def test_registry_sync_preserves_knowledge_rows(
        self, db, web_llm, monkeypatch,
    ) -> None:
        """sync_from_registry prunes rows missing from disk — knowledge
        skills are DB-native and must survive (机制4 recall depends on it)."""
        out = await kr.research_domain(db, "p1", "刑侦")
        skill_index.reset_sync_cache()
        monkeypatch.setattr(skill_index, "_registry_snapshot", lambda: [])
        skill_index.sync_from_registry(db, force=True)
        assert skill_index.get_skill(db, out["skill_id"]) is not None


class TestAPI:
    @pytest.fixture
    def client(self, db, monkeypatch) -> TestClient:
        monkeypatch.setattr(
            "ui.backend.app.routers.knowledge_api._db", lambda: db,
        )
        from ui.backend.app.main import app
        return TestClient(app)

    @pytest.mark.asyncio
    async def test_list_edit_delete_roundtrip(
        self, db, web_llm, client,
    ) -> None:
        out = await kr.research_domain(db, "p1", "刑侦")
        sid = out["skill_id"]
        items = client.get("/api/knowledge/skills").json()["items"]
        assert [i["skill_id"] for i in items] == [sid]
        # 机制2: 用户可修改；标注被删时自动补回。
        r = client.put(f"/api/knowledge/skills/{sid}",
                       json={"body": "我自己改写的内容"})
        assert r.status_code == 200
        assert kr.NON_AUTHORITATIVE_LABEL in r.json()["body_snippet"]
        assert "我自己改写的内容" in r.json()["body_snippet"]
        assert client.delete(f"/api/knowledge/skills/{sid}").status_code == 200
        assert client.get("/api/knowledge/skills").json()["items"] == []

    def test_update_missing_404(self, client) -> None:
        r = client.put("/api/knowledge/skills/nope", json={"body": "x"})
        assert r.status_code == 404


class TestDomainDetectionNotification:
    @pytest.mark.asyncio
    async def test_batch_run_notifies_detected_domains(
        self, db, monkeypatch,
    ) -> None:
        from ui.backend.app.services.commit_pipeline.sub_tasks import (
            SubTaskContext, preference_analyzer,
        )
        import uuid as _uuid

        async def fake_llm(prompt, ctx):
            return {
                "preferences": [],
                "professional_domains": [
                    {"domain": "中医", "reason": "主角行医诊脉"},
                ],
            }
        monkeypatch.setattr(preference_analyzer, "_call_llm", fake_llm)
        with sqlite3.connect(db) as con:
            for i in range(1, 6):
                cid = f"ch{i}"
                con.execute(
                    "INSERT INTO chapters (chapter_id, project_id, "
                    "chapter_num, title) VALUES (?, 'p1', ?, ?)",
                    (cid, i, f"第{i}章"),
                )
                con.execute(
                    "INSERT INTO text_versions (version_id, chapter_id, "
                    "version_num, source, content) VALUES (?, ?, 1, 'ai', ?)",
                    (f"v_{_uuid.uuid4().hex[:8]}", cid, "草稿"),
                )
                con.execute(
                    "INSERT INTO text_versions (version_id, chapter_id, "
                    "version_num, source, content) VALUES (?, ?, 2, "
                    "'user_edit', ?)",
                    (f"v_{_uuid.uuid4().hex[:8]}", cid, "定稿"),
                )
            con.commit()
        ctx = SubTaskContext(project_id="p1", chapter_id="", db_path=db)
        out = await preference_analyzer.run(ctx)
        assert out["domains_detected"] == ["中医"]
        with sqlite3.connect(db) as con:
            row = con.execute(
                "SELECT title, notification_type FROM user_notifications "
                "WHERE notification_type='knowledge_research_suggested'",
            ).fetchone()
        assert row is not None
        assert "中医" in row[0]
