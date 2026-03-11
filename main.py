"""InkOctoBot CLI.

Crawler (spider) runtime has been extracted to another repository.
This CLI now only provides crawler DB integration helpers.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import config

PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_crawler_db_path() -> Path:
    crawler_cfg = getattr(config, "CRAWLER_DATABASE", {}) or {}
    p = crawler_cfg.get("path")
    if isinstance(p, str) and p.strip():
        pp = Path(p)
        return pp if pp.is_absolute() else (PROJECT_ROOT / pp).resolve()

    legacy = getattr(config, "SPIDER_DATABASE", {}) or {}
    p2 = legacy.get("path")
    if isinstance(p2, str) and p2.strip():
        pp = Path(p2)
        return pp if pp.is_absolute() else (PROJECT_ROOT / pp).resolve()

    return (PROJECT_ROOT / "data" / "InkOctoBot_Crawler.db").resolve()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="InkOctoBot CLI (crawler extracted)")
    p.add_argument("mode", choices=["db_info", "once", "scheduler"], nargs="?", default="db_info")
    return p


def main() -> None:
    args = build_parser().parse_args()

    if args.mode in {"once", "scheduler"}:
        raise SystemExit(
            "Crawler runtime has moved to another repo. "
            "Please run crawling there and sync data file 'InkOctoBot_Crawler.db'."
        )

    db_path = resolve_crawler_db_path()
    print(f"crawler_db_path={db_path}")


if __name__ == "__main__":
    main()
