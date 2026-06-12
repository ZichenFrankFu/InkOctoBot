"""多作品共通点学习 (spec 参考数据库功能10) + skill 双模式 (Skill类·机制6-8)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.project_schema import ensure_creation_tables
from storage.reference_schema import ensure_reference_tables
from ui.backend.app.services import common_pattern_learning as cpl
from ui.backend.app.services import skill_index


_LLM_OUTPUT = {
    "skill_name": "黄金三章开局",
    "summary": "三部作品都在前三章完成金手指揭示与首次冲突",
    "common_patterns": [
        {"pattern": "第一章末必有钩子", "evidence": "三部作品第一章均以悬念收尾",
         "how_to_apply": "每章最后一段设置未解问题"},
        {"pattern": "", "evidence": "空模式应被丢弃"},
    ],
}


@pytest.fixture
def dbs(tmp_path: Path) -> tuple[str, str]:
    proj = tmp_path / "project.db"
    con = sqlite3.connect(str(proj))
    ensure_creation_tables(con)
    con.commit(); con.close()
    ref = tmp_path / "reference.db"
    con = sqlite3.connect(str(ref))
    ensure_reference_tables(con)
    for rid, title, outline in (
        ("r1", "斗破苍穹", '[{"event": "三年之约"}]'),
        ("r2", "凡人修仙传", '[{"event": "拜入七玄门"}]'),
        ("r3", "无数据作品", ""),
    ):
        con.execute(
            "INSERT INTO reference_works (ref_id, title, media_type, source, "
            "plot_outline_json) VALUES (?, ?, 'web_novel', 'manual', ?)",
            (rid, title, outline or None),
        )
    con.commit(); con.close()
    return str(proj), str(ref)


@pytest.fixture
def llm(monkeypatch):
    captured: dict = {}

    async def fake_invoke(self, *, prompt, system, **kw):
        captured["prompt"] = prompt
        return json.dumps(_LLM_OUTPUT, ensure_ascii=False)
    monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)
    return captured


class TestLearning:
    @pytest.mark.asyncio
    async def test_learn_stores_self_learned_skill(self, dbs, llm) -> None:
        proj, ref = dbs
        out = await cpl.learn_common_patterns(proj, ref, ["r1", "r2"], "plot")
        assert out["kind"] == "self_learned"
        assert out["display_name"] == "自学习：黄金三章开局"
        assert out["patterns_count"] == 1            # 空模式丢弃
        assert "第一章末必有钩子" in out["body_snippet"]
        assert "《斗破苍穹》" in out["body_snippet"]
        # Both works' material reached the single prompt.
        assert "三年之约" in llm["prompt"] and "拜入七玄门" in llm["prompt"]
        assert skill_index.get_skill(proj, out["skill_id"]) is not None

    @pytest.mark.asyncio
    async def test_requires_two_works(self, dbs, llm) -> None:
        proj, ref = dbs
        with pytest.raises(ValueError):
            await cpl.learn_common_patterns(proj, ref, ["r1"], "plot")

    @pytest.mark.asyncio
    async def test_no_dimension_data_rejected(self, dbs, llm) -> None:
        proj, ref = dbs
        with pytest.raises(ValueError):
            await cpl.learn_common_patterns(proj, ref, ["r3", "r3", "r3"], "plot")

    @pytest.mark.asyncio
    async def test_invalid_dimension(self, dbs, llm) -> None:
        proj, ref = dbs
        with pytest.raises(ValueError):
            await cpl.learn_common_patterns(proj, ref, ["r1", "r2"], "mood")

    @pytest.mark.asyncio
    async def test_sync_preserves_self_learned(self, dbs, llm, monkeypatch) -> None:
        proj, ref = dbs
        out = await cpl.learn_common_patterns(proj, ref, ["r1", "r2"], "plot")
        skill_index.reset_sync_cache()
        monkeypatch.setattr(skill_index, "_registry_snapshot", lambda: [])
        skill_index.sync_from_registry(proj, force=True)
        assert skill_index.get_skill(proj, out["skill_id"]) is not None


class TestAPI:
    @pytest.fixture
    def client(self, dbs, monkeypatch) -> TestClient:
        proj, ref = dbs
        monkeypatch.setattr(
            "ui.backend.app.routers.reference.learning._project_db",
            lambda: proj,
        )
        monkeypatch.setattr(
            "ui.backend.app.routers.reference.learning.reference_db_path",
            lambda: ref,
        )
        from ui.backend.app.main import app
        return TestClient(app)

    def test_learn_list_delete_roundtrip(self, client, llm) -> None:
        r = client.post("/api/references/works/learn-common",
                        json={"ref_ids": ["r1", "r2"], "dimension": "plot"})
        assert r.status_code == 200
        sid = r.json()["skill_id"]
        items = client.get("/api/references/learned-skills").json()["items"]
        assert [i["skill_id"] for i in items] == [sid]
        assert client.delete(
            f"/api/references/learned-skills/{sid}").status_code == 200
        assert client.get(
            "/api/references/learned-skills").json()["items"] == []

    def test_single_work_400(self, client, llm) -> None:
        r = client.post("/api/references/works/learn-common",
                        json={"ref_ids": ["r1"], "dimension": "plot"})
        assert r.status_code == 400


class TestSkillDualMode:
    def test_compress_strips_examples_keeps_rules(self) -> None:
        from ui.backend.app.services.prompt_context.loaders.skills import (
            compress_for_injection,
        )
        body = (
            "要求：对话使用短句\n"
            "示例：他说：「走。」\n"
            "```\n完整示例代码块\n```\n"
            "设计动机：模仿口语节奏\n"
            "禁止：连续三句超过30字\n"
        )
        out = compress_for_injection(body)
        assert "要求：对话使用短句" in out
        assert "禁止：连续三句超过30字" in out
        assert "示例" not in out
        assert "完整示例代码块" not in out
        assert "设计动机" not in out

    def test_native_capability_flag(self) -> None:
        from llm.skill_capabilities import supports_native_skills
        assert supports_native_skills("anthropic", "claude-sonnet-4-6")
        assert not supports_native_skills("openai", "gpt-4o")
        assert not supports_native_skills("ollama", "qwen2.5")

    def test_build_manifest_vs_injection(self, monkeypatch) -> None:
        from ui.backend.app.services.prompt_context.loaders import skills as sk
        pinned = [{
            "skill_id": "s1", "display_name": "短句对话", "kind": "learned",
            "description": "对话节奏技法",
            "body_snippet": "要求：短句\n示例：xxx",
        }]
        # Injection mode (no native support)
        monkeypatch.setattr(sk, "_native_mode_enabled", lambda: False)
        title, body = sk._build(pinned, [])
        assert "原生挂载" not in title
        assert "要求：短句" in body and "示例" not in body
        # Native mode → manifest only, no bodies
        monkeypatch.setattr(sk, "_native_mode_enabled", lambda: True)
        title2, body2 = sk._build(pinned, [])
        assert "原生挂载" in title2
        assert "短句对话" in body2
        assert "要求：短句" not in body2
