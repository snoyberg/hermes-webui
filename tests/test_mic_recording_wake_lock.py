"""Exercise real composer capture/wake-lock functions with synthetic browser APIs.

These tests establish lifecycle and byte handoff, not physical device sleep.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


@pytest.mark.parametrize("scenario", ["raw", "transcribe", "unsupported", "rejected", "visibility", "pending-stop", "pending-hide", "error", "cancel", "start-error", "speech"])
def test_capture_wake_lock_lifecycle(scenario):
    result = subprocess.run(
        [NODE, str(ROOT / "tests/mic_recording_wake_lock.cjs"), str(ROOT / "static/boot.js"), scenario],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
