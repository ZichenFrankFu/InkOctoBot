"""LoRA model provider — loads base model + PEFT adapter locally."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from .base import BaseLLMProvider, LLMMessage, LLMResponse, ProviderConfig

logger = logging.getLogger("inkoctobot.models.lora")


class LoRAProvider(BaseLLMProvider):

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._model = None
        self._tokenizer = None
        self._lora_path: str = config.extra.get("lora_path", "")
        self._base_model: str = config.extra.get("base_model", config.model_name)
        self._device: str = config.extra.get("device", "auto")

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from peft import PeftModel
            import torch
        except ImportError:
            raise ImportError("pip install transformers peft torch bitsandbytes")
        logger.info("Loading base=%s lora=%s", self._base_model, self._lora_path)
        self._tokenizer = AutoTokenizer.from_pretrained(self._base_model, trust_remote_code=True)
        base = AutoModelForCausalLM.from_pretrained(
            self._base_model, device_map=self._device,
            torch_dtype=torch.bfloat16, trust_remote_code=True,
        )
        self._model = PeftModel.from_pretrained(base, self._lora_path) if self._lora_path else base
        self._model.eval()

    async def generate(self, messages: list[LLMMessage], *, temperature: float | None = None,
                       max_tokens: int | None = None, stop: list[str] | None = None, **kw: Any) -> LLMResponse:
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None, self._sync_generate, messages, temperature, max_tokens,
        )

    def _sync_generate(self, messages: list[LLMMessage],
                       temperature: float | None, max_tokens: int | None) -> LLMResponse:
        import torch
        self._ensure_loaded()
        assert self._tokenizer is not None and self._model is not None
        prompt = "".join(f"<|{m.role}|>\n{m.content}\n" for m in messages) + "<|assistant|>\n"
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        input_len = inputs["input_ids"].shape[1]
        temp = temperature if temperature is not None else self.config.temperature
        gen_kw: dict[str, Any] = {"max_new_tokens": max_tokens or self.config.max_tokens, "do_sample": temp > 0}
        if temp > 0:
            gen_kw["temperature"] = temp
            gen_kw["top_p"] = self.config.top_p
        with torch.no_grad():
            output = self._model.generate(**inputs, **gen_kw)
        new_tokens = output[0][input_len:]
        return LLMResponse(content=self._tokenizer.decode(new_tokens, skip_special_tokens=True).strip(),
                           model=self._base_model, input_tokens=input_len,
                           output_tokens=len(new_tokens), finish_reason="stop")
