"""The health identity names the code loaded by this process, not live HEAD."""

import importlib
import re
import subprocess
from types import SimpleNamespace
from urllib.parse import urlparse

import api.config as config
import api.routes as routes
import api.runtime_identity as runtime_identity


def _git(repo, *args, capture=False):
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip() if capture else None


def test_runtime_revision_is_captured_once_until_module_restart(tmp_path, monkeypatch):
    repo = tmp_path / "checkout"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "A")
    revision_a = _git(repo, "rev-parse", "HEAD", capture=True)

    original_root = config.REPO_ROOT
    try:
        monkeypatch.setattr(config, "REPO_ROOT", repo)
        loaded = importlib.reload(runtime_identity)
        assert loaded.WEBUI_REVISION == revision_a
        assert re.fullmatch(r"[0-9a-f]{40}", loaded.WEBUI_REVISION)

        _git(repo, "commit", "--allow-empty", "-q", "-m", "B")
        revision_b = _git(repo, "rev-parse", "HEAD", capture=True)
        assert revision_b != revision_a
        assert loaded.WEBUI_REVISION == revision_a

        restarted = importlib.reload(runtime_identity)
        assert restarted.WEBUI_REVISION == revision_b
    finally:
        config.REPO_ROOT = original_root
        importlib.reload(runtime_identity)


def test_health_exposes_only_captured_valid_revision(monkeypatch):
    responses = []
    monkeypatch.setattr(routes, "WEBUI_REVISION", "a" * 40)
    monkeypatch.setattr(routes, "_streams_lock_health", lambda: {"status": "ok", "active_streams": 0})
    monkeypatch.setattr(routes, "_run_lifecycle_health", lambda: {"active_runs": 0, "runs": [], "last_run_finished_at": None})
    monkeypatch.setattr(routes, "_accept_loop_health", lambda _handler: {"status": "ok"})
    monkeypatch.setattr(
        routes, "j",
        lambda _handler, payload, **kwargs: responses.append(payload) or True,
    )

    routes._handle_health(SimpleNamespace(), urlparse("/health"))

    assert responses[0]["webui_revision"] == "a" * 40

    responses.clear()
    monkeypatch.setattr(routes, "WEBUI_REVISION", None)
    routes._handle_health(SimpleNamespace(), urlparse("/health"))
    assert "webui_revision" not in responses[0]


def test_runtime_revision_fails_closed_without_a_git_head(tmp_path):
    # Initialize a nested repository so Git cannot discover the enclosing WebUI
    # checkout while still leaving HEAD genuinely unavailable.
    _git(tmp_path, "init", "-q")
    assert runtime_identity._capture_webui_revision(tmp_path) is None