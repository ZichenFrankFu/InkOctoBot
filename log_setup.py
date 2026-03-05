# log_setup.py
"""
InkOctoBot 全局日志配置。
在项目入口（main.py / launcher.py）的最开头调用一次 setup_logging()。
其他所有模块只需 logging.getLogger(__name__) 即可。
"""
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def setup_logging(
    *,
    log_dir: str | Path = "outputs/logs",
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    log_filename: str | None = None,
) -> None:
    """
    一次性配置 root logger。

    - 文件: DEBUG 级别，记录一切（给开发者事后排查）
    - 控制台: INFO 级别（给用户看进度）
    - UI 模式下控制台可以设为 WARNING（日志通过 API 拉取）
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    if log_filename is None:
        log_filename = f"inkoctobot_{datetime.now():%Y%m%d_%H%M%S}.log"

    log_path = log_dir / log_filename

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)   # root 接受一切，由 handler 过滤
    root.handlers.clear()          # 防止重复

    # ---- 文件 handler: 记录一切 ----
    fmt_file = logging.Formatter(
        "%(asctime)s | %(name)-30s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(file_level)
    fh.setFormatter(fmt_file)
    root.addHandler(fh)

    # ---- 控制台 handler: 只显示 INFO+ ----
    fmt_console = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(console_level)
    ch.setFormatter(fmt_console)
    root.addHandler(ch)

    # ---- 降低第三方库的日志噪音 ----
    for noisy in ["urllib3", "selenium", "undetected_chromedriver", "httpcore", "httpx", "uvicorn.access"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger("inkoctobot").info(f"日志初始化完成: {log_path}")