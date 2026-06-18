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

    def test_quick_input_legacy_string_wrapped_into_entry(self, client) -> None:
        """Legacy single-string PUT is wrapped into a single entry."""
        r = client.put("/api/references/works/w1/pure-setting",
                       json={"quick_input_text": _WIKI})
        assert r.status_code == 200
        body = r.json()
        assert len(body["raw_entries"]) == 1
        assert body["raw_entries"][0]["content"] == _WIKI

    def test_raw_entries_roundtrip(self, client) -> None:
        entries = [
            {"title": "条目 A", "content": "A 的内容"},
            {"title": "条目 B", "content": "B 的内容"},
        ]
        r = client.put("/api/references/works/w1/pure-setting",
                       json={"raw_entries": entries})
        assert r.status_code == 200
        assert r.json()["raw_entries"] == entries

    def test_raw_entries_empty_strings_dropped(self, client) -> None:
        r = client.put("/api/references/works/w1/pure-setting", json={
            "raw_entries": [
                {"title": "", "content": ""},  # dropped
                {"title": "保留", "content": "x"},
            ],
        })
        assert len(r.json()["raw_entries"]) == 1

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
        # Rendered text wraps the entry with "## 条目 1\n\n..." so chars
        # exceed _WIKI alone but the entry's body is preserved.
        assert _WIKI in body["chunks"][0]["preview"] or \
               body["chunks"][0]["n_chars"] > len(_WIKI)

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


class TestCombinedSources:
    """新行为：快捷输入 / 设定 / 角色三者任一非空即可提取，
    且 prompt 必须包含三类内容作为去重上下文。"""

    @pytest.fixture
    def captured_prompt(self, monkeypatch):
        captured: dict = {}

        async def fake_invoke(self, *, prompt, system, **kw):
            captured["prompt"] = prompt
            return json.dumps({
                "settings": [], "characters": [],
                "setting_features": [{"title": "测试母题", "description": "..."}],
            })
        monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)
        return captured

    def test_extract_with_only_settings(self, client, captured_prompt) -> None:
        """快捷输入为空，但已有设定 → 应可提取，prompt 含设定上下文。"""
        client.put("/api/references/works/w1/pure-setting", json={
            "settings": [{"category": "地理", "title": "Site-19",
                          "content": "主收容站点"}],
        })
        r = client.post("/api/references/works/w1/pure-setting/extract",
                        json={"chunk_index": 0})
        assert r.status_code == 200
        prompt = captured_prompt["prompt"]
        # 设定 Site-19 应出现在 prompt 里作为去重上下文
        assert "Site-19" in prompt
        # 应至少返回特征（基于已有设定）
        assert r.json()["setting_features"][0]["title"] == "测试母题"

    def test_extract_with_only_characters(self, client, captured_prompt) -> None:
        """快捷输入为空，但已有角色 → 应可提取，prompt 含角色上下文。"""
        client.put("/api/references/works/w1/pure-setting", json={
            "static_characters": [{"name": "Bright博士", "role": "研究员",
                                    "description": "无法死亡"}],
        })
        r = client.post("/api/references/works/w1/pure-setting/extract",
                        json={"chunk_index": 0})
        assert r.status_code == 200
        assert "Bright博士" in captured_prompt["prompt"]

    def test_extract_with_all_three_sources(self, client, captured_prompt) -> None:
        """三类输入都有时 prompt 必须同时包含。"""
        client.put("/api/references/works/w1/pure-setting", json={
            "quick_input_text": _WIKI,
            "settings": [{"category": "地理", "title": "Site-19", "content": "x"}],
            "static_characters": [{"name": "Bright博士", "role": "研究员",
                                    "description": "y"}],
        })
        r = client.post("/api/references/works/w1/pure-setting/extract",
                        json={"chunk_index": 0})
        assert r.status_code == 200
        prompt = captured_prompt["prompt"]
        assert "SCP-173" in prompt        # 快捷输入原文
        assert "Site-19" in prompt        # 已有设定
        assert "Bright博士" in prompt      # 已有角色

    def test_extract_with_nothing_400(self, client, captured_prompt) -> None:
        """三类输入全部为空 → 400。"""
        r = client.post("/api/references/works/w1/pure-setting/extract",
                        json={"chunk_index": 0})
        assert r.status_code == 400

    def test_segments_returns_existing_counts(self, client) -> None:
        client.put("/api/references/works/w1/pure-setting", json={
            "quick_input_text": _WIKI,
            "settings": [{"category": "地理", "title": "Site-19", "content": "x"}],
            "static_characters": [
                {"name": "A", "role": "x", "description": "y"},
                {"name": "B", "role": "x", "description": "y"},
            ],
        })
        body = client.get(
            "/api/references/works/w1/pure-setting/segments").json()
        assert body["existing_settings_count"] == 1
        assert body["existing_characters_count"] == 2
        assert body["can_extract"] is True

    def test_segments_empty_input_with_existing(self, client) -> None:
        """快捷输入为空但已有设定/角色 → 返回 1 个空段，can_extract=True。"""
        client.put("/api/references/works/w1/pure-setting", json={
            "settings": [{"category": "地理", "title": "X", "content": "y"}],
        })
        body = client.get(
            "/api/references/works/w1/pure-setting/segments").json()
        assert body["total_chunks"] == 1
        assert body["chunks"][0]["n_chars"] == 0
        assert body["can_extract"] is True

    def test_segments_truly_empty_not_extractable(self, client) -> None:
        body = client.get(
            "/api/references/works/w1/pure-setting/segments").json()
        assert body["can_extract"] is False
        assert body["total_chunks"] == 0


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


class TestFeatureCategories:
    """设定特征 now carries a category in {核心冲突, 高概念}; legacy 母题
    or other invalid values are coerced to 高概念."""

    def test_parse_paste_categorized_features(self, client) -> None:
        raw = json.dumps({
            "settings": [], "characters": [],
            "setting_features": [
                {"category": "核心冲突", "title": "秩序与失序",
                 "description": "..."},
                {"category": "高概念", "title": "未注视时移动的雕像",
                 "description": "..."},
                {"category": "母题",  # legacy — coerced to 高概念
                 "title": "黑色幽默", "description": "..."},
                {"category": "其他",  # invalid → coerced to 高概念
                 "title": "X", "description": "y"},
            ],
        })
        r = client.post(
            "/api/references/works/w1/pure-setting/parse-paste",
            json={"raw": raw},
        )
        assert r.status_code == 200
        cats = {f["title"]: f["category"] for f in r.json()["setting_features"]}
        assert cats["秩序与失序"] == "核心冲突"
        assert cats["未注视时移动的雕像"] == "高概念"
        assert cats["黑色幽默"] == "高概念"
        assert cats["X"] == "高概念"

    def test_parse_paste_missing_category_defaults(self, client) -> None:
        raw = json.dumps({
            "settings": [], "characters": [],
            "setting_features": [{"title": "foo", "description": "bar"}],
        })
        r = client.post(
            "/api/references/works/w1/pure-setting/parse-paste",
            json={"raw": raw},
        )
        assert r.json()["setting_features"][0]["category"] == "高概念"


class TestTranslation:
    @pytest.fixture
    def fake_llm(self, monkeypatch):
        async def fake_invoke(self, *, prompt, system, **kw):
            return f"[译文]{prompt[-30:]}"
        monkeypatch.setattr("llm.call_site.LLMCallSite.invoke", fake_invoke)

    def test_translate_entry(self, client, fake_llm) -> None:
        client.put("/api/references/works/w1/pure-setting", json={
            "raw_entries": [
                {"title": "A", "content": "Hello world."},
                {"title": "B", "content": "Foo bar."},
            ],
        })
        r = client.post(
            "/api/references/works/w1/pure-setting/translate",
            json={"entry_index": 1},
        )
        assert r.status_code == 200
        assert r.json()["entry_index"] == 1
        assert r.json()["translated"].startswith("[译文]")

    def test_translate_missing_index_400(self, client, fake_llm) -> None:
        r = client.post(
            "/api/references/works/w1/pure-setting/translate", json={},
        )
        assert r.status_code == 400

    def test_translate_out_of_range_400(self, client, fake_llm) -> None:
        client.put("/api/references/works/w1/pure-setting",
                   json={"raw_entries": [{"title": "A", "content": "x"}]})
        r = client.post(
            "/api/references/works/w1/pure-setting/translate",
            json={"entry_index": 5},
        )
        assert r.status_code == 400


class TestExportImport:
    def test_export_round_trip(self, client) -> None:
        # Set up everything
        client.put("/api/references/works/w1/pure-setting", json={
            "raw_entries": [
                {"title": "条目 A", "content": "A 内容\n第二行"},
                {"title": "条目 B", "content": "B 内容"},
            ],
            "settings": [
                {"category": "地理", "title": "Site-19",
                 "content": "主要收容站点"},
            ],
            "static_characters": [
                {"name": "Bright博士", "role": "研究员",
                 "description": "无法死亡"},
            ],
            "setting_features": [
                {"category": "核心冲突", "title": "秩序与失序",
                 "description": "..."},
                {"category": "高概念", "title": "异常实体",
                 "description": "..."},
            ],
        })
        r = client.get("/api/references/works/w1/pure-setting/export.txt")
        assert r.status_code == 200
        body = r.text
        assert "## 原始文本" in body
        assert "### 条目 A" in body
        assert "## 设定" in body
        assert "[地理] Site-19" in body
        assert "Bright博士 [研究员]" in body
        assert "[核心冲突] 秩序与失序" in body

    def test_import_replace_mode(self, client) -> None:
        from io import BytesIO
        # Existing data we want clobbered
        client.put("/api/references/works/w1/pure-setting", json={
            "settings": [{"category": "其他", "title": "OLD", "content": "x"}],
        })
        txt = (
            "# InkOctoBot 纯设定作品导出 v1\n"
            "# 标题: 测试\n"
            "\n## 原始文本\n"
            "### 条目 1\n"
            "导入的内容\n"
            "\n## 设定\n"
            "- [地理] 新条目: 描述\n"
            "\n## 角色\n"
            "- 新角色 [定位]: 说明\n"
            "\n## 设定特征\n"
            "- [核心冲突] 张力: 简介\n"
            "- [高概念] 钩子: 简介\n"
        )
        r = client.post(
            "/api/references/works/w1/pure-setting/import?mode=replace",
            files={"file": ("import.txt", BytesIO(txt.encode("utf-8")), "text/plain")},
        )
        assert r.status_code == 200
        body = client.get("/api/references/works/w1/pure-setting").json()
        # OLD setting is gone (replace mode)
        assert [s["title"] for s in body["settings"]] == ["新条目"]
        assert body["raw_entries"][0]["title"] == "条目 1"
        assert body["static_characters"][0]["name"] == "新角色"
        assert {f["category"] for f in body["setting_features"]} == {"核心冲突", "高概念"}

    def test_import_merge_mode_dedup(self, client) -> None:
        from io import BytesIO
        client.put("/api/references/works/w1/pure-setting", json={
            "settings": [{"category": "地理", "title": "Site-19", "content": "x"}],
        })
        txt = (
            "## 原始文本\n"
            "（无）\n"
            "## 设定\n"
            "- [地理] Site-19: 重复条目\n"
            "- [地理] 新条目: 新\n"
            "## 角色\n"
            "## 设定特征\n"
        )
        client.post(
            "/api/references/works/w1/pure-setting/import?mode=merge",
            files={"file": ("import.txt", BytesIO(txt.encode("utf-8")), "text/plain")},
        )
        body = client.get("/api/references/works/w1/pure-setting").json()
        # Site-19 deduped, 新条目 appended
        assert [s["title"] for s in body["settings"]] == ["Site-19", "新条目"]

    def test_import_invalid_format_400(self, client) -> None:
        from io import BytesIO
        r = client.post(
            "/api/references/works/w1/pure-setting/import",
            files={"file": ("bad.txt", BytesIO(b"random garbage"), "text/plain")},
        )
        assert r.status_code == 400
