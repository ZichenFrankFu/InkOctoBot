# launcher.py
import argparse
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
from pathlib import Path
from log_setup import setup_logging
import uvicorn
import webview


# ---------- 跨平台日志目录 ----------
def _log_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Logs")
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    d = Path(base) / "InkOctoBot"
    d.mkdir(parents=True, exist_ok=True)
    return d


LOG_PATH = _log_dir() / "launcher.log"
setup_logging(
    log_dir=_log_dir(),
    console_level=logging.WARNING,
    log_filename="launcher.log",
)
logger = logging.getLogger("inkoctobot.launcher")
logging.info("Launcher starting...")

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
    else:
        os.environ.setdefault("WN_REPO_ROOT", str(root))

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

    title = "InkOctoBot [TEST]" if args.test else "WebNovel Trends"
    webview.create_window(
        title,
        f"http://{HOST}:{PORT}",
        width=1200,
        height=800,
    )
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()