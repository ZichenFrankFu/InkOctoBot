"""
InkOctoBot CLI — Typer-based extensible command-line interface.

Usage:
    ink agent list
    ink skill list/test/create
    ink extract ingest/run/emit/status/clean-status
    ink model list
    ink config show/validate
    ink db info
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="ink",
    help="InkOctoBot — AI Novel Creation System (AI 小说创作系统)",
    no_args_is_help=True,
)


# ═══════════════════════════════════════════════════════════════════
# Agent & Skill management
# ═══════════════════════════════════════════════════════════════════
agent_app = typer.Typer(help="Agent & Skill management (Agent 与 Skill 管理)")
app.add_typer(agent_app, name="agent")


@agent_app.command("list")
def agent_list() -> None:
    """List all agents and their available skills."""
    agents_dir = Path(__file__).resolve().parent / "agents"
    for agent_dir in sorted(agents_dir.iterdir()):
        if agent_dir.is_dir() and (agent_dir / "__init__.py").exists():
            skills_dir = agent_dir / "skills"
            skill_count = 0
            if skills_dir.is_dir():
                skill_count = sum(
                    1 for s in skills_dir.iterdir()
                    if s.is_dir() and (s / "skill.py").exists()
                )
            if agent_dir.name in ("learned_skills", "__pycache__"):
                continue
            typer.echo(f"  {agent_dir.name}: {skill_count} skills")


skill_app = typer.Typer(help="Skill management (Skill 管理)")
app.add_typer(skill_app, name="skill")


@skill_app.command("list")
def skill_list(
    tag: Optional[str] = typer.Option(None, help="Filter by tag"),
) -> None:
    """List all registered skills."""
    from framework.skill_registry import SkillRegistry

    registry = SkillRegistry()
    agents_dir = Path(__file__).resolve().parent / "agents"
    registry.scan_all(agents_dir)

    skills = registry.list_all()
    if tag:
        skills = [s for s in skills if tag in s.tags]

    if not skills:
        typer.echo("No skills found.")
        return

    for s in skills:
        tags_str = ", ".join(s.tags) if s.tags else "—"
        typer.echo(f"  {s.name:30s} v{s.version:8s} [{tags_str}]  {s.display_name}")


@skill_app.command("test")
def skill_test(
    name: str = typer.Argument(..., help="Skill name"),
    input_file: Optional[str] = typer.Option(None, "--input", "-i", help="Input JSON file"),
) -> None:
    """Test a single skill with JSON input."""
    from framework.skill_registry import SkillRegistry

    registry = SkillRegistry()
    agents_dir = Path(__file__).resolve().parent / "agents"
    registry.scan_all(agents_dir)

    try:
        skill = registry.get(name)
    except KeyError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)

    meta = skill.meta()
    typer.echo(f"Skill: {meta.name} ({meta.display_name})")
    typer.echo(f"  Version: {meta.version}")
    typer.echo(f"  Model role: {meta.model_role}")
    typer.echo(f"  Tags: {meta.tags}")

    if input_file:
        with open(input_file, "r", encoding="utf-8") as f:
            inputs = json.load(f)
        typer.echo(f"\nRunning with inputs from {input_file}...")

        from llm.router import ModelRouter
        router = ModelRouter()

        result = asyncio.run(skill.execute_with_messages(inputs, router))
        typer.echo(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
    else:
        typer.echo("\nUse --input to provide a JSON input file for execution.")


@skill_app.command("create")
def skill_create(
    name: str = typer.Argument(..., help="Skill name (snake_case)"),
    agent: str = typer.Argument(..., help="Parent agent (e.g. evaluation)"),
) -> None:
    """Create a new skill scaffold."""
    agents_dir = Path(__file__).resolve().parent / "agents"
    skill_dir = agents_dir / agent / "skills" / name
    if skill_dir.exists():
        typer.echo(f"Skill directory already exists: {skill_dir}", err=True)
        raise typer.Exit(1)

    skill_dir.mkdir(parents=True)

    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
display_name: {name.replace('_', ' ').title()}
description: Describe this skill
version: 1.0.0
model_role: default
tags: [{agent}]
permissions: []
---

## Description
Describe what this skill does.

## Input
- `text`: Input text

## Output
- `result`: Output result
""",
        encoding="utf-8",
    )

    (skill_dir / "skill.py").write_text(
        f'''"""Skill: {name}"""
from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="{name}",
            display_name="{name.replace('_', ' ').title()}",
            description="Describe this skill",
            tags=["{agent}"],
        )

    async def build_prompt(self, inputs: dict) -> str:
        return f"Build prompt from {{inputs}}"

    async def parse_output(self, raw: str) -> dict:
        return {{"raw": raw}}
''',
        encoding="utf-8",
    )

    typer.echo(f"Created skill scaffold: {skill_dir}")


# ═══════════════════════════════════════════════════════════════════
# Novel Skill Extraction
# ═══════════════════════════════════════════════════════════════════
extract_app = typer.Typer(help="Novel skill extraction pipeline (小说技巧提取)")
app.add_typer(extract_app, name="extract")

_DEFAULT_CORPUS_DIR = "data/novel_corpus"
_DEFAULT_DB = "data/reference.db"


@extract_app.command("ingest")
def extract_ingest(
    dir: str = typer.Option(_DEFAULT_CORPUS_DIR, "--dir", "-d", help="Novel corpus directory"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Single txt file to ingest"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="Database path"),
) -> None:
    """Ingest and clean novel txt files (扫描、清洗、注册小说)."""
    from reference_ingest.novel_ingester import NovelIngester

    ingester = NovelIngester(db, dir)

    if file:
        result = ingester.ingest_single(file)
        if result:
            typer.echo(f"Ingested: {result.title} ({result.total_chapters} chapters, "
                       f"{result.total_chars:,} chars, {result.excluded_author_notes} author-notes removed)")
            if result.needs_review:
                typer.echo(f"  {len(result.needs_review)} items need review")
        else:
            typer.echo("No new file to ingest (already processed or not found).")
    else:
        results = ingester.ingest_all()
        typer.echo(f"\nIngested {len(results)} novels:")
        total_chars = 0
        total_notes = 0
        for r in results:
            typer.echo(f"  {r.title}: {r.total_chapters} chapters, {r.total_chars:,} chars")
            total_chars += r.total_chars
            total_notes += r.excluded_author_notes

        typer.echo(f"\nTotal: {len(results)} novels, {total_chars:,} chars, "
                   f"{total_notes} author-notes removed")


@extract_app.command("run")
def extract_run(
    phase: Optional[str] = typer.Option(None, "--phase", "-p",
        help="Phase to run: clean/chapter/novel/pattern/emit (default: all)"),
    novels: Optional[str] = typer.Option(None, "--novels", "-n",
        help="Comma-separated novel titles to process"),
    resume: bool = typer.Option(True, "--resume/--no-resume",
        help="Resume from last checkpoint"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="Database path"),
    corpus_dir: str = typer.Option(_DEFAULT_CORPUS_DIR, "--dir", help="Corpus directory"),
) -> None:
    """Run extraction pipeline (运行提取流程)."""
    from reference_ingest.skill_extraction.orchestrator import SkillExtractionOrchestrator
    from llm.router import ModelRouter

    orchestrator = SkillExtractionOrchestrator(
        db_path=db,
        corpus_dir=corpus_dir,
        model_router=ModelRouter(),
    )

    phases = [phase] if phase else None
    novel_list = [n.strip() for n in novels.split(",")] if novels else None

    result = asyncio.run(orchestrator.run(
        resume=resume,
        novels=novel_list,
        phases=phases,
    ))

    typer.echo(f"\nPipeline result:")
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@extract_app.command("emit")
def extract_emit(
    category: Optional[str] = typer.Option(None, "--category", "-c",
        help="Category: writing_technique/chapter_design/story_arc"),
    all_cats: bool = typer.Option(False, "--all", help="Emit all categories"),
    db: str = typer.Option(_DEFAULT_DB, "--db", help="Database path"),
) -> None:
    """Generate skill files from mined patterns (生成技巧skill)."""
    from reference_ingest.skill_extraction.skill_emitter import SkillEmitter

    emitter = SkillEmitter(db)

    if all_cats or not category:
        counts = emitter.emit_all()
        for cat, count in counts.items():
            typer.echo(f"  {cat}: {count} skills emitted")
    else:
        count = emitter.emit_category(category)
        typer.echo(f"  {category}: {count} skills emitted")


@extract_app.command("status")
def extract_status(
    db: str = typer.Option(_DEFAULT_DB, "--db", help="Database path"),
) -> None:
    """Show extraction pipeline status (查看提取进度)."""
    from reference_ingest.skill_extraction.orchestrator import SkillExtractionOrchestrator

    orchestrator = SkillExtractionOrchestrator(db_path=db)
    status = orchestrator.get_status()

    typer.echo(f"\nNovel Skill Extraction Status")
    typer.echo(f"{'='*40}")
    typer.echo(f"Total novels: {status['total_novels']}")

    typer.echo(f"\nPhase progress:")
    for phase, counts in status.get("phases", {}).items():
        total = sum(counts.values())
        completed = counts.get("completed", 0)
        failed = counts.get("failed", 0)
        typer.echo(f"  {phase:20s}: {completed}/{total} completed"
                   + (f", {failed} failed" if failed else ""))

    typer.echo(f"\nExtracted patterns:")
    for cat, count in status.get("patterns", {}).items():
        typer.echo(f"  {cat}: {count}")

    typer.echo(f"\nSkills emitted: {status.get('skills_emitted', 0)}")


@extract_app.command("clean-status")
def extract_clean_status(
    db: str = typer.Option(_DEFAULT_DB, "--db", help="Database path"),
) -> None:
    """Show data cleaning status (查看清洗报告)."""
    from reference_ingest.novel_ingester import NovelIngester

    ingester = NovelIngester(db)
    status = ingester.get_clean_status()

    typer.echo(f"\nData Cleaning Status")
    typer.echo(f"{'='*40}")
    typer.echo(f"Total novels ingested: {status['total_novels']}")
    typer.echo(f"By status: {status['by_status']}")
    typer.echo(f"Total chapters: {status['total_chapters']}")
    typer.echo(f"Total chars: {status['total_chars']:,}")
    typer.echo(f"Author notes removed: {status['total_author_notes_removed']}")


# ═══════════════════════════════════════════════════════════════════
# Model management
# ═══════════════════════════════════════════════════════════════════
model_app = typer.Typer(help="LLM model management (LLM 模型管理)")
app.add_typer(model_app, name="model")


@model_app.command("list")
def model_list() -> None:
    """List configured models."""
    from llm.router import ModelRouter
    try:
        router = ModelRouter()
        for key, desc in router.list_providers().items():
            typer.echo(f"  {key}: {desc}")
    except Exception as e:
        typer.echo(f"Error loading models: {e}", err=True)


# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════
config_app = typer.Typer(help="Configuration management (配置管理)")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    from framework.config import get_config
    cfg = get_config()
    typer.echo(f"Config dir: {cfg.config_dir}")
    typer.echo(f"App DB:     {cfg.app_db_path}")
    typer.echo(f"Crawler DB: {cfg.crawler_db_path}")


@config_app.command("validate")
def config_validate() -> None:
    """Validate configuration files."""
    from framework.config import get_config
    try:
        cfg = get_config()
        cfg.reload()
        typer.echo("Configuration is valid.")
    except Exception as e:
        typer.echo(f"Configuration error: {e}", err=True)
        raise typer.Exit(1)


# ═══════════════════════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════════════════════
db_app = typer.Typer(help="Database management (数据库管理)")
app.add_typer(db_app, name="db")


@db_app.command("info")
def db_info() -> None:
    """Show database info."""
    from framework.config import get_config
    cfg = get_config()
    for label, path in [("App DB", cfg.app_db_path), ("Crawler DB", cfg.crawler_db_path)]:
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        typer.echo(f"  {label}: {path} ({'exists' if exists else 'MISSING'}, {size:,} bytes)")


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════
def main() -> None:
    app()


if __name__ == "__main__":
    main()
