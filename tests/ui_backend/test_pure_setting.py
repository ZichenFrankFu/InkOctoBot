"""纯设定作品 (spec 2.2.2 / 6.2) — quick input, extraction preview, CRUD."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


_WIKI = "收容物SCP-173是一座混凝土雕像，无人直视时高速移动。基金会负责收容。"

_LLM_OUTPUT = {
    "settings": [
        {"category": "世界观", "title": "收容协议",
         "content": "基金会对异常物品分级收容"},
        {"category": "异常生物", "title": "SCP-173",
         "content": "未注视时高速移动的雕像"},   # 非法分类 → 其他
    ],
    "characters": [
        {"name": "O5议会成员", "role": "高层", "description": "基金会最高决策者"},
        {"name": "", "role": "x", "description": "无名 → 丢弃"},
    ],
    "setting_features": [
        {"title": "收容失效恐惧", "description": "秩序随时可能崩塌的母题"},
    ],
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    db = tmp_path / "reference.db"
    con = sqlite3.connect(str(db))
    from storage.reference_schema import ensure_reference_tables
    ensure_reference_tables(con)
    con.execute(
        "INSERT INTO reference_works (ref_id, title, media_type, source, "
        "structure_type) VALUES ('w1', 'SCP基金会', 'other', 'manual', "
        "'setting_collection')",
    )
    con.commit()
    con.close()
    monkeypatch.setattr(
        "ui.backend.app.routers.reference.pure_setting.reference_db_path",
        lambda: str(db),
    )
    from ui.backend.app.main import app
    return TestClient(app)


class TestCRUD:
    def test_get_defaults(self, client) -> None:
        r = client.get("/api/references/works/w1/pure-setting")
        assert r.status_code == 200
        body = r.json()
        assert body["structure_type"] == "setting_collection"
        assert body["settings"] == []
        assert body["static_characters"] == []

    def test_quick_input_roundtrip(self, client) -> None:
        r = client.put("/api/references/works/w1/pure-setting",
                       json={"quick_input_text": _WIKI})
        assert r.status_code == 200
        assert r.json()["quick_input_text"] == _WIKI

    def test_manual_lists_update(self, client) -> None:
        r = client.put("/api/references/works/w1/pure-setting", json={
            "settings": [{"category": "地理", "title": "Site-19",
                          "content": "主要收容站点"}],
            "static_characters": [{"name": "Bright博士", "role": "研究员",
                                   "description": "无法死亡"}],
            "setting_features": [{"title": "黑色幽默", "description": "..."}],
        })
        body = r.json()
        assert body["settings"][0]["title"] == "Site-19"
        assert body["static_characters"][0]["name"] == "Bright博士"
        assert body["setting_features"][0]["title"] == "黑色幽默"

    def test_structure_type_switch_validated(self, client) -> None:
        assert client.put("/api/references/works/w1/pure-setting",
                          json={"structure_type": "narrative"}).status_code == 200
        assert client.put("/api/references/works/w1/pure-setting",
                          json={"structure_type": "wiki"}).status_code == 400

    def test_missing_work_404(self, client) -> None:
        assert client.get(
            "/api/references/works/nope/pure-setting").status_code == 404


class TestExtraction:
    @pytest.fixture
    def llm(self, monkeypatch):
        async def fake_invoke(self, *, prompt, system, **kw):
            assert "SCP-173" in prompt
            return json.dumps(_LLM_OUTPUT, ensure_ascii=False)
        monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)

    def test_extract_preview_not_persisted(self, client, llm) -> None:
        client.put("/api/references/works/w1/pure-setting",
                   json={"quick_input_text": _WIKI})
        r = client.post("/api/references/works/w1/pure-setting/extract", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["preview"] is True
        # Illegal category coerced to 其他; nameless character dropped.
        cats = {s["title"]: s["category"] for s in body["settings"]}
        assert cats["SCP-173"] == "其他"
        assert [c["name"] for c in body["characters"]] == ["O5议会成员"]
        assert body["setting_features"][0]["title"] == "收容失效恐惧"
        # 预览不入库 (LLM交互·机制2) — stored lists still empty.
        stored = client.get("/api/references/works/w1/pure-setting").json()
        assert stored["settings"] == []

    def test_extract_then_commit_via_put(self, client, llm) -> None:
        """逐项入库: client prunes the preview then PUTs the kept items."""
        client.put("/api/references/works/w1/pure-setting",
                   json={"quick_input_text": _WIKI})
        preview = client.post(
            "/api/references/works/w1/pure-setting/extract", json={},
        ).json()
        kept = [s for s in preview["settings"] if s["category"] != "其他"]
        client.put("/api/references/works/w1/pure-setting", json={
            "settings": kept,
            "static_characters": preview["characters"],
            "setting_features": preview["setting_features"],
        })
        stored = client.get("/api/references/works/w1/pure-setting").json()
        assert [s["title"] for s in stored["settings"]] == ["收容协议"]
        assert stored["static_characters"][0]["name"] == "O5议会成员"

    def test_extract_without_input_400(self, client, llm) -> None:
        r = client.post("/api/references/works/w1/pure-setting/extract", json={})
        assert r.status_code == 400


class TestSegments:
    def test_segments_empty_text(self, client) -> None:
        r = client.get("/api/references/works/w1/pure-setting/segments")
        assert r.status_code == 200
        body = r.json()
        assert body["total_chunks"] == 0
        assert body["chunks"] == []

    def test_segments_short_text_single_chunk(self, client) -> None:
        client.put("/api/references/works/w1/pure-setting",
                   json={"quick_input_text": _WIKI})
        body = client.get(
            "/api/references/works/w1/pure-setting/segments").json()
        assert body["total_chunks"] == 1
        assert body["chunks"][0]["n_chars"] == len(_WIKI)

    def test_segments_long_text_multi_chunk(self, client) -> None:
        # 30000 chars across many paragraphs forces a split (max_chunk_chars=12000)
        long = "\n\n".join(f"段落 {i}：" + ("内容" * 400) for i in range(15))
        client.put("/api/references/works/w1/pure-setting",
                   json={"quick_input_text": long})
        body = client.get(
            "/api/references/works/w1/pure-setting/segments").json()
        assert body["total_chunks"] >= 2
        # Each chunk obeys the max
        assert all(c["n_chars"] <= body["max_chunk_chars"] for c in body["chunks"])

    def test_extract_specific_chunk(self, client, monkeypatch) -> None:
        captured: dict = {}

        async def fake_invoke(self, *, prompt, system, **kw):
            captured["prompt"] = prompt
            return json.dumps({
                "settings": [], "characters": [], "setting_features": [],
            })
        monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)

        long = "\n\n".join(f"段落 {i}：" + ("内容" * 400) for i in range(15))
        client.put("/api/references/works/w1/pure-setting",
                   json={"quick_input_text": long})
        r = client.post("/api/references/works/w1/pure-setting/extract",
                        json={"chunk_index": 1})
        assert r.status_code == 200
        assert r.json()["chunk_index"] == 1
        assert r.json()["total_chunks"] >= 2
        # Prompt should advertise chunk 2/N
        assert "第 2" in captured["prompt"]

    def test_extract_invalid_chunk_index_400(self, client, monkeypatch) -> None:
        async def fake_invoke(self, *, prompt, system, **kw):
            return "{}"
        monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)
        client.put("/api/references/works/w1/pure-setting",
                   json={"quick_input_text": _WIKI})
        r = client.post("/api/references/works/w1/pure-setting/extract",
                        json={"chunk_index": 5})
        assert r.status_code == 400


class TestPastedParse:
    def test_parse_paste_normalizes(self, client) -> None:
        raw = json.dumps({
            "settings": [
                {"category": "地理", "title": "Site-19", "content": "主收容站"},
                {"category": "未知类", "title": "X", "content": "x"},
            ],
            "characters": [{"name": "Bright博士", "role": "研究员", "description": "无法死亡"}],
            "setting_features": [{"title": "黑色幽默", "description": "..."}],
        })
        r = client.post(
            "/api/references/works/w1/pure-setting/parse-paste",
            json={"chunk_index": 0, "raw": raw},
        )
        assert r.status_code == 200
        body = r.json()
        cats = {s["title"]: s["category"] for s in body["settings"]}
        assert cats["Site-19"] == "地理"
        # 非法分类 normalized to 其他
        assert cats["X"] == "其他"
        assert body["characters"][0]["name"] == "Bright博士"

    def test_parse_paste_with_markdown_fence(self, client) -> None:
        raw = ("```json\n"
               + json.dumps({"settings": [], "characters": [], "setting_features": []})
               + "\n```")
        r = client.post(
            "/api/references/works/w1/pure-setting/parse-paste",
            json={"raw": raw},
        )
        assert r.status_code == 200

    def test_parse_paste_invalid_400(self, client) -> None:
        r = client.post(
            "/api/references/works/w1/pure-setting/parse-paste",
            json={"raw": "not json"},
        )
        assert r.status_code == 400

    def test_parse_paste_empty_400(self, client) -> None:
        r = client.post(
            "/api/references/works/w1/pure-setting/parse-paste",
            json={"raw": ""},
        )
        assert r.status_code == 400
