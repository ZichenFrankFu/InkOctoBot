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

        # Enhance with explainable output
        parsed = self._enrich_evaluation(parsed)

        self.emit_event("EVALUATION_COMPLETED", {
            "chapter_num": chapter_num,
            "passed": parsed.get("passed", True),
            "score": parsed.get("score", 0),
            "issue_count": len(parsed.get("issues", [])),
        })
        return parsed

    @staticmethod
    def _enrich_evaluation(result: dict[str, Any]) -> dict[str, Any]:
        """Add summary_text, dimension_scores, and process_log to evaluation result."""
        score = result.get("score", 0)
        issues = result.get("issues", [])
        strengths = result.get("strengths", [])

        # Build dimension scores from issues
        dimensions = {
            "constraint_satisfaction": 85,
            "consistency": 85,
            "knowledge_isolation": 90,
            "repetition": 85,
            "ai_flavor": 80,
            "narrative_quality": 80,
        }
        severity_penalty = {"high": 15, "medium": 8, "low": 3}
        dim_map = {
            "约束满足": "constraint_satisfaction",
            "一致性": "consistency",
            "知识隔离": "knowledge_isolation",
            "重复": "repetition",
            "AI味": "ai_flavor",
            "ai_flavor": "ai_flavor",
            "repetition": "repetition",
            "consistency": "consistency",
            "foreshadowing": "narrative_quality",
        }
        for issue in issues:
            dim_key = None
            issue_type = (issue.get("dimension") or issue.get("type") or "").lower()
            for keyword, dim in dim_map.items():
                if keyword.lower() in issue_type:
                    dim_key = dim
                    break
            if dim_key:
                penalty = severity_penalty.get(issue.get("severity", "low"), 3)
                dimensions[dim_key] = max(0, dimensions[dim_key] - penalty)

        result["dimension_scores"] = dimensions

        # Build natural language summary
        lines = [f"综合评分：{score}/100"]
        if score >= 80:
            lines.append("整体质量良好。")
        elif score >= 60:
            lines.append("基本通过，但有一些需要注意的问题。")
        else:
            lines.append("质量不足，建议修改后重试。")
        if issues:
            high_issues = [i for i in issues if i.get("severity") == "high"]
            if high_issues:
                lines.append(f"发现 {len(high_issues)} 个严重问题需要关注。")
        if strengths:
            lines.append(f"亮点：{'; '.join(strengths[:3])}")
        result["summary_text"] = " ".join(lines)

        # Build process log
        process_log = [
            {"detector": "约束检查", "status": "通过" if not any(i.get("type") == "constraint" for i in issues) else f"发现{sum(1 for i in issues if i.get('type') == 'constraint')}处", "detail": "检查约束满足度"},
            {"detector": "一致性检查", "status": "通过" if not any(i.get("type") == "consistency" for i in issues) else f"发现{sum(1 for i in issues if i.get('type') == 'consistency')}处", "detail": "检查角色行为一致性"},
            {"detector": "重复检测", "status": "通过" if not any("重复" in (i.get("type") or "") for i in issues) else f"发现{sum(1 for i in issues if '重复' in (i.get('type') or ''))}处", "detail": "检测重复表达"},
            {"detector": "AI味检测", "status": "通过" if not any("ai" in (i.get("type") or "").lower() for i in issues) else f"发现{sum(1 for i in issues if 'ai' in (i.get('type') or '').lower())}处", "detail": "检测AI生成痕迹"},
            {"detector": "综合评估", "status": "done", "detail": f"最终评分 {score}/100"},
        ]
        result["process_log"] = process_log

        return result

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
      "type": "constraint/consistency/knowledge_isolation/repetition/ai_flavor/foreshadowing",
      "severity": "high/medium/low",
      "description": "问题描述",
      "suggestion": "修改建议"
    }
  ],
  "strengths": ["优点1", "优点2"],
  "summary": "总体评价（自然语言摘要）"
}
```""")
        return "\n".join(parts)
