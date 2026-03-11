"""
InkOctoBot CLI — Typer-based extensible command-line interface.

Usage:
    ink project create/list/delete/export
    ink agent list/run
    ink skill list/test/create
    ink generate chapter/evaluate
    ink analysis trend/extract/report
    ink memory status/consolidate/search
    ink model list/test/cost
    ink config show/validate
    ink db info/migrate
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(
    name="ink",
    help="InkOctoBot — AI Novel Creation System (AI 小说创作系统)",
    no_args_is_help=True,
)


# ═══════════════════════════════════════════════════════════════════
# Project management
# ═══════════════════════════════════════════════════════════════════
project_app = typer.Typer(help="Project management (项目管理)")
app.add_typer(project_app, name="project")


@project_app.command("create")
def project_create(
    name: str = typer.Argument(..., help="Project name"),
    genre: str = typer.Option("玄幻", help="Genre (类型)"),
) -> None:
    """Create a new novel project."""
    typer.echo(f"Creating project: {name} (genre={genre})")
    # TODO: wire to database/db_handler.py project creation


@project_app.command("list")
def project_list() -> None:
    """List all projects."""
    typer.echo("Projects:")
    # TODO: wire to database


@project_app.command("delete")
def project_delete(
    project_id: str = typer.Argument(..., help="Project ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete a project."""
    if not force:
        typer.confirm(f"Delete project {project_id}?", abort=True)
    typer.echo(f"Deleting project: {project_id}")


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
    from core.skill_registry import SkillRegistry

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
    from core.skill_registry import SkillRegistry

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

        from models.router import ModelRouter
        router = ModelRouter()

        result = asyncio.run(skill.execute_with_messages(inputs, router))
        typer.echo(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
    else:
        typer.echo("\nUse --input to provide a JSON input file for execution.")


@skill_app.command("create")
def skill_create(
    name: str = typer.Argument(..., help="Skill name (snake_case)"),
    agent: str = typer.Argument(..., help="Parent agent (e.g. evaluation)"),
    template: str = typer.Option("basic", help="Template type"),
) -> None:
    """Create a new skill scaffold."""
    agents_dir = Path(__file__).resolve().parent / "agents"
    skill_dir = agents_dir / agent / "skills" / name
    if skill_dir.exists():
        typer.echo(f"Skill directory already exists: {skill_dir}", err=True)
        raise typer.Exit(1)

    skill_dir.mkdir(parents=True)

    # SKILL.md
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
display_name: {name.replace('_', ' ').title()}
description: TODO — describe this skill
version: 1.0.0
model_role: default
tags: [{agent}]
permissions: []
---

## Description
TODO

## Input
- `text`: Input text

## Output
- `result`: Output result
""",
        encoding="utf-8",
    )

    # skill.py
    (skill_dir / "skill.py").write_text(
        f'''"""Skill: {name}"""
from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="{name}",
            display_name="{name.replace('_', ' ').title()}",
            description="TODO",
            tags=["{agent}"],
        )

    async def build_prompt(self, inputs: dict) -> str:
        return f"TODO: build prompt from {{inputs}}"

    async def parse_output(self, raw: str) -> dict:
        return {{"raw": raw}}
''',
        encoding="utf-8",
    )

    typer.echo(f"Created skill scaffold: {skill_dir}")


# ═══════════════════════════════════════════════════════════════════
# Generation pipeline
# ═══════════════════════════════════════════════════════════════════
generate_app = typer.Typer(help="Content generation (内容生成)")
app.add_typer(generate_app, name="generate")


@generate_app.command("chapter")
def generate_chapter(
    project: str = typer.Argument(..., help="Project ID"),
    chapter: int = typer.Argument(..., help="Chapter number"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Estimate cost only"),
) -> None:
    """Generate a chapter."""
    typer.echo(f"Generating chapter {chapter} for project {project}")
    if dry_run:
        typer.echo("(dry run — cost estimation only)")
    # TODO: wire to pipeline


@generate_app.command("evaluate")
def generate_evaluate(
    project: str = typer.Argument(..., help="Project ID"),
    chapter: int = typer.Argument(..., help="Chapter number"),
) -> None:
    """Evaluate a generated chapter."""
    typer.echo(f"Evaluating chapter {chapter} for project {project}")


# ═══════════════════════════════════════════════════════════════════
# Analysis
# ═══════════════════════════════════════════════════════════════════
analysis_app = typer.Typer(help="Market data analysis (市场数据分析)")
app.add_typer(analysis_app, name="analysis")


@analysis_app.command("trend")
def analysis_trend(
    tag: Optional[str] = typer.Option(None, help="Genre/tag filter"),
    weeks: int = typer.Option(12, help="Lookback weeks"),
) -> None:
    """Run trend analysis."""
    typer.echo(f"Running trend analysis (tag={tag}, weeks={weeks})")


# ═══════════════════════════════════════════════════════════════════
# Memory system
# ═══════════════════════════════════════════════════════════════════
memory_app = typer.Typer(help="Memory system management (记忆系统管理)")
app.add_typer(memory_app, name="memory")


@memory_app.command("status")
def memory_status(
    project: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Show memory system status."""
    typer.echo(f"Memory status for project: {project}")


@memory_app.command("consolidate")
def memory_consolidate(
    project: str = typer.Argument(..., help="Project ID"),
) -> None:
    """Force memory consolidation."""
    typer.echo(f"Consolidating memory for project: {project}")


# ═══════════════════════════════════════════════════════════════════
# Model management
# ═══════════════════════════════════════════════════════════════════
model_app = typer.Typer(help="LLM model management (LLM 模型管理)")
app.add_typer(model_app, name="model")


@model_app.command("list")
def model_list() -> None:
    """List configured models."""
    from models.router import ModelRouter
    try:
        router = ModelRouter()
        for key, desc in router.list_providers().items():
            typer.echo(f"  {key}: {desc}")
    except Exception as e:
        typer.echo(f"Error loading models: {e}", err=True)


@model_app.command("test")
def model_test(
    provider: str = typer.Argument(..., help="Provider name (ollama/openai/...)"),
) -> None:
    """Test model provider connectivity."""
    typer.echo(f"Testing provider: {provider}")


# ═══════════════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════════════
config_app = typer.Typer(help="Configuration management (配置管理)")
app.add_typer(config_app, name="config")


@config_app.command("show")
def config_show() -> None:
    """Show current configuration."""
    from core.config import get_config
    cfg = get_config()
    typer.echo(f"Config dir: {cfg.config_dir}")
    typer.echo(f"App DB:     {cfg.app_db_path}")
    typer.echo(f"Crawler DB: {cfg.crawler_db_path}")


@config_app.command("validate")
def config_validate() -> None:
    """Validate configuration files."""
    from core.config import get_config
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
    from core.config import get_config
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
