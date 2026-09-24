"""Immutable identity of the WebUI source loaded by this process."""

import os
import re
import subprocess
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


# Import-time capture deliberately survives an in-place checkout update. Only a
# new process/import can claim the newly loaded revision.
WEBUI_REVISION: str | None = _capture_webui_revision(REPO_ROOT)
