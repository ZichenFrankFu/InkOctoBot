# CLI Reference

InkOctoBot CLI is built with [Typer](https://typer.tiangolo.com/).

## Usage

```bash
python cli.py [COMMAND] [OPTIONS]
```

## Commands

### Project Management
```bash
ink project create <name> [--genre 玄幻]    # Create new project
ink project list                             # List all projects
ink project delete <project_id> [--force]    # Delete a project
```

### Agent & Skill Management
```bash
ink agent list                               # List agents and skills
ink skill list [--tag TAG]                   # List registered skills
ink skill test <name> [--input FILE]         # Test a skill
ink skill create <name> <agent> [--template] # Create skill scaffold
```

### Content Generation
```bash
ink generate chapter <project> <chapter_num> [--dry-run]
ink generate evaluate <project> <chapter_num>
```

### Analysis
```bash
ink analysis trend [--tag TAG] [--weeks 12]
```

### Memory System
```bash
ink memory status <project>
ink memory consolidate <project>
```

### Model Management
```bash
ink model list                               # List configured models
ink model test <provider>                    # Test provider connectivity
```

### Configuration
```bash
ink config show                              # Show configuration
ink config validate                          # Validate config files
```

### Database
```bash
ink db info                                  # Show database info
```
