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

logger = logging.getLogger("inkoctobot.preprocessing.lora.trainer")


@dataclass
class LoRATrainConfig:
    """Configuration for a LoRA SFT training run."""

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

    logger.info("Loading base model: %s", config.base_model)

    # Quantisation config (optional 4-bit)
    quant_config = None
    if config.use_4bit:
        try:
            from transformers import BitsAndBytesConfig
            quant_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
            )
        except ImportError:
            logger.warning("bitsandbytes not available, training in full precision")

    tokenizer = AutoTokenizer.from_pretrained(
        config.base_model, trust_remote_code=True,
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        quantization_config=quant_config,
        device_map="auto",
        torch_dtype=torch.bfloat16,
        trust_remote_code=True,
    )

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

    # Training arguments
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
        bf16=torch.cuda.is_available(),
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
