"""Regression: ``/api/generation/context-manifest`` must always carry
the four list fields the bundled React editor reads ``.length`` from
without nullish guards.

Background: v3.1 dropped the ``writing_knowledge`` table + CRUD, but
the frontend bundle still does

    const skillCount = manifest.default_skills.length
        + manifest.learned_skills.length
        + manifest.writing_knowledge.length;

at ``EditorPage.tsx:2217``. Missing the field crashes the entire
editor with ``Cannot read properties of undefined (reading 'length')``
on first chapter open."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


class TestContextManifestShape(unittest.TestCase):

    def test_success_path_includes_all_four_arrays(self) -> None:
        """``creation_context_manifest`` must return the four keys the
        editor reads ``.length`` from."""
        from ui.backend.app.services.prompt_context import (
            creation_context_manifest,
        )
        manifest = creation_context_manifest(
            project_id="nonexistent_project_for_test",
            chapter_id="",
            chapter_num=1,
            mode="cluster",
        )
        for key in ("rag", "default_skills",
                    "learned_skills", "writing_knowledge"):
            self.assertIn(key, manifest, f"manifest missing {key!r}")
            self.assertIsInstance(manifest[key], list)

    def test_error_fallback_includes_all_four_arrays(self) -> None:
        """The endpoint's ``except`` branch in generation_api.py also
        needs to return the four arrays — used to omit ``writing_knowledge``
        and crash the same frontend code path."""
        from ui.backend.app.routers import generation_api
        with mock.patch(
            "ui.backend.app.routers._rag_context.creation_context_manifest",
            side_effect=RuntimeError("boom"),
        ):
            result = generation_api.context_manifest(
                project_id="x", chapter_id="y", chapter_num=1, mode="c",
            )
        for key in ("rag", "default_skills",
                    "learned_skills", "writing_knowledge"):
            self.assertIn(key, result, f"fallback missing {key!r}")
            self.assertEqual(result[key], [])


if __name__ == "__main__":
    unittest.main()
