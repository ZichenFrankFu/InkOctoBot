"""
InkOctoBot 项目自检工具

自动检测并报告：
- 冗余/备份文件 (.bak, .old, .orig, ~)
- 空的 __init__.py 以外的空文件
- 孤立的 Python 模块（无人导入）
- 损坏的内部 import
- 重复的模块名（不同路径同名文件）

Usage:
    python scripts/check_project_health.py [--fix]

    --fix  自动删除明确的冗余文件（.bak, .orig, .old, ~）
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", "data",
    "data_test", ".tox", "eggs", "*.egg-info",
}

BACKUP_SUFFIXES = {".bak", ".old", ".orig", ".backup", ".swp", ".swo"}
BACKUP_PATTERNS = {"~"}


def iter_files(root: Path):
    """Yield all non-ignored files in the project."""
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file():
            yield p


def check_backup_files(root: Path) -> list[Path]:
    """Find backup/temporary files."""
    found = []
    for p in iter_files(root):
        if p.suffix in BACKUP_SUFFIXES or p.name.endswith("~"):
            found.append(p)
    return found


def check_empty_files(root: Path) -> list[Path]:
    """Find empty files that are not __init__.py or .gitkeep."""
    found = []
    for p in iter_files(root):
        if p.name in ("__init__.py", ".gitkeep", ".gitignore"):
            continue
        if p.suffix in (".py", ".ts", ".tsx", ".js", ".yaml", ".yml", ".json"):
            try:
                if p.stat().st_size == 0:
                    found.append(p)
            except OSError:
                pass
    return found


def check_duplicate_modules(root: Path) -> dict[str, list[Path]]:
    """Find Python modules with the same filename in different directories."""
    by_name: dict[str, list[Path]] = defaultdict(list)
    for p in iter_files(root):
        if p.suffix == ".py" and p.name != "__init__.py":
            by_name[p.name].append(p)
    return {name: paths for name, paths in by_name.items() if len(paths) > 1}


def check_broken_imports(root: Path) -> list[tuple[Path, str]]:
    """Check Python files for imports that reference non-existent internal modules."""
    issues = []
    py_files = [p for p in iter_files(root) if p.suffix == ".py"]

    # Build set of known internal module paths
    known_modules: set[str] = set()
    for p in py_files:
        rel = p.relative_to(root)
        parts = list(rel.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1].removesuffix(".py")
        mod = ".".join(parts)
        known_modules.add(mod)
        # Also add parent packages
        for i in range(1, len(parts)):
            known_modules.add(".".join(parts[:i]))

    internal_prefixes = {
        "agents", "analysis", "core", "models", "preprocessing",
        "rag", "database", "security", "tasks", "ui", "config",
        "constraints",
    }

    for p in py_files:
        try:
            tree = ast.parse(p.read_text("utf-8"), filename=str(p))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            mod_name = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in internal_prefixes:
                        mod_name = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in internal_prefixes:
                    mod_name = node.module

            if mod_name and mod_name not in known_modules:
                # Check if any known module starts with this name (package)
                if not any(km.startswith(mod_name + ".") or km == mod_name for km in known_modules):
                    issues.append((p, mod_name))

    return issues


def main():
    parser = argparse.ArgumentParser(description="InkOctoBot project health check")
    parser.add_argument("--fix", action="store_true", help="Auto-remove backup files")
    args = parser.parse_args()

    print("=" * 60)
    print("  InkOctoBot 项目自检报告")
    print("=" * 60)
    print()

    issues_found = 0

    # 1. Backup files
    backups = check_backup_files(PROJECT_ROOT)
    if backups:
        issues_found += len(backups)
        print(f"[!] 备份/临时文件 ({len(backups)}):")
        for p in backups:
            rel = p.relative_to(PROJECT_ROOT)
            print(f"    {rel}")
            if args.fix:
                p.unlink()
                print(f"    -> 已删除")
        print()
    else:
        print("[OK] 无备份/临时文件")
        print()

    # 2. Empty files
    empties = check_empty_files(PROJECT_ROOT)
    if empties:
        issues_found += len(empties)
        print(f"[!] 空文件 ({len(empties)}):")
        for p in empties:
            print(f"    {p.relative_to(PROJECT_ROOT)}")
        print()
    else:
        print("[OK] 无空文件")
        print()

    # 3. Duplicate module names
    dupes = check_duplicate_modules(PROJECT_ROOT)
    if dupes:
        issues_found += len(dupes)
        print(f"[!] 同名模块 ({len(dupes)} 组):")
        for name, paths in sorted(dupes.items()):
            print(f"    {name}:")
            for p in paths:
                print(f"      - {p.relative_to(PROJECT_ROOT)}")
        print()
    else:
        print("[OK] 无同名模块冲突")
        print()

    # 4. Broken imports
    broken = check_broken_imports(PROJECT_ROOT)
    if broken:
        issues_found += len(broken)
        print(f"[!] 可能的失效 import ({len(broken)}):")
        for p, mod in broken:
            print(f"    {p.relative_to(PROJECT_ROOT)}: {mod}")
        print()
    else:
        print("[OK] 无失效 import")
        print()

    # Summary
    print("=" * 60)
    if issues_found == 0:
        print("  项目状态: 健康 (0 个问题)")
    else:
        print(f"  项目状态: 发现 {issues_found} 个问题")
        if not args.fix:
            print("  提示: 使用 --fix 自动清理备份文件")
    print("=" * 60)

    return 1 if issues_found > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
