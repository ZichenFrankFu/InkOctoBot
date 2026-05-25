"""
/api/dev — Developer tools API endpoints.
"""
from __future__ import annotations
import subprocess, sys
from pathlib import Path
from fastapi import APIRouter, Body

router = APIRouter(prefix="/dev", tags=["dev"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent


@router.post("/health-check")
def run_health_check():
    """Run the project health check script."""
    script = PROJECT_ROOT / "scripts" / "check_project_health.py"
    if not script.exists():
        return {"output": "Health check script not found.", "issues": -1}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=30, cwd=str(PROJECT_ROOT),
        )
        output = result.stdout + result.stderr
        issues = 0
        for line in output.splitlines():
            if line.strip().startswith("[!]"):
                issues += 1
        return {"output": output, "issues": issues}
    except subprocess.TimeoutExpired:
        return {"output": "Health check timed out after 30s.", "issues": -1}
    except Exception as e:
        return {"output": f"Error running health check: {e}", "issues": -1}


@router.post("/seed-test-data")
def seed_test_data(body: dict = Body(...)):
    """Run the test seed script."""
    script = PROJECT_ROOT / "test_seed.py"
    if not script.exists():
        return {"message": "Test seed script (test_seed.py) not found."}
    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=30, cwd=str(PROJECT_ROOT),
        )
        output = result.stdout.strip() or result.stderr.strip() or "Seed completed."
        return {"message": output}
    except subprocess.TimeoutExpired:
        return {"message": "Seed script timed out after 30s."}
    except Exception as e:
        return {"message": f"Error: {e}"}
