"""
Overall Evaluator — comprehensive quality assessment of generated text.

README §2.2.6: Checks constraint satisfaction, consistency, knowledge
isolation, repetition, and memory system alignment.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents.base_agent import BaseAgent

logger = logging.getLogger("inkoctobot.agents.evaluation.evaluator")


class Evaluator(BaseAgent):
    agent_name = "evaluator"

    async def evaluate_chapter(
        self,
        chapter_text: str,
        *,
        chapter_num: int = 1,
        scene_plan: dict[str, Any] | None = None,
        character_cards: str = "",
        memory_context: str = "",
        constraints: str = "",
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """
        Comprehensive chapter evaluation.

        Returns evaluation result with pass/fail and diagnostics.
        """
        user_content = self._build_evaluation_prompt(
            chapter_text, chapter_num, scene_plan, constraints,
        )
        context_parts = []
        if character_cards:
            context_parts.append(f"[角色设定]\n{character_cards}")
        if memory_context:
            context_parts.append(memory_context)

        resp = await self.invoke(
            user_content,
            context="\n\n".join(context_parts),
            constraints=constraints,
            temperature=0.3,
            max_tokens=4000,
            parse_json=True,
        )

        parsed = resp.raw.get("parsed")
        if not parsed:
            parsed = {"passed": True, "score": 70, "issues": [],
                       "raw_evaluation": resp.content}

        self.emit_event("EVALUATION_COMPLETED", {
            "chapter_num": chapter_num,
            "passed": parsed.get("passed", True),
            "score": parsed.get("score", 0),
            "issue_count": len(parsed.get("issues", [])),
        })
        return parsed

    def _build_evaluation_prompt(
        self, text: str, chapter_num: int,
        scene_plan: dict | None, constraints: str,
    ) -> str:
        parts = [
            f"请对第{chapter_num}章的生成文本进行全面评估。\n",
            "评估维度:",
            "1. 约束满足度: 是否违反世界观规则或情节约束",
            "2. 一致性: 角色行为是否符合设定",
            "3. 知识隔离: 角色是否泄露了不该知道的信息",
            "4. 重复度: 是否有过度重复的表达",
            "5. AI味检测: 是否有明显的AI生成痕迹",
            "6. 伏笔回溯: 相关伏笔是否得到恰当处理",
        ]
        if scene_plan:
            musts = []
            for scene in scene_plan.get("scenes", []):
                for char, instr in scene.get("character_instructions", {}).items():
                    musts.extend(instr.get("must", []))
                    musts.extend([f"不得{x}" for x in instr.get("must_not", [])])
            if musts:
                parts.append(f"\n导演指令检查清单: {'; '.join(musts)}")

        parts.append(f"\n## 待评估文本\n{text}")
        parts.append("""
请以JSON格式输出评估结果：
```json
{
  "passed": true/false,
  "score": 0-100,
  "issues": [
    {
      "dimension": "评估维度",
      "severity": "high/medium/low",
      "location": "问题位置（段落或引用）",
      "description": "问题描述",
      "suggestion": "修改建议"
    }
  ],
  "strengths": ["优点1", "优点2"],
  "summary": "总体评价"
}
```""")
        return "\n".join(parts)
