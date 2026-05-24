# InkOctoBot.spec
# 用法: pyinstaller InkOctoBot.spec

import os
from pathlib import Path

block_cipher = None
ROOT = os.path.abspath(".")

a = Analysis(
    ["launcher.py"],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # ---- 前端 build 产物 ----
        ("ui/backend/app/static", "ui/backend/app/static"),

        # ---- 应用图标（窗口 / 任务栏图标）----
        ("assets", "assets"),

        # ---- 项目配置 ----
        ("config.py", "."),

        # ---- 所有 Python 源码包（PyInstaller 不一定能自动发现动态 import）----
        ("database", "database"),
        ("tasks", "tasks"),
        ("analysis", "analysis"),
        ("ui/backend", "ui/backend"),

    ],
    hiddenimports=[
        # FastAPI / Uvicorn 相关
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",

        # 你的模块（动态 import 的）
        "ui.backend.app.main",
        "ui.backend.app.routers.config_api",
        "ui.backend.app.routers.tasks_api",
        "ui.backend.app.routers.reports_api",
        "ui.backend.app.routers.db_api",
        "ui.backend.app.settings",
        "ui.backend.app.store",
        "ui.backend.app.runner",
        "ui.backend.app.utils",

        "database.db_handler",
        "database.db_schema",

        # 分析模块
        "analysis.trend_analyzer",
        "analysis.data_access",
        "analysis.heat",
        "analysis.metrics",
        "analysis.report",
        "analysis.visualization",
        "analysis.run_analysis",

        # 第三方库容易被漏掉的
        "pydantic_settings",
        "multipart",
        "email_validator",

        # 数据分析
        "pandas",
        "numpy",
        "matplotlib",
        "matplotlib.backends.backend_agg",
        "seaborn",
        "jieba",
        "wordcloud",
        "openpyxl",

        # pywebview
        "webview",

    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "test",
    ],
    noarchive=False,
    optimize=0,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InkOctoBot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,              # False = 无控制台窗口 (GUI app)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="InkOctoBot",
)