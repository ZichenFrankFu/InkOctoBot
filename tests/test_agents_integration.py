"""
Integration tests for AI agents — requires a running Ollama instance.

Uses the DeepSeek-R1-Distill-Qwen-32B model via Ollama.

Setup:
  1. Install Ollama: https://ollama.ai
  2. Import the model:
     cd scripts/ollama_modelfiles/DeepSeek_R1_Qwen_32B
     ollama create deepseek-r1-qwen-32b -f Modelfile
  3. Run tests:
     python -m pytest tests/test_agents_integration.py -v

These tests are marked with @unittest.skipUnless to skip when Ollama is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.base import LLMMessage, ProviderConfig

# Model name — set by Modelfile
OLLAMA_MODEL = os.environ.get("INKOCTOBOT_TEST_MODEL", "deepseek-r1-qwen-32b")
OLLAMA_BASE = os.environ.get("INKOCTOBOT_OLLAMA_URL", "http://localhost:11434")


def _ollama_available() -> bool:
    """Check if Ollama is running and the model is available."""
    try:
        import httpx
        r = httpx.get(f"{OLLAMA_BASE}/api/tags", timeout=3)
        if r.status_code != 200:
            return False
        models = r.json().get("models", [])
        return any(OLLAMA_MODEL in m.get("name", "") for m in models)
    except Exception:
        return False


_HAS_OLLAMA = _ollama_available()


def _run(coro):
    """Helper to run async coroutine in tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _setup_test_env():
    """Create temp DB + project for integration tests."""
    from storage.project_schema import ensure_creation_tables
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = sqlite3.connect(db_path)
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects (project_id, title, genre) VALUES ('test_proj', '测试小说', '仙侠')")
    conn.commit()
    conn.close()
    return db_path


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestOllamaProvider(unittest.TestCase):
    """Direct Ollama provider test."""

    def test_generate(self):
        from llm.ollama_provider import OllamaProvider
        provider = OllamaProvider(ProviderConfig(
            provider_type="ollama",
            model_name=OLLAMA_MODEL,
            base_url=OLLAMA_BASE,
            max_tokens=200,
        ))
        messages = [
            LLMMessage(role="system", content="你是一个中文小说创作助手。"),
            LLMMessage(role="user", content="用一句话描述一个仙侠世界的日出。"),
        ]
        resp = _run(provider.generate(messages))
        self.assertTrue(len(resp.content) > 0)
        print(f"\n[OllamaProvider] Response ({resp.output_tokens} tokens): {resp.content[:200]}")

    def test_health_check(self):
        from llm.ollama_provider import OllamaProvider
        provider = OllamaProvider(ProviderConfig(
            provider_type="ollama",
            model_name=OLLAMA_MODEL,
            base_url=OLLAMA_BASE,
        ))
        healthy = _run(provider.health_check())
        self.assertTrue(healthy)


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestSceneDirectorIntegration(unittest.TestCase):
    """Scene Director generates scene plans."""

    def test_plan_scene(self):
        from llm.router import ModelRouter
        from agents.production.scene_director import SceneDirector

        router = ModelRouter()
        director = SceneDirector(router=router, project_id="test_proj")

        chapter_outline = (
            "张远在青云宗大殿参加新弟子入门仪式。"
            "长老宣布灵根觉醒测试，张远觉醒了稀有的木属性灵根。"
            "李清漪向他表示祝贺。"
        )

        result = _run(director.plan_scenes(
            chapter_outline,
            chapter_num=1,
            world_book="修仙世界，有宗门体系，修炼需要灵根。",
            character_list="张远（主角）、李清漪（师姐）、长老",
        ))
        self.assertIsInstance(result, (dict, list, str))
        print(f"\n[SceneDirector] Result type: {type(result).__name__}")
        if isinstance(result, dict):
            print(json.dumps(result, ensure_ascii=False, indent=2)[:500])
        else:
            print(str(result)[:500])


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestActorAgentIntegration(unittest.TestCase):
    """Actor Agent generates character performance."""

    def test_perform(self):
        from llm.router import ModelRouter
        from agents.production.actor_agent import ActorAgent

        router = ModelRouter()
        actor = ActorAgent(
            router=router,
            character_name="张远",
            character_card="性格：沉稳内敛，好奇心强。背景：出身普通村庄的少年。",
            project_id="test_proj",
            extra_system="你（张远）目前不知道李清漪的真实身份。",
        )

        scene_plan = {
            "summary": "张远在灵泉中觉醒灵根",
            "characters": ["张远", "长老"],
            "location": "大殿灵泉",
            "time": "清晨",
            "character_instructions": {
                "张远": {"emotional_state": "紧张期待", "secret_goal": ""},
            },
        }

        result = _run(actor.perform(scene_plan))
        self.assertTrue(len(result) > 0)
        print(f"\n[ActorAgent/张远] Performance ({len(result)} chars): {result[:300]}")


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestEditorWriterIntegration(unittest.TestCase):
    """Editor-Writer transforms performance records into chapter text."""

    def test_assemble(self):
        from llm.router import ModelRouter
        from agents.production.editor_writer import EditorWriter

        router = ModelRouter()
        editor = EditorWriter(router=router, project_id="test_proj")

        performance_log = """
=== 场景: 张远觉醒灵根 ===

[旁白]
大殿灵泉散发着淡淡的蓝光，空气中弥漫着灵气。

[张远的表演]
张远(紧张): *缓缓将双手探入灵泉* "希望一切顺利..."
  内心: 冰冷的泉水让他打了个激灵，随即一股暖流涌入全身
张远(震惊): *猛地睁开眼睛* "这是...灵根？"

[长老的表演]
长老(淡然): *微微点头* "木属性灵根，中品。不错。"
"""
        result = _run(editor.assemble(
            performance_log,
            pov="张远",
            style_instructions="第三人称叙述，短句为主",
        ))
        self.assertTrue(len(result) > 0)
        print(f"\n[EditorWriter] Chapter text ({len(result)} chars): {result[:400]}")


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestEvaluatorIntegration(unittest.TestCase):
    """Evaluator assesses chapter quality."""

    def test_evaluate(self):
        from llm.router import ModelRouter
        from agents.evaluation.evaluator import Evaluator

        router = ModelRouter()
        evaluator = Evaluator(router=router, project_id="test_proj")

        chapter_text = (
            "张远缓缓将双手探入灵泉。冰冷的泉水让他微微一颤，但紧接着，一股温暖的力量从指尖涌入全身。"
            "他猛地睁开眼，周围的世界仿佛变得不同了——空气中的灵气变得清晰可见。"
            "「木属性灵根，中品。」白发长老淡淡说道，语气波澜不惊。"
            "但张远注意到，长老的目光多停留了一瞬。那一瞬间的异样，让张远心中升起一丝疑惑。"
        )

        result = _run(evaluator.evaluate(
            chapter_text,
            constraints="世界观：修仙世界，需要灵根才能修炼",
        ))
        self.assertIsInstance(result, dict)
        print(f"\n[Evaluator] Result: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestStoryArchitectIntegration(unittest.TestCase):
    """Story Architect disambiguates and refines settings."""

    def test_refine(self):
        from llm.router import ModelRouter
        from agents.planner.story_architect import StoryArchitect

        router = ModelRouter()
        architect = StoryArchitect(router=router, project_id="test_proj")

        result = _run(architect.refine(
            user_input="我想写一个修仙小说，主角是个普通少年，被卷入宗门争斗。",
            world_book="",
            character_cards="",
        ))
        self.assertIsInstance(result, dict)
        print(f"\n[StoryArchitect] Result: {json.dumps(result, ensure_ascii=False, indent=2)[:500]}")


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestNarratorAgentIntegration(unittest.TestCase):
    """Narrator Agent generates atmosphere/environment."""

    def test_narrate(self):
        from llm.router import ModelRouter
        from agents.production.narrator_agent import NarratorAgent

        router = ModelRouter()
        narrator = NarratorAgent(router=router, project_id="test_proj")

        scene_plan = {
            "summary": "张远站在青云宗山门前",
            "location": "青云宗山门",
            "time": "黄昏",
            "characters": ["张远"],
        }

        result = _run(narrator.narrate(
            scene_plan,
            narrator_instructions="营造壮观震撼的氛围，突出仙境感",
        ))
        self.assertTrue(len(result) > 0)
        print(f"\n[NarratorAgent] Narration ({len(result)} chars): {result[:300]}")


@unittest.skipUnless(_HAS_OLLAMA, f"Ollama with {OLLAMA_MODEL} not available")
class TestFullPipelineIntegration(unittest.TestCase):
    """End-to-end: Scene Director → Actor → Narrator → Editor → Evaluator."""

    def test_mini_pipeline(self):
        from llm.router import ModelRouter
        from agents.production.scene_director import SceneDirector
        from agents.production.actor_agent import ActorAgent
        from agents.production.narrator_agent import NarratorAgent
        from agents.production.editor_writer import EditorWriter

        router = ModelRouter()

        # Step 1: Scene Director
        print("\n=== Step 1: Scene Director ===")
        director = SceneDirector(router=router, project_id="test_proj")
        plan = _run(director.plan_scenes(
            "张远觉醒灵根后，与李清漪一起离开大殿。",
            chapter_num=1,
        ))
        print(f"Plan: {str(plan)[:300]}")

        # Step 2: Actor performance
        print("\n=== Step 2: Actor Agent ===")
        actor = ActorAgent(
            router=router,
            character_name="张远",
            character_card="性格：沉稳好奇。身份：新弟子。",
            project_id="test_proj",
        )
        performance = _run(actor.perform(
            {"summary": "张远与李清漪离开大殿", "characters": ["张远", "李清漪"]},
        ))
        print(f"Performance ({len(performance)} chars): {performance[:200]}")

        # Step 3: Narrator
        print("\n=== Step 3: Narrator Agent ===")
        narrator = NarratorAgent(router=router, project_id="test_proj")
        narration = _run(narrator.narrate(
            {"summary": "离开大殿", "location": "大殿外廊", "time": "午后"},
        ))
        print(f"Narration ({len(narration)} chars): {narration[:200]}")

        # Step 4: Editor
        print("\n=== Step 4: Editor Writer ===")
        combined_log = f"[旁白]\n{narration}\n\n[张远的表演]\n{performance}"
        editor = EditorWriter(router=router, project_id="test_proj")
        chapter = _run(editor.assemble(combined_log, pov="张远"))
        print(f"Chapter ({len(chapter)} chars): {chapter[:300]}")

        self.assertTrue(len(chapter) > 0)
        print("\n=== Pipeline complete ===")


if __name__ == "__main__":
    unittest.main(verbosity=2)
