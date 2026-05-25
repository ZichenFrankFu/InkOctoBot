#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
InkOctoBot — PyInstaller 打包前诊断测试
=========================================

放置位置: InkOctoBot/ui/test_pyinstaller_readiness.py
运行方式: cd InkOctoBot && python ui/test_pyinstaller_readiness.py

功能：
  - 在源码态模拟 PyInstaller 打包后会遇到的所有问题
  - 逐层检查: 路径 → import → 静态文件 → config → DB → FastAPI → launcher
  - 每项检查 PASS / FAIL / WARN，最终给出汇总报告
  - 不启动浏览器，不启动爬虫，不修改任何文件

用法:
  python ui/test_pyinstaller_readiness.py          # 跑全部检查
  python ui/test_pyinstaller_readiness.py --level 3 # 只跑到 Level 3
  python ui/test_pyinstaller_readiness.py --verbose  # 显示详细信息
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import os
import socket
import sqlite3
import sys
import textwrap
import time
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ------------------------------------------------------------------
# 0. 定位项目根目录（此文件在 ui/ 下，往上一层就是项目根）
# ------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent          # InkOctoBot/ui/
PROJECT_ROOT = SCRIPT_DIR.parent                      # InkOctoBot/

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ------------------------------------------------------------------
# Helper: 测试结果收集
# ------------------------------------------------------------------
class TestResult:
    def __init__(self):
        self.results: List[Tuple[str, str, str]] = []  # (status, name, detail)

    def ok(self, name: str, detail: str = ""):
        self.results.append(("PASS", name, detail))

    def fail(self, name: str, detail: str = ""):
        self.results.append(("FAIL", name, detail))

    def warn(self, name: str, detail: str = ""):
        self.results.append(("WARN", name, detail))

    @property
    def pass_count(self) -> int:
        return sum(1 for s, _, _ in self.results if s == "PASS")

    @property
    def fail_count(self) -> int:
        return sum(1 for s, _, _ in self.results if s == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for s, _, _ in self.results if s == "WARN")

    def summary(self) -> str:
        return f"PASS={self.pass_count}  WARN={self.warn_count}  FAIL={self.fail_count}"


R = TestResult()
VERBOSE = False


def _print(msg: str):
    print(msg)


def _detail(msg: str):
    if VERBOSE:
        for line in msg.strip().splitlines():
            print(f"       {line}")


def _section(title: str):
    _print(f"\n{'='*60}")
    _print(f"  {title}")
    _print(f"{'='*60}")


# ==================================================================
#  LEVEL 1: 项目结构和关键路径
# ==================================================================
def level1_paths():
    _section("Level 1: 项目结构 & 关键路径")

    # 1.1 项目根目录
    _print(f"\n  项目根目录: {PROJECT_ROOT}")
    if PROJECT_ROOT.exists():
        R.ok("项目根目录存在")
    else:
        R.fail("项目根目录不存在", str(PROJECT_ROOT))
        return

    # 1.2 关键文件检查
    critical_files = {
        "config.py":                    PROJECT_ROOT / "config.py",
        "launcher.py":                  PROJECT_ROOT / "launcher.py",
        "requirements.txt":             PROJECT_ROOT / "requirements.txt",
        "ui/backend/app/main.py":       PROJECT_ROOT / "ui" / "backend" / "app" / "main.py",
        "ui/backend/app/settings.py":   PROJECT_ROOT / "ui" / "backend" / "app" / "settings.py",
        "ui/backend/app/runner.py":     PROJECT_ROOT / "ui" / "backend" / "app" / "runner.py",
        "ui/backend/app/store.py":      PROJECT_ROOT / "ui" / "backend" / "app" / "store.py",
        "ui/backend/app/utils.py":      PROJECT_ROOT / "ui" / "backend" / "app" / "utils.py",
        "database/db_handler.py":       PROJECT_ROOT / "database" / "db_handler.py",
        "database/db_schema.py":        PROJECT_ROOT / "database" / "db_schema.py",
    }

    for label, p in critical_files.items():
        if p.exists():
            R.ok(f"文件存在: {label}")
        else:
            R.fail(f"文件缺失: {label}", str(p))

    # 1.3 前端 build 产物
    static_dir = PROJECT_ROOT / "ui" / "backend" / "app" / "static"
    index_html = static_dir / "index.html"
    assets_dir = static_dir / "assets"

    if index_html.exists():
        R.ok("前端 index.html 存在", str(index_html))
    else:
        R.fail(
            "前端 index.html 缺失 — 需要先 build 前端",
            "运行: cd ui/frontend && npm install && npm run build",
        )

    if assets_dir.exists() and any(assets_dir.iterdir()):
        R.ok("前端 assets/ 目录存在且非空")
    else:
        R.warn("前端 assets/ 目录为空或不存在", str(assets_dir))

    # 1.4 Python 包目录有 __init__.py
    packages = ["database", "tasks", "analysis",
                 "ui", "ui/backend", "ui/backend/app", "ui/backend/app/routers"]
    for pkg in packages:
        init_file = PROJECT_ROOT / pkg.replace("/", os.sep) / "__init__.py"
        if init_file.exists():
            R.ok(f"__init__.py 存在: {pkg}/")
        else:
            # 有些目录可能不需要 __init__.py（隐式命名空间包），给 warn
            R.warn(f"__init__.py 缺失: {pkg}/", f"PyInstaller 可能无法发现此包 → 需要 --collect-submodules 或 --hidden-import")

    # 1.5 .spec 文件
    spec_file = PROJECT_ROOT / "InkOctoBot.spec"
    if spec_file.exists():
        R.ok("InkOctoBot.spec 存在")
    else:
        R.warn("InkOctoBot.spec 不存在", "第一次打包会自动生成，或可手动创建")


# ==================================================================
#  LEVEL 2: Python import 检查（模拟 PyInstaller 的 hidden-import 需求）
# ==================================================================
def level2_imports():
    _section("Level 2: Python import 链检查")

    # 2.1 项目核心模块
    project_modules = [
        ("config",                              "config.py"),
        ("storage.db_schema",                  "database/db_schema.py"),
        ("storage.db_handler",                 "database/db_handler.py"),
        ("ui.backend.app.main",                 "ui/backend/app/main.py"),
        ("ui.backend.app.settings",             "ui/backend/app/settings.py"),
        ("ui.backend.app.runner",               "ui/backend/app/runner.py"),
        ("ui.backend.app.store",                "ui/backend/app/store.py"),
        ("ui.backend.app.utils",                "ui/backend/app/utils.py"),
        ("ui.backend.app.routers.config_api",   "ui/backend/app/routers/config_api.py"),
        ("ui.backend.app.routers.tasks_api",    "ui/backend/app/routers/tasks_api.py"),
        ("ui.backend.app.routers.reports_api",  "ui/backend/app/routers/reports_api.py"),
        ("ui.backend.app.routers.db_api",       "ui/backend/app/routers/db_api.py"),
    ]

    for mod_name, file_hint in project_modules:
        try:
            m = importlib.import_module(mod_name)
            R.ok(f"import {mod_name}")
        except Exception as e:
            R.fail(f"import {mod_name}", f"{type(e).__name__}: {e}")

    # 2.2 关键第三方库
    third_party = [
        "fastapi",
        "uvicorn",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan.on",
        "pydantic",
        "pydantic_settings",
        "bs4",
        "selenium",
        "pandas",
        "numpy",
        "matplotlib",
        "fontTools",
        "fontTools.ttLib",
    ]

    for mod_name in third_party:
        try:
            importlib.import_module(mod_name)
            R.ok(f"import {mod_name}")
        except ImportError:
            R.fail(f"import {mod_name}", "pip install 缺失，打包后也会缺")
        except Exception as e:
            R.warn(f"import {mod_name}", f"{type(e).__name__}: {e}")

    # 2.3 可选：pywebview
    try:
        import webview
        R.ok("import webview (pywebview)")
    except ImportError:
        R.fail("import webview (pywebview)", "pip install pywebview — launcher.py 必需")

    # 2.4 可选：undetected_chromedriver（仅外部 crawler repo 需要）
    try:
        import undetected_chromedriver
        R.ok("import undetected_chromedriver")
    except ImportError:
        R.warn("import undetected_chromedriver", "外部 crawler 仓库可能需要，当前仓库可跳过")


# ==================================================================
#  LEVEL 3: config.py 路径逻辑检查
# ==================================================================
def level3_config():
    _section("Level 3: config.py 路径 & 打包态模拟")

    try:
        import config
    except Exception as e:
        R.fail("import config", str(e))
        return

    # 3.1 BASE_DIR
    base_dir = getattr(config, "BASE_DIR", None)
    if not base_dir:
        R.fail("config.BASE_DIR 未定义")
        return

    base_path = Path(base_dir)
    _print(f"\n  config.BASE_DIR = {base_dir}")

    if base_path == PROJECT_ROOT or base_path.resolve() == PROJECT_ROOT.resolve():
        R.ok("config.BASE_DIR 指向项目根目录")
    else:
        R.warn("config.BASE_DIR 与项目根目录不一致",
               f"BASE_DIR={base_dir}\nPROJECT_ROOT={PROJECT_ROOT}")

    # 3.2 DATABASE path
    db_cfg = getattr(config, "DATABASE", {}) or {}
    db_path = db_cfg.get("path", "")
    _print(f"  config.DATABASE['path'] = {db_path}")

    if db_path:
        db_p = Path(db_path)
        if db_p.parent.exists():
            R.ok("DATABASE 目录存在", str(db_p.parent))
        else:
            R.warn("DATABASE 目录尚不存在（首次运行会自动创建）", str(db_p.parent))
    else:
        R.fail("config.DATABASE['path'] 为空")

    crawler_db_cfg = getattr(config, "CRAWLER_DATABASE", {}) or {}
    crawler_db_path = crawler_db_cfg.get("path", "")
    _print(f"  config.CRAWLER_DATABASE['path'] = {crawler_db_path}")
    if crawler_db_path:
        crawler_db_p = Path(crawler_db_path)
        if crawler_db_p.parent.exists():
            R.ok("CRAWLER_DATABASE 目录存在", str(crawler_db_p.parent))
        else:
            R.warn("CRAWLER_DATABASE 目录尚不存在（首次运行会自动创建）", str(crawler_db_p.parent))
    else:
        R.warn("config.CRAWLER_DATABASE['path'] 未设置", "将回退到 DATABASE['path']")

    # 3.3 OUTPUT_PATHS
    out_paths = getattr(config, "OUTPUT_PATHS", {}) or {}
    for key, path_str in out_paths.items():
        p = Path(path_str)
        if p.exists():
            R.ok(f"OUTPUT_PATHS['{key}'] 存在")
        else:
            R.warn(f"OUTPUT_PATHS['{key}'] 不存在（会自动创建）", str(p))

    # 3.4 ⚠️ 打包态关键检查：BASE_DIR 是否在临时目录
    _print(f"\n  [模拟打包态] 检查 BASE_DIR 是否有 _MEIPASS 兼容逻辑...")

    config_source = Path(config.__file__).read_text(encoding="utf-8", errors="replace")
    has_meipass_check = "_MEIPASS" in config_source

    if has_meipass_check:
        R.ok("config.py 包含 _MEIPASS 检查（打包态路径兼容）")
    else:
        R.fail(
            "config.py 缺少 _MEIPASS 检查 — 打包后 BASE_DIR 会指向临时目录",
            textwrap.dedent("""
            打包后 __file__ 指向 _MEIPASS/config.py（临时目录），
            BASE_DIR = os.path.dirname(os.path.abspath(__file__)) 将导致：
            - DATABASE['path'] 指向临时目录 → 数据每次启动都丢失
            - OUTPUT_PATHS 全部指向临时目录

            修复方案（在 config.py 顶部）：
                import sys, os
                if hasattr(sys, "_MEIPASS"):
                    if os.name == "nt":
                        BASE_DIR = os.path.join(
                            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                            "InkOctoBot"
                        )
                    else:
                        BASE_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "InkOctoBot")
                    os.makedirs(BASE_DIR, exist_ok=True)
                else:
                    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
            """).strip(),
        )


# ==================================================================
#  LEVEL 4: settings.py / runner.py 打包态兼容性
# ==================================================================
def level4_backend():
    _section("Level 4: UI Backend 打包态兼容性")

    # 4.1 settings.py — repo_root
    try:
        from ui.backend.app.settings import settings
        _print(f"\n  settings.repo_root = {settings.repo_root}")
        _print(f"  settings.python_bin = {settings.python_bin}")

        if settings.repo_root.exists() and (settings.repo_root / "config.py").exists():
            R.ok("settings.repo_root 有效（能找到 config.py）")
        else:
            R.fail("settings.repo_root 无效（找不到 config.py）",
                   str(settings.repo_root))

        # 检查是否有 _MEIPASS fallback
        settings_source = (PROJECT_ROOT / "ui" / "backend" / "app" / "settings.py").read_text(
            encoding="utf-8", errors="replace"
        )
        if "_MEIPASS" in settings_source:
            R.ok("settings.py 包含 _MEIPASS 兼容逻辑")
        else:
            R.fail(
                "settings.py 缺少 _MEIPASS 兼容逻辑",
                "打包后 Path(__file__).parents[3] 不再指向项目根目录",
            )

        # python_bin
        if settings.python_bin == sys.executable or settings.python_bin == "python":
            if settings.python_bin == "python":
                R.warn(
                    "settings.python_bin = 'python'",
                    "打包后没有独立的 python 命令，建议改为 sys.executable",
                )
            else:
                R.ok(f"settings.python_bin = sys.executable")
        else:
            R.warn(f"settings.python_bin = '{settings.python_bin}'")

    except Exception as e:
        R.fail("导入 settings 失败", f"{type(e).__name__}: {e}")

    # 4.2 runner.py — cwd 检查
    try:
        runner_source = (PROJECT_ROOT / "ui" / "backend" / "app" / "runner.py").read_text(
            encoding="utf-8", errors="replace"
        )
        if "if False" in runner_source:
            R.fail(
                "runner.py 包含 'if False' — cwd 永远为 None",
                "子进程工作目录不正确，UI 启动爬虫会失败",
            )
        else:
            R.ok("runner.py 无 'if False' 问题")
    except Exception as e:
        R.fail("读取 runner.py 失败", str(e))

    # 4.3 main.py — STATIC_DIR 检查
    try:
        main_source = (PROJECT_ROOT / "ui" / "backend" / "app" / "main.py").read_text(
            encoding="utf-8", errors="replace"
        )
        if "_MEIPASS" in main_source:
            R.ok("ui/backend/app/main.py 包含 _MEIPASS 兼容逻辑（STATIC_DIR）")
        else:
            R.warn(
                "ui/backend/app/main.py 缺少 _MEIPASS 兼容逻辑",
                "打包后 STATIC_DIR 可能找不到前端文件",
            )
    except Exception as e:
        R.fail("读取 ui/backend/app/main.py 失败", str(e))

    # 4.4 launcher.py 检查
    try:
        launcher_source = (PROJECT_ROOT / "launcher.py").read_text(
            encoding="utf-8", errors="replace"
        )

        # 检查重复 import
        import_count = launcher_source.count("import uvicorn")
        if import_count > 1:
            R.warn("launcher.py 中 'import uvicorn' 出现 {import_count} 次（有重复 import）")
        else:
            R.ok("launcher.py 无重复 import")

        # 检查硬编码旧名
        if "webnovel_trends" in launcher_source.lower():
            R.warn("launcher.py 仍包含 'webnovel_trends' 字符串（可能未完全重命名）")
        else:
            R.ok("launcher.py 已完成 InkOctoBot 重命名")

        # 检查 _MEIPASS
        if "_MEIPASS" in launcher_source:
            R.ok("launcher.py 包含 _MEIPASS 路径检测")
        else:
            R.warn("launcher.py 缺少 _MEIPASS 路径检测",
                   "打包后 Path(__file__) 不再可用，需要 sys._MEIPASS fallback")

    except Exception as e:
        R.fail("读取 launcher.py 失败", str(e))


# ==================================================================
#  LEVEL 5: FastAPI 应用能否启动（不开端口，只验证 app 对象）
# ==================================================================
def level5_fastapi():
    _section("Level 5: FastAPI app 初始化")

    try:
        from ui.backend.app.main import app
        R.ok("FastAPI app 对象创建成功")

        # 检查 routers
        route_paths = [r.path for r in app.routes]
        expected_prefixes = ["/api/config", "/api/tasks", "/api/reports", "/api/db", "/health"]

        for prefix in expected_prefixes:
            matched = any(rp.startswith(prefix) for rp in route_paths)
            if matched:
                R.ok(f"路由已注册: {prefix}*")
            else:
                R.fail(f"路由未注册: {prefix}*")

    except Exception as e:
        R.fail("FastAPI app 初始化失败", f"{type(e).__name__}: {e}")


# ==================================================================
#  LEVEL 6: 数据库 schema 初始化测试
# ==================================================================
def level6_database():
    _section("Level 6: 数据库 schema 初始化（内存 DB）")

    try:
        from storage.db_schema import create_all

        conn = sqlite3.connect(":memory:")
        create_all(conn)
        R.ok("create_all() 在内存 DB 上执行成功")

        # 验证表是否存在
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()]

        expected = [
            "first_n_chapters", "novel_tag_map", "novel_titles",
            "novels", "rank_entries", "rank_lists", "rank_snapshots", "tags",
        ]
        for t in expected:
            if t in tables:
                R.ok(f"表已创建: {t}")
            else:
                R.fail(f"表缺失: {t}")

        conn.close()

    except Exception as e:
        R.fail("数据库 schema 初始化失败", f"{type(e).__name__}: {e}")


# ==================================================================
#  LEVEL 7: 端口可用性 & 完整 launcher 模拟（可选）
# ==================================================================
def level7_launcher_smoke():
    _section("Level 7: Launcher 冒烟测试（启动 FastAPI 3秒后关闭）")

    host, port = "127.0.0.1", 18713  # 用不同端口避免冲突

    # 检查端口是否空闲
    try:
        with socket.create_connection((host, port), timeout=0.5):
            R.warn(f"端口 {port} 被占用，跳过冒烟测试")
            return
    except (OSError, ConnectionRefusedError):
        pass  # 端口空闲，可以继续

    server_started = threading.Event()
    server_error: List[str] = []

    def _run_server():
        try:
            import uvicorn
            from ui.backend.app.main import app
            # 用线程跑一个短暂的 uvicorn
            config = uvicorn.Config(app, host=host, port=port, log_level="error")
            server = uvicorn.Server(config)
            server_started.set()
            server.run()
        except Exception as e:
            server_error.append(f"{type(e).__name__}: {e}")
            server_started.set()

    th = threading.Thread(target=_run_server, daemon=True)
    th.start()
    server_started.wait(timeout=5)

    if server_error:
        R.fail("FastAPI 服务器启动失败", server_error[0])
        return

    # 等待端口可连接
    connected = False
    for _ in range(20):
        try:
            with socket.create_connection((host, port), timeout=0.3):
                connected = True
                break
        except OSError:
            time.sleep(0.25)

    if not connected:
        R.fail(f"FastAPI 服务器未在 {host}:{port} 上响应")
        return

    R.ok(f"FastAPI 服务器在 {host}:{port} 上成功监听")

    # 简单 HTTP 请求测试
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"http://{host}:{port}/health", timeout=3)
        data = resp.read().decode()
        if '"ok"' in data and "true" in data.lower():
            R.ok("/health 端点返回正常")
        else:
            R.warn("/health 返回异常内容", data[:200])
    except Exception as e:
        R.fail("/health 请求失败", str(e))

    try:
        resp = urllib.request.urlopen(f"http://{host}:{port}/api/config/schema", timeout=3)
        data = resp.read().decode()
        if "rank_keys" in data:
            R.ok("/api/config/schema 返回正常")
        else:
            R.warn("/api/config/schema 返回格式异常", data[:200])
    except Exception as e:
        R.fail("/api/config/schema 请求失败", str(e))

    # 不主动 shutdown（daemon thread 会随主进程退出）
    _print("\n  (测试服务器将随脚本退出自动关闭)")


# ==================================================================
#  主入口
# ==================================================================
def main():
    global VERBOSE

    parser = argparse.ArgumentParser(description="InkOctoBot PyInstaller 打包前诊断")
    parser.add_argument("--level", type=int, default=7,
                        help="最高执行到哪个 Level (1-7，默认全部)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="显示详细信息")
    args = parser.parse_args()
    VERBOSE = args.verbose

    _print("=" * 60)
    _print("  InkOctoBot — PyInstaller 打包前诊断测试")
    _print(f"  Python: {sys.version}")
    _print(f"  项目根: {PROJECT_ROOT}")
    _print(f"  时间:   {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _print("=" * 60)

    levels = [
        (1, level1_paths),
        (2, level2_imports),
        (3, level3_config),
        (4, level4_backend),
        (5, level5_fastapi),
        (6, level6_database),
        (7, level7_launcher_smoke),
    ]

    for lvl, fn in levels:
        if lvl > args.level:
            break
        try:
            fn()
        except Exception as e:
            R.fail(f"Level {lvl} 执行异常", f"{type(e).__name__}: {e}")

    # ------ 汇总 ------
    _print(f"\n{'='*60}")
    _print(f"  汇总: {R.summary()}")
    _print(f"{'='*60}\n")

    # 逐条打印失败和警告
    fails = [(n, d) for s, n, d in R.results if s == "FAIL"]
    warns = [(n, d) for s, n, d in R.results if s == "WARN"]

    if fails:
        _print("❌ FAIL 项（必须修复后再打包）：")
        for i, (name, detail) in enumerate(fails, 1):
            _print(f"  {i}. {name}")
            if detail:
                for line in detail.strip().splitlines():
                    _print(f"     {line}")
        _print("")

    if warns:
        _print("⚠️  WARN 项（建议修复）：")
        for i, (name, detail) in enumerate(warns, 1):
            _print(f"  {i}. {name}")
            if detail:
                for line in detail.strip().splitlines():
                    _print(f"     {line}")
        _print("")

    if not fails:
        _print("✅ 所有必须项通过！可以尝试 pyinstaller 打包。")
        _print("   下一步: pyinstaller --noconfirm --console InkOctoBot.spec")
    else:
        _print(f"🚫 有 {len(fails)} 项必须修复后再打包。")

    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()