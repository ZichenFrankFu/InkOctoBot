# ui/backend/app/runtime_paths.py
"""
PyInstaller 兼容的路径工具。
打包态: 代码在 _MEIPASS，用户数据在 LOCALAPPDATA/InkOctoBot
源码态: 直接用 repo_root/outputs
"""
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否在 PyInstaller 打包环境中运行"""
    return hasattr(sys, "_MEIPASS")


def bundle_dir() -> Path:
    """PyInstaller 解包目录（代码所在位置）"""
    if is_frozen():
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def app_home() -> Path:
    """用户数据根目录（outputs/logs/db 的父目录）"""
    if is_frozen():
        # 打包态: 用户数据放在 OS 标准位置
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        elif sys.platform == "darwin":
            base = str(Path.home() / "Library" / "Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        home = Path(base) / "InkOctoBot"
    else:
        # 源码态: 直接用项目根目录
        home = Path(__file__).resolve().parents[3]
    home.mkdir(parents=True, exist_ok=True)
    return home


def outputs_dir() -> Path:
    d = app_home() / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = outputs_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_dir() -> Path:
    d = outputs_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def reports_dir() -> Path:
    d = outputs_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d