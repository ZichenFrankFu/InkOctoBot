"""
LoRA trainer — SFT fine-tuning via PEFT + bitsandbytes.

README Appendix A (Weaver paper): v1 implements SFT only;
Constitutional DPO is planned for a future iteration.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.reference_ingest.lora.trainer")


@dataclass
class LoRATrainConfig:
    """Configuration for a LoRA SFT training run.

    Use ``LoRATrainConfig.from_hardware()`` to populate sane defaults
    based on the detected device (CUDA / MPS / CPU).
    """

    base_model: str = "Qwen/Qwen2-1.5B"
    rank: int = 16
    alpha: int = 32
    target_modules: list[str] = field(
        default_factory=lambda: ["q_proj", "v_proj"]
    )
    learning_rate: float = 2e-4
    epochs: int = 3
    batch_size: int = 4
    max_length: int = 2048
    use_4bit: bool = True
    gradient_accumulation_steps: int = 4
    warmup_ratio: float = 0.05
    save_steps: int = 200
    logging_steps: int = 50

    # Device routing (v2.2 hardware detection).
    # device: 'auto' resolves at runtime via detect_hardware().
    # dtype:  'auto' picks bf16 / fp16 / fp32 per GPU capability.
    device: str = "auto"
    dtype: str = "auto"

    @classmethod
    def from_hardware(cls, *, base_model: str | None = None,
                      epochs: int | None = None) -> "LoRATrainConfig":
        """Construct a config with safe defaults from detect_hardware()."""
        from reference_ingest.lora.hardware import detect_hardware
        rep = detect_hardware()
        return cls(
            base_model=base_model or "Qwen/Qwen2-1.5B",
            epochs=epochs if epochs is not None else 3,
            batch_size=rep.recommended_batch_size,
            gradient_accumulation_steps=rep.recommended_grad_accum,
            use_4bit=rep.recommended_4bit,
            device=rep.recommended_device,
            dtype=rep.recommended_dtype,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_model": self.base_model,
            "rank": self.rank,
            "alpha": self.alpha,
            "target_modules": self.target_modules,
            "learning_rate": self.learning_rate,
            "epochs": self.epochs,
            "batch_size": self.batch_size,
            "max_length": self.max_length,
            "use_4bit": self.use_4bit,
            "gradient_accumulation_steps": self.gradient_accumulation_steps,
            "warmup_ratio": self.warmup_ratio,
            "device": self.device,
            "dtype": self.dtype,
        }


def train_lora(
    config: LoRATrainConfig,
    dataset_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Run LoRA SFT training.

    Parameters
    ----------
    config : LoRATrainConfig
        Training hyperparameters.
    dataset_path : str | Path
        Path to JSONL training data (from ``data_constructor.save_dataset``).
    output_dir : str | Path
        Directory to save the adapter weights.

    Returns
    -------
    dict
        ``{"adapter_path": str, "metrics": dict, "training_time_s": float}``

    Raises
    ------
    ImportError
        If ``torch``, ``transformers``, ``peft``, or ``datasets`` are missing.
    """
    try:
        import torch
        from datasets import load_dataset
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            TrainingArguments,
            Trainer,
            DataCollatorForSeq2Seq,
        )
        from peft import LoraConfig, TaskType, get_peft_model
    except ImportError as e:
        raise ImportError(
            "LoRA training requires: pip install torch transformers peft "
            "bitsandbytes datasets"
        ) from e

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = Path(dataset_path)
    t0 = time.perf_counter()

    # Resolve device + dtype from config (honor 'auto' via hardware detection)
    device, dtype_str = _resolve_device_dtype(config)
    torch_dtype = _torch_dtype_for(torch, dtype_str)
    logger.info(
        "Loading base model: %s (device=%s, dtype=%s, 4bit=%s)",
        config.base_model, device, dtype_str, config.use_4bit,
    )

    # Quantisation config (optional 4-bit; only meaningful on CUDA)
    quant_config = None
    if config.use_4bit and device == "cuda":
        try:
            from transformers import BitsAndBytesConfig
            quant_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch_dtype,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        except ImportError:
            logger.warning("bitsandbytes not available, training in full precision")
    elif config.use_4bit:
        logger.warning(
            "use_4bit requested but device=%s — 4-bit only supported on CUDA; "
            "falling back to %s precision",
            device, dtype_str,
        )

    tokenizer = AutoTokenizer.from_pretrained(
        config.base_model, trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # device_map: 'auto' lets HuggingFace pick; on MPS/CPU we explicitly
    # set the device so model lands in the right memory.
    device_map: Any = "auto" if device == "cuda" else None

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        quantization_config=quant_config,
        device_map=device_map,
        torch_dtype=torch_dtype,
        trust_remote_code=True,
    )
    if device == "mps":
        model = model.to("mps")
    elif device == "cpu":
        model = model.to("cpu")

    # LoRA adapter
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=config.rank,
        lora_alpha=config.alpha,
        target_modules=config.target_modules,
        lora_dropout=0.05,
        bias="none",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # Load dataset
    ds = load_dataset("json", data_files=str(dataset_path), split="train")

    def _tokenize(example: dict) -> dict:
        prompt = example.get("instruction", "") + "\n" + example.get("input", "")
        target = example.get("output", "")
        full = prompt + "\n" + target
        tok = tokenizer(
            full,
            max_length=config.max_length,
            truncation=True,
            padding=False,
        )
        tok["labels"] = tok["input_ids"].copy()
        return tok

    ds = ds.map(_tokenize, remove_columns=ds.column_names)

    # Training arguments — bf16/fp16 flags follow the resolved dtype.
    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=config.epochs,
        per_device_train_batch_size=config.batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        learning_rate=config.learning_rate,
        warmup_ratio=config.warmup_ratio,
        logging_steps=config.logging_steps,
        save_steps=config.save_steps,
        save_total_limit=2,
        bf16=(dtype_str == "bf16"),
        fp16=(dtype_str == "fp16"),
        use_mps_device=(device == "mps"),
        report_to="none",
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=ds,
        data_collator=DataCollatorForSeq2Seq(
            tokenizer=tokenizer, padding=True,
        ),
    )

    logger.info("Starting LoRA SFT training (%d epochs, %d samples)", config.epochs, len(ds))
    train_result = trainer.train()

    # Save adapter
    adapter_path = output_dir / "adapter"
    model.save_pretrained(str(adapter_path))
    tokenizer.save_pretrained(str(adapter_path))

    elapsed = time.perf_counter() - t0
    metrics = {
        "train_loss": train_result.training_loss,
        "train_samples": len(ds),
        "epochs": config.epochs,
    }

    logger.info("Training complete in %.1fs — adapter saved to %s", elapsed, adapter_path)

    return {
        "adapter_path": str(adapter_path),
        "metrics": metrics,
        "training_time_s": round(elapsed, 1),
    }


def _resolve_device_dtype(config: LoRATrainConfig) -> tuple[str, str]:
    """Return (device, dtype_str) honoring 'auto' values."""
    device = config.device
    dtype = config.dtype
    if device == "auto" or dtype == "auto":
        from reference_ingest.lora.hardware import detect_hardware
        rep = detect_hardware()
        if device == "auto":
            device = rep.recommended_device
        if dtype == "auto":
            dtype = rep.recommended_dtype
    if device not in {"cuda", "mps", "cpu"}:
        device = "cpu"
    if dtype not in {"bf16", "fp16", "fp32"}:
        dtype = "fp32"
    return device, dtype


def _torch_dtype_for(torch_mod, dtype_str: str):
    """Map dtype string → torch dtype."""
    if dtype_str == "bf16":
        return torch_mod.bfloat16
    if dtype_str == "fp16":
        return torch_mod.float16
    return torch_mod.float32
