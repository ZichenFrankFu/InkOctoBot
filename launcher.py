# launcher.py
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


def wait_for_server(host, port, timeout=15):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def run_server():
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # 设置环境变量，让 settings.py 知道真正的项目根
    os.environ.setdefault("WN_REPO_ROOT", str(root))

    from ui.backend.app.main import app
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")


def main():
    multiprocessing.freeze_support()

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    if not wait_for_server(HOST, PORT):
        logging.error("Server failed to start.")
        sys.exit(1)

    webview.create_window(
        "WebNovel Trends",
        f"http://{HOST}:{PORT}",
        width=1200,
        height=800,
    )
    webview.start(gui="edgechromium")


if __name__ == "__main__":
    main()