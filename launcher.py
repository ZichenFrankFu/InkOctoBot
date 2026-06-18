# launcher.py
import argparse
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import warnings
from pathlib import Path

# Silence third-party noise that we can't fix:
#   - jieba imports `pkg_resources` which setuptools now flags as deprecated
#   - some libs leak SyntaxWarning from regex compiles
# These are upstream and don't affect runtime; muting them keeps the
# console legible. Done BEFORE any heavy imports so the filter is in
# place when jieba loads (via downstream modules).
warnings.filterwarnings(
    "ignore",
    message=r"pkg_resources is deprecated.*",
    category=UserWarning,
    module=r"jieba(\..*)?",
)

from framework.log_setup import setup_logging
import uvicorn
import webview


# ---------- Log directory ----------
# Source-tree runs (the common case): write to <repo>/outputs/logs/ so
# developers can find logs alongside the code. Each launch produces a
# fresh timestamped file via setup_logging's default naming, so old
# logs aren't overwritten.
#
# PyInstaller-packaged runs (sys._MEIPASS exists): use the OS user-state
# dir so the exe doesn't try to write inside the read-only bundle.
def _log_dir() -> Path:
    if hasattr(sys, "_MEIPASS"):
        # Packaged: write to OS user-state directory
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        elif sys.platform == "darwin":
            base = str(Path.home() / "Library" / "Logs")
        else:
            base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
        d = Path(base) / "InkOctoBot" / "logs"
    else:
        # Source run: write into the repo's outputs/logs/ directory.
        d = Path(__file__).resolve().parent / "outputs" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


# log_filename=None → setup_logging auto-generates inkoctobot_<timestamp>.log
setup_logging(
    log_dir=_log_dir(),
    console_level=logging.WARNING,
)
logger = logging.getLogger("inkoctobot.launcher")
logger.info("Launcher starting (log dir=%s)", _log_dir())

HOST = "127.0.0.1"
PORT = 8713


def _project_root() -> Path:
    """PyInstaller exe: _MEIPASS 内的代码根；源码: launcher.py 所在目录"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _setup_test_mode(root: Path) -> Path:
    """Set up a test data directory under data_test/ and seed it if needed."""
    test_root = root / "data_test"
    # Re-seed if directory is empty OR if key DB files are missing
    needs_seed = (
        not test_root.exists()
        or not any(test_root.iterdir())
        or not (test_root / "InkOctoBot_Crawler.db").exists()
        or not (test_root / "novels.db").exists()
    )
    if needs_seed:
        from test_seed import seed
        seed(test_root)
        logger.info("Seeded test data into %s", test_root)
    return test_root


def wait_for_server(host, port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _auto_migrate_split_dbs(data_dir: Path) -> None:
    """One-time migration: move reference + inspiration tables out of
    novels.db into their own files.

    Safe + idempotent — bails if there's nothing to do or if the
    destination already has data. See scripts/migrate_split_dbs.py.
    """
    try:
        from scripts.migrate_split_dbs import needs_migration, migrate
        if needs_migration(data_dir):
            logger.info("Auto-running DB split migration on %s", data_dir)
            summary = migrate(data_dir)
            for label, tables in summary.items():
                total = sum(tables.values())
                if total:
                    logger.info("Migrated %d rows to %s.db (%s)", total, label, tables)
    except Exception as e:
        # Non-fatal — log + continue. Legacy fallback still leaves data
        # accessible; user can fix manually with the CLI script.
        logger.warning("DB split migration skipped: %s", e, exc_info=True)


def run_server(test_mode: bool = False):
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    if test_mode:
        # In test mode, override the data directory to data_test/
        # We do this by creating a wrapper root that has data_test as its "data" dir
        test_data_dir = _setup_test_mode(root)
        os.environ["WN_REPO_ROOT"] = str(root)
        os.environ["WN_DATA_DIR"] = str(test_data_dir)
        os.environ["WN_TEST_MODE"] = "1"
        _auto_migrate_split_dbs(test_data_dir)
    else:
        os.environ.setdefault("WN_REPO_ROOT", str(root))
        _auto_migrate_split_dbs(root / "data")

    from ui.backend.app.main import app
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")


def main():
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="InkOctoBot Launcher")
    parser.add_argument("--test", action="store_true",
                        help="Launch in test mode with sample data (isolated from real data)")
    parser.add_argument("--no-gui", action="store_true",
                        help="Run server only without webview GUI")
    args = parser.parse_args()

    if args.test:
        logger.info("Starting in TEST MODE — all data is isolated in data_test/")
        print("[TEST MODE] Using isolated test data in data_test/")

    server_thread = threading.Thread(target=run_server, args=(args.test,), daemon=True)
    server_thread.start()

    if not wait_for_server(HOST, PORT):
        logging.error("Server failed to start.")
        sys.exit(1)

    if args.no_gui:
        print(f"Server running at http://{HOST}:{PORT} (no GUI mode)")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    title = "InkOctoBot [TEST]" if args.test else "InkOctoBot"
    webview.create_window(
        title,
        f"http://{HOST}:{PORT}",
        width=1200,
        height=800,
    )
    # Window / taskbar icon — without an explicit icon the OS falls back
    # to the bare Python interpreter icon. Windows needs .ico; GTK/macOS .png.
    icon_path = _project_root() / "assets" / (
        "icon.ico" if os.name == "nt" else "icon.png")
    start_kwargs = {"gui": "edgechromium"}
    if icon_path.exists():
        start_kwargs["icon"] = str(icon_path)
    webview.start(**start_kwargs)


if __name__ == "__main__":
    main()