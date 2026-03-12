"""
Mock LLM provider for test mode.

Returns fixed, deterministic responses so all features can be tested
without a real AI model connection.
"""
from __future__ import annotations

import random
from typing import Any, AsyncIterator

from models.base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig

# Pre-defined responses keyed by hints in the prompt
_RESPONSES: dict[str, str] = {
    "outline": (
        "## 大纲建议\n\n"
        "**核心冲突**：主角在星际考古中发现远古文明的秘密，"
        "却被神秘组织追杀。\n\n"
        "**第一幕**：废弃矿星上的意外发现\n"
        "**第二幕**：与特工联手追寻真相\n"
        "**第三幕**：星门激活，命运抉择\n\n"
        "建议在第一章用感官描写营造氛围，用主角的内心独白建立代入感。"
    ),
    "calibration": (
        "夜幕低垂，星光如碎银般洒落在废弃的矿星表面。"
        "李星河蹲在一处裸露的岩层前，指尖轻触那块散发着幽蓝微光的水晶。\n\n"
        "一瞬间，无数画面如潮水般涌入脑海——那是亿万年前的记忆残片，"
        "是一个文明在覆灭前最后的呐喊。他感受到了恐惧、不甘，"
        "以及一种超越时空的孤独。\n\n"
        "\"又来了……\"他低声呢喃，额头渗出细密的汗珠。"
        "每一次使用这种能力，都像是在窥视深渊——而深渊也在凝视着他。"
    ),
    "character": (
        "根据您的描述，我为角色补充以下设定：\n\n"
        "**性格描述**：外表沉稳冷静，实则内心充满对未知的好奇与热情。"
        "面对危险时会本能地保护同伴，但不善于表达感情。\n\n"
        "**背景故事**：出身普通家庭，童年一次意外中获得了感知古代遗物情感的能力。"
        "这种能力让他在考古学界崭露头角，但也让他饱受孤独之苦。\n\n"
        "**说话风格**：用词简洁精准，偶尔冒出考古学专业术语。"
        "紧张时会不自觉地摸后脑勺。"
    ),
    "consistency": (
        '{"result": "世界观设定基本一致", '
        '"issues": ["修炼体系等级名称在第三条和第五条中略有不同"]}'
    ),
    "quick": (
        "银河联邦历2847年，人类的星际版图已延伸至猎户座旋臂的边缘。\n\n"
        "在那些被遗忘的星域中，散落着无数远古文明的遗迹。"
        "它们沉默地矗立在荒凉的星球表面，等待着被发现，被解读，被唤醒。\n\n"
        "而李星河，就是那个被选中的解读者。"
    ),
}

_DEFAULT_RESPONSE = (
    "这是一段测试模式下的模拟AI回复。\n\n"
    "在正式使用时，请在设置页面配置真实的AI模型供应商（如 OpenAI、Anthropic、Ollama 等）。\n\n"
    "当前测试模式会返回固定内容，用于验证UI交互和数据流是否正常工作。"
)


def _pick_response(messages: list[LLMMessage]) -> str:
    """Pick a canned response based on message content."""
    combined = " ".join(m.content.lower() for m in messages)
    for key, resp in _RESPONSES.items():
        if key in combined:
            return resp
    # Check for Chinese keywords
    if any(kw in combined for kw in ["大纲", "outline", "情节", "故事线", "头脑风暴"]):
        return _RESPONSES["outline"]
    if any(kw in combined for kw in ["校准", "样本", "风格", "calibrat"]):
        return _RESPONSES["calibration"]
    if any(kw in combined for kw in ["角色", "人设", "character", "性格"]):
        return _RESPONSES["character"]
    if any(kw in combined for kw in ["一致性", "矛盾", "检查", "consistency"]):
        return _RESPONSES["consistency"]
    return _DEFAULT_RESPONSE


class MockProvider(BaseLLMProvider):
    """Deterministic mock provider for test mode."""

    def __init__(self, config: ProviderConfig | None = None):
        if config is None:
            config = ProviderConfig(
                provider_type="mock",
                model_name="mock-test-v1",
                max_tokens=4096,
                temperature=0.0,
            )
        super().__init__(config)

    async def generate(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        content = _pick_response(messages)
        # Simulate token counts
        input_tokens = sum(len(m.content) for m in messages)
        output_tokens = len(content)
        return LLMResponse(
            content=content,
            model="mock-test-v1",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason="stop",
            raw={"mock": True},
        )

    async def generate_stream(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        content = _pick_response(messages)
        # Stream in chunks
        chunk_size = 20
        for i in range(0, len(content), chunk_size):
            yield content[i : i + chunk_size]

    async def health_check(self) -> bool:
        return True

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return 0.0
