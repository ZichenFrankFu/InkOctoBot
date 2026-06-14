from __future__ import annotations
import importlib.util
from pathlib import Path
from typing import Any, Dict, Tuple

def load_repo_config(repo_root: Path):
    cfg_path = repo_root / "config.py"
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.py not found at: {cfg_path}")
    spec = importlib.util.spec_from_file_location("repo_config", str(cfg_path))
    module = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    spec.loader.exec_module(module)  # type: ignore
    return module

def get_output_paths(repo_cfg) -> Dict[str, str]:
    # 你 config.py 定义了 OUTPUT_PATHS
    out = getattr(repo_cfg, "OUTPUT_PATHS", None) or {}
    return {k: str(v) for k, v in out.items()}

def get_db_path(repo_cfg, repo_root: Path) -> str:
    # 兼容你 config.py 的 DATABASE['path']（你现在就是这个）:contentReference[oaicite:1]{index=1}
    db = getattr(repo_cfg, "DATABASE", None) or {}
    p = db.get("path")
    if p:
        return str(Path(p))  # config.py 里已是绝对/拼好了 BASE_DIR
    # fallback
    out = get_output_paths(repo_cfg)
    data_dir = out.get("data", str(repo_root / "outputs" / "data"))
    return str(Path(data_dir) / "novels.db")

def get_crawler_db_path(repo_cfg, repo_root: Path) -> str:
    crawler_db = getattr(repo_cfg, "CRAWLER_DATABASE", None) or {}
    p = crawler_db.get("path")
    if p:
        return str(Path(p))

    # backward compatibility for old key name
    spider_db = getattr(repo_cfg, "SPIDER_DATABASE", None) or {}
    p2 = spider_db.get("path")
    if p2:
        return str(Path(p2))

    return get_db_path(repo_cfg, repo_root)


def resolve_crawler_db_path() -> str:
    """The canonical "where is the market data DB?" resolver.

    Priority (highest wins):
      1. ``data/settings.json:crawler_db_path`` — user-configured path
         from the Settings page. This is the **user-facing override**;
         setting a path here is how a user points the app at an externally-
         maintained ``InkOctoBot_Crawler.db``.
      2. Test-mode override (``data_test/InkOctoBot_Crawler.db``).
      3. ``config.py:CRAWLER_DATABASE['path']`` from paths.yaml.
      4. Fallback to project DB path.

    Empty string is returned ONLY when nothing resolves and the file
    doesn't exist anywhere — callers should handle that gracefully.

    Every caller that touches the crawler DB MUST use this function so
    user-configured paths take effect uniformly across all 3+ routers
    (market_db / analysis / marketing).
    """
    import json
    import logging
    from .settings import settings as _s

    log = logging.getLogger("inkoctobot.ui.backend.crawler_path")

    # 1. User override via settings.json — highest precedence.
    try:
        sp = _s.get_data_path("settings.json")
        if sp.exists():
            user = json.loads(sp.read_text("utf-8"))
            custom = (user.get("crawler_db_path") or "").strip()
            if custom and Path(custom).exists():
                log.debug("crawler_db user-configured path=%s", custom)
                return str(Path(custom).resolve())
            elif custom:
                # User set a path but the file is missing — log it loudly so
                # they know why "set crawler DB" appeared to do nothing.
                log.warning(
                    "crawler_db user path configured but file missing: %s",
                    custom,
                )
    except Exception as e:
        log.debug("crawler_db settings.json read failed: %s", e)

    # 2. Test mode → data_test/InkOctoBot_Crawler.db if it exists.
    if _s.test_mode and _s.data_dir:
        test_p = _s.data_dir / "InkOctoBot_Crawler.db"
        if test_p.exists():
            log.debug("crawler_db test-mode path=%s", test_p)
            return str(test_p)

    # 3. paths.yaml default.
    try:
        repo_cfg = load_repo_config(_s.repo_root)
        p = get_crawler_db_path(repo_cfg, _s.repo_root)
        log.debug("crawler_db paths.yaml path=%s", p)
        return p
    except Exception as e:
        log.debug("crawler_db paths.yaml resolution failed: %s", e)
        return str(_s.repo_root / "data" / "InkOctoBot_Crawler.db")


def crawler_db_version() -> str:
    """Version key for cached market computations: crawler DB mtime+size.

    Empty string means "no crawler DB" — callers short-circuit with an
    empty payload instead of starting a doomed compute job.
    """
    import os

    path = resolve_crawler_db_path()
    try:
        st = os.stat(path)
        return f"{st.st_mtime_ns}.{st.st_size}"
    except OSError:
        return ""


def get_rank_keys(repo_cfg) -> Dict[str, list[str]]:
    websites = getattr(repo_cfg, "WEBSITES", None) or {}
    res: Dict[str, list[str]] = {}
    for plat in ["qidian", "fanqie"]:
        cfg = websites.get(plat, {}) or {}
        rank_urls = cfg.get("rank_urls", {}) or {}
        res[plat] = list(rank_urls.keys())
    return res
