"""
Actor Agent — role-plays a single character in a scene.

README §2.2.4: Each actor is an independent instance with information
isolation.  Input: scene plan + character card + RAG context + constraints.
Output: semi-structured performance record (actions + dialogue + inner thoughts + atmosphere).
"""
from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from llm.base import LLMMessage, LLMResponse

logger = logging.getLogger("inkoctobot.agents.production.actor_agent")


class ActorAgent(BaseAgent):
    agent_name = "actor_agent"

    def __init__(self, *args, character_name: str = "", character_card: str = "", **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.character_name = character_name
        self.character_card = character_card

    async def perform(
        self,
        scene_plan: dict[str, Any],
        *,
        scene_context: str = "",
        knowledge_view: str = "",
        previous_beats: str = "",
        constraints: str = "",
    ) -> str:
        """Generate a performance record for this character in the scene."""
        instructions = scene_plan.get("character_instructions", {}).get(self.character_name, {})
        user_content = self._build_performance_prompt(
            scene_plan, instructions, previous_beats,
        )
        context_parts = []
        if self.character_card:
            context_parts.append(f"[角色卡 — {self.character_name}]\n{self.character_card}")
        if knowledge_view:
            context_parts.append(knowledge_view)
        if scene_context:
            context_parts.append(scene_context)
        context = "\n\n".join(context_parts)

        constraint_parts = []
        if constraints:
            constraint_parts.append(constraints)
        musts = instructions.get("must", [])
        must_nots = instructions.get("must_not", [])
        if musts:
            constraint_parts.append("必须: " + "; ".join(musts))
        if must_nots:
            constraint_parts.append("禁止: " + "; ".join(must_nots))

        resp = await self.invoke(
            user_content,
            context=context,
            constraints="\n".join(constraint_parts),
            temperature=0.8,
            max_tokens=3000,
        )
        return resp.content

    async def perform_scene(
        self,
        scene_plan: dict[str, Any],
        character_cards: dict[str, str],
        *,
        behavior_directives: dict[str, str] | None = None,
        knowledge_views: dict[str, str] | None = None,
        previous_scene_text: str = "",
        constraints: str = "",
    ) -> str:
        """One Actor call covering ALL on-stage characters (Actor·机制3).

        ``behavior_directives`` come from the code-level decision
        sampler (Actor·机制1/机制2) — the LLM dramatizes the given
        tendencies into concrete acts and dialogue, it does not pick
        the tendencies itself.
        """
        characters = scene_plan.get("characters", []) or []
        directives = behavior_directives or {}
        views = knowledge_views or {}

        context_parts: list[str] = []
        for name in characters:
            card = (character_cards.get(name) or "").strip()
            if card:
                context_parts.append(f"[角色卡 — {name}]\n{card}")
            view = (views.get(name) or "").strip()
            if view:
                context_parts.append(f"[{name} 的已知信息边界]\n{view}")
        if previous_scene_text:
            context_parts.append(f"[前序场景内容]\n{previous_scene_text}")

        constraint_parts: list[str] = []
        if constraints:
            constraint_parts.append(constraints)
        directive_lines = [directives[n] for n in characters if directives.get(n)]
        if directive_lines:
            constraint_parts.append(
                "本场行为指令（由决策采样器给定，必须遵守，不得自行更改各角色的行为倾向）：\n"
                + "\n".join(f"- {d}" for d in directive_lines)
            )
        instr_map = scene_plan.get("character_instructions", {}) or {}
        for name in characters:
            ins = instr_map.get(name) or {}
            musts = ins.get("must", [])
            must_nots = ins.get("must_not", [])
            if musts:
                constraint_parts.append(f"{name} 必须: " + "; ".join(musts))
            if must_nots:
                constraint_parts.append(f"{name} 禁止: " + "; ".join(must_nots))

        parts = [
            "你是本场戏的全体演员，需要同时扮演以下所有角色，并按场景节拍交织地输出他们的互动表演。",
            f"\n场景: {scene_plan.get('summary', '')}",
            f"地点: {scene_plan.get('location', '未知')}",
            f"时间: {scene_plan.get('time', '')}",
            f"出场角色: {', '.join(characters)}",
        ]
        for name in characters:
            ins = instr_map.get(name) or {}
            sub: list[str] = []
            if ins.get("emotional_state"):
                sub.append(f"情绪状态: {ins['emotional_state']}")
            if ins.get("secret_goal"):
                sub.append(f"秘密目标（不要直接说出）: {ins['secret_goal']}")
            if sub:
                parts.append(f"\n{name} — " + "；".join(sub))
        beats = scene_plan.get("beats", [])
        if beats:
            parts.append("\n本场景节拍: " + " / ".join(beats))
        parts.append("""
请以剧本格式输出本场全部角色的表演，按节拍顺序推进：

角色名
（情绪/神态描写）
动作描写。
"台词对话内容。"
（内心活动：该角色此刻的心理描写）

要求：
- 覆盖每个出场角色，戏份按其在本场的重要性分配
- 严格遵守各角色的行为指令与知识边界，角色不得说出或利用其不应知道的信息
- 台词用引号包裹，内心活动用括号标注
- 角色之间的对话与动作要相互衔接、互相回应
- 环境描写交给旁白，这里只写角色本身
""")
        resp = await self.invoke(
            "\n".join(parts),
            context="\n\n".join(context_parts),
            constraints="\n".join(constraint_parts),
            temperature=0.8,
            max_tokens=4000,
        )
        return resp.content

    async def check_consistency(
        self,
        performance_text: str,
        character_card: dict[str, Any] | str = "",
        scene_plan: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Check if a performance violates character settings.

        Returns list of warnings: [{character, message, severity}].
        Empty list means no violations detected.
        """
        card_str = character_card if isinstance(character_card, str) else (
            "\n".join(f"{k}: {v}" for k, v in character_card.items() if v) if character_card else ""
        )
        if not card_str and not scene_plan:
            return []

        prompt = f"""请检查以下角色表演是否违反了角色设定。

角色名：{self.character_name}
角色设定：
{card_str}

{"场景计划：" + str(scene_plan.get("summary", "")) if scene_plan else ""}

角色表演内容：
{performance_text[:3000]}

检查项：
1. 角色性格是否与设定矛盾
2. 角色行为是否违反禁止约束
3. 角色知识是否泄露了不应知道的信息
4. 角色语言风格是否符合设定

如果发现问题，以JSON数组格式输出：
[{{"character": "角色名", "message": "问题描述", "severity": "high/medium/low"}}]

如果没有问题，输出空数组：[]
"""
        try:
            resp = await self.invoke(prompt, temperature=0.3, max_tokens=1000)
            import json
            text = resp.content.strip()
            # Extract JSON array
            if "[" in text:
                json_str = text[text.index("["):text.rindex("]") + 1]
                warnings = json.loads(json_str)
                return [w for w in warnings if isinstance(w, dict) and w.get("message")]
            return []
        except Exception as e:
            logger.warning("Consistency check failed: %s", e)
            return []

    def _build_performance_prompt(
        self, scene_plan: dict, instructions: dict, previous_beats: str,
    ) -> str:
        parts = [
            f"你现在是「{self.character_name}」，请以第一人称视角进行角色扮演。",
            f"\n场景: {scene_plan.get('summary', '')}",
            f"地点: {scene_plan.get('location', '未知')}",
            f"时间: {scene_plan.get('time', '')}",
            f"在场人物: {', '.join(scene_plan.get('characters', []))}",
        ]
        if instructions.get("emotional_state"):
            parts.append(f"\n你当前的情绪状态: {instructions['emotional_state']}")
        if instructions.get("secret_goal"):
            parts.append(f"你的秘密目标（不要直接说出）: {instructions['secret_goal']}")
        beats = scene_plan.get("beats", [])
        if beats:
            parts.append(f"\n本场景节拍: {' → '.join(beats)}")
        if previous_beats:
            parts.append(f"\n前序表演:\n{previous_beats}")
        parts.append(f"""
请以电影剧本的方式，输出「{self.character_name}」在本场景中的表演。

格式参考（类似剧本格式）：

{self.character_name.upper()}
（情绪/神态描写）
动作描写。

          {self.character_name.upper()}
    "台词对话内容。"

（内心活动：角色此刻的心理描写）

要求：
- 只写{self.character_name}的内容，不要代替其他角色说话或行动
- 动作和神态用叙述方式描写（不要用*号标记）
- 台词用引号包裹
- 内心活动用括号标注
- 按场景节拍的先后顺序推进剧情
- 环境描写交给旁白，你只负责角色本身
""")
        return "\n".join(parts)
