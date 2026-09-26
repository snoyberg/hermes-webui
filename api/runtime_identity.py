"""Immutable identity of the WebUI source loaded by this process."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from api.config import REPO_ROOT
from api.subprocess_utils import windows_hide_flags


def _capture_webui_revision(repo_root: Path) -> str | None:
    """Resolve an exact SHA-1 HEAD once, failing closed when unavailable."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            cwd=str(repo_root), env=env, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=3,
            encoding="utf-8", errors="replace",
            creationflags=windows_hide_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = (result.stdout or "").strip().lower()
    if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        return None
    return revision


def _capture_agent_generation(source: Path, facts_path: Path, loaded_environment: Path) -> dict | None:
    """Report the imported Agent's committed PM graph, never an intended config graph."""
    try:
        source = source.resolve(strict=True)
        root = source.parent
        if source.name != "run_agent.py" or not (root / ".git").exists():
            return None
        revision = _capture_webui_revision(root)
        if revision is None:
            return None
        git_env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        git_env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                cwd=root, env=git_env, capture_output=True, timeout=3,
                                creationflags=windows_hide_flags())
        if status.returncode != 0 or status.stdout:
            return None
        fact = json.loads(facts_path.read_text(encoding="utf-8"))["packages"]["venv"]
        environment = Path(fact["environment"]).resolve(strict=True)
        lock = Path(fact["resolved_lock"]).resolve(strict=True)
        if (environment != loaded_environment.resolve(strict=True)
                or environment.name != "venv"
                or lock != environment.parent / "workspace" / "uv.lock"
                or not lock.is_file()):
            return None
        return {"source_revision": revision, "environment": str(environment),
                "dependency_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        return None


def _loaded_agent_generation() -> dict | None:
    agent = sys.modules.get("run_agent")
    source = getattr(agent, "__file__", None)
    if not source:
        return None
    try:
        from pm.environments import install_state_dir, site_packages
        root = Path(source).resolve(strict=True).parent
        facts = install_state_dir(root) / "facts.json"
        fact = json.loads(facts.read_text(encoding="utf-8"))["packages"]["venv"]
        environment = Path(fact["environment"]).resolve(strict=True)
        # PM activates the selected generation into this process's import path.
        if str(site_packages(environment).resolve(strict=True)) not in sys.path:
            return None
        return _capture_agent_generation(Path(source), facts, environment)
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        return None

# Import-time capture deliberately survives an in-place checkout update. Only a
# new process/import can claim the newly loaded revision.
WEBUI_REVISION: str | None = _capture_webui_revision(REPO_ROOT)
AGENT_GENERATION: dict | None = _loaded_agent_generation()
