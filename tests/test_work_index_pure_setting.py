"""Pure-setting (setting_collection) handling in the L1/L2/L3 work indexer.

Regression: building L1 for a 纯设定 work used to silently index zero items
(the indexer only read narrative-shaped JSON columns), while L2/L3 returned
``status=done`` with 0 items, misleading the UI into showing 「已完成」on a
work that has no chapter text at all. After the fix:
  - L1 also embeds static_characters_json + setting_features_json
  - L2/L3 return ``status=not_applicable`` with ``skipped=pure_setting``
  - index_all skips L2/L3 entirely for setting_collection works
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from knowledge.work_index import WorkIndexer


class _FakeEmb:
    name = "fake"

    async def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


class _FakeVS:
    def __init__(self) -> None:
        self.items: list[dict] = []

    def add_batch_with_embeddings(self, ids, texts, vecs, metas):
        for i, t, m in zip(ids, texts, metas):
            self.items.append({"id": i, "text": t, "meta": m})

    def delete_where(self, _where):
        self.items = []

    def get_many(self, _ids):
        return set()


@pytest.fixture
def pure_setting_db(tmp_path: Path) -> str:
    db_path = tmp_path / "ref.db"
    from storage.reference_schema import ensure_reference_tables

    con = sqlite3.connect(str(db_path))
    ensure_reference_tables(con)
    con.execute(
        "INSERT INTO reference_works ("
        "ref_id, title, media_type, source, structure_type, "
        "settings_json, static_characters_json, setting_features_json"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "w1", "SCP-001", "other", "manual", "setting_collection",
            json.dumps([{"category": "其他", "title": "收容协议",
                         "content": "SCP 必须保存在..."}]),
            json.dumps([{"name": "O5-1", "role": "议会成员",
                         "description": "负责审批..."}]),
            json.dumps([{"category": "高概念", "title": "基金会的主旨",
                         "description": "控制 收容 保护"}]),
        ),
    )
    con.commit()
    con.close()
    return str(db_path)


def test_l1_indexes_pure_setting_fields(pure_setting_db: str) -> None:
    vs = _FakeVS()
    indexer = WorkIndexer(pure_setting_db, vs, _FakeEmb())
    result = asyncio.run(indexer.index_l1("w1"))
    assert result["embedded"] == 3, result
    sources = sorted(it["meta"]["source_type"] for it in vs.items)
    assert sources == ["character", "setting", "setting_feature"]
    # The character was pulled from static_characters_json, not the legacy
    # extracted_characters_json (which is null here).
    char_item = next(it for it in vs.items
                     if it["meta"]["source_type"] == "character")
    assert char_item["meta"].get("char_kind") == "static"
    assert "O5-1" in char_item["text"]


def test_l2_and_l3_skip_pure_setting(pure_setting_db: str) -> None:
    vs = _FakeVS()
    indexer = WorkIndexer(pure_setting_db, vs, _FakeEmb())
    r2 = asyncio.run(indexer.index_l2("w1"))
    r3 = asyncio.run(indexer.index_l3("w1"))
    assert r2 == {"level": "L2", "embedded": 0, "skipped": "pure_setting"}
    assert r3 == {"level": "L3", "embedded": 0, "skipped": "pure_setting"}
    # Progress row should report not_applicable, not done — the UI uses this
    # to hide the chip instead of showing "已完成 (0)".
    progress = {row["level"]: row["status"] for row in indexer.get_progress("w1")}
    assert progress.get("L2") == "not_applicable"
    assert progress.get("L3") == "not_applicable"


def test_index_all_skips_l3_for_pure_setting(pure_setting_db: str) -> None:
    vs = _FakeVS()
    indexer = WorkIndexer(pure_setting_db, vs, _FakeEmb())
    result = asyncio.run(indexer.index_all("w1", include_l3=True))
    assert result["L1"]["embedded"] == 3
    assert result["L2"]["skipped"] == "pure_setting"
    assert result["L3"]["skipped"] == "pure_setting"
    # L3 must NOT have run the chapter pipeline (no file_path on this work).
    progress = {row["level"]: row["status"] for row in indexer.get_progress("w1")}
    assert progress["L1"] == "done"
    assert progress["L2"] == "not_applicable"
    assert progress["L3"] == "not_applicable"


def test_narrative_work_still_uses_legacy_chars(tmp_path: Path) -> None:
    """A regular narrative work without static_characters_json must still get
    its `extracted_characters_json` characters indexed at L1 — the merge
    must not break the original code path."""
    db_path = tmp_path / "narr.db"
    from storage.reference_schema import ensure_reference_tables

    con = sqlite3.connect(str(db_path))
    ensure_reference_tables(con)
    con.execute(
        "INSERT INTO reference_works ("
        "ref_id, title, media_type, source, structure_type, "
        "extracted_characters_json"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (
            "n1", "诛仙", "web_novel", "manual", "narrative",
            json.dumps([{"name": "张小凡", "intro": "草庙村少年"}]),
        ),
    )
    con.commit()
    con.close()

    vs = _FakeVS()
    indexer = WorkIndexer(str(db_path), vs, _FakeEmb())
    asyncio.run(indexer.index_l1("n1"))
    char_items = [it for it in vs.items
                  if it["meta"]["source_type"] == "character"]
    assert len(char_items) == 1
    assert char_items[0]["meta"]["char_kind"] == "narrative"
    assert "张小凡" in char_items[0]["text"]
