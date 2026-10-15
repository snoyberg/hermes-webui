"""Behavioral failed-upload recovery gate; synthetic audio, no server or microphone."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_failed_upload_recovery():
    node = shutil.which("node")
    if not node:
        pytest.skip("node required")
    result = subprocess.run(
        [node, str(ROOT / "tests/upload_recovery.cjs"), str(ROOT / "static")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["caseCount"] == len(report["cases"]) == 18
    assert len({case["name"] for case in report["cases"]}) == 18
