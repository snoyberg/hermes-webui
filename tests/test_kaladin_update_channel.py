"""Regression coverage for the fixed Kaladin WebUI update channel."""

import json
import subprocess
from unittest.mock import MagicMock

import api.config as config
import api.updates as updates


KALADIN_SOURCE = "https://github.com/snoyberg/hermes-webui.git"
KALADIN_REFSPEC = "refs/tags/kaladin-v*:refs/tags/kaladin-v*"


def _git(repo, *args, capture=False):
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
    )
    return result.stdout.strip() if capture else None


def _repo_with_kaladin_history(tmp_path):
    remote = tmp_path / "kaladin-source.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    _git(source, "remote", "add", "publish", str(remote))
    _git(source, "commit", "--allow-empty", "-q", "-m", "base")
    _git(source, "tag", "kaladin-v1.0.0")
    first = _git(source, "rev-parse", "HEAD", capture=True)
    _git(source, "commit", "--allow-empty", "-q", "-m", "next")
    _git(source, "tag", "kaladin-v1.1.0")
    second = _git(source, "rev-parse", "HEAD", capture=True)
    _git(source, "push", "-q", "publish", "refs/tags/kaladin-v1.0.0", "refs/tags/kaladin-v1.1.0")

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.email", "test@example.com")
    _git(checkout, "config", "user.name", "Test")
    _git(checkout, "remote", "add", "origin", "https://github.com/nesquena/hermes-webui.git")
    _git(checkout, "fetch", "-q", str(remote), KALADIN_REFSPEC)
    _git(checkout, "checkout", "-q", "kaladin-v1.0.0")
    return remote, source, checkout, first, second


def test_kaladin_channel_contract_is_fixed_and_source_scoped():
    assert updates._normalize_channel("kaladin") == "kaladin"
    assert updates._channel_tag_glob("kaladin") == "kaladin-v*"
    assert updates._channel_source_url("kaladin") == KALADIN_SOURCE
    assert updates._channel_fetch_args("kaladin") == [
        "fetch",
        KALADIN_SOURCE,
        "--quiet",
        KALADIN_REFSPEC,
    ]


def test_existing_channel_fetch_contract_is_unchanged():
    for channel in ("stable", "experimental"):
        assert updates._channel_source_url(channel) is None
        assert updates._channel_fetch_args(channel) == [
            "fetch", "origin", "--quiet", "--tags", "--force"
        ]


def test_kaladin_check_fetches_only_from_trusted_source_without_force(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    calls = []

    def fake_git(args, cwd, timeout=10):
        calls.append(args)
        if args == ["fetch", KALADIN_SOURCE, "--quiet", KALADIN_REFSPEC]:
            return "", False
        if args == ["tag", "--list", "kaladin-v*", "--sort=-v:refname"]:
            return "", True
        if args == ["diff-index", "--quiet", "HEAD", "--"]:
            return "", True
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr(updates, "_run_git", fake_git)
    result = updates._check_repo(tmp_path, "webui", "kaladin")
    assert result["stale_check"] is True
    assert calls[0] == ["fetch", KALADIN_SOURCE, "--quiet", KALADIN_REFSPEC]
    assert "--force" not in calls[0]
    assert "--tags" not in calls[0]


def test_kaladin_release_uses_trusted_compare_repository(tmp_path, monkeypatch):
    _remote, _source, checkout, _first, _second = _repo_with_kaladin_history(tmp_path)
    info = updates._check_repo_release(checkout, "webui", "kaladin")
    assert info["latest_version"] == "kaladin-v1.1.0"
    assert info["repo_url"] == KALADIN_SOURCE.removesuffix(".git")
    assert info["compare_url"].startswith(
        "https://github.com/snoyberg/hermes-webui/compare/"
    )


def test_kaladin_never_falls_back_to_a_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "base")
    _git(repo, "remote", "add", "origin", "https://github.com/nesquena/hermes-webui.git")
    assert updates._check_repo_release(repo, "webui", "kaladin") is None
    assert updates._select_apply_compare_ref(repo, "kaladin", "webui") is None


def test_kaladin_divergent_release_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "installed")
    _git(repo, "tag", "kaladin-v1.0.0")
    _git(repo, "checkout", "-q", "--orphan", "release-line")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "divergent release")
    _git(repo, "tag", "kaladin-v2.0.0")
    _git(repo, "checkout", "-q", "kaladin-v1.0.0")

    info = updates._check_repo_release(repo, "webui", "kaladin")

    assert info["behind"] is None
    assert info["channel"] == "kaladin"
    assert "fast-forward" in info["error"].lower()
    assert updates._select_apply_compare_ref(repo, "kaladin", "webui") is None


def test_kaladin_moved_tag_fetch_fails_closed(tmp_path, monkeypatch):
    remote, source, checkout, first, _second = _repo_with_kaladin_history(tmp_path)
    _git(source, "tag", "-f", "kaladin-v1.0.0", "HEAD")
    _git(source, "push", "-q", "--force", "publish", "refs/tags/kaladin-v1.0.0")
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", str(remote))

    out, ok = updates._fetch_channel_tags(checkout, "kaladin")

    assert ok is False
    assert out
    assert _git(checkout, "rev-parse", "kaladin-v1.0.0", capture=True) == first


def test_kaladin_normal_apply_fast_forward_merges_selected_tag(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    calls = []

    def fake_git(args, cwd, timeout=10):
        calls.append(args)
        if args == ["fetch", KALADIN_SOURCE, "--quiet", KALADIN_REFSPEC]:
            return "", True
        if args == ["status", "--porcelain", "--untracked-files=no"]:
            return "", True
        if args == ["merge", "--ff-only", "kaladin-v1.1.0"]:
            return "", True
        raise AssertionError(f"unexpected git args: {args!r}")

    monkeypatch.setattr(updates, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(updates, "_run_git", fake_git)
    monkeypatch.setattr(
        updates, "_select_apply_compare_ref", lambda *args: "kaladin-v1.1.0"
    )
    monkeypatch.setattr(updates, "_schedule_restart", MagicMock())

    result = updates._apply_update_inner("webui", "kaladin")

    assert result["ok"] is True
    assert ["merge", "--ff-only", "kaladin-v1.1.0"] in calls
    assert not any(call and call[0] == "pull" for call in calls)


def test_kaladin_force_refuses_to_abandon_tag_reachable_head(tmp_path, monkeypatch):
    _remote, _source, checkout, _first, _second = _repo_with_kaladin_history(tmp_path)
    divergent = tmp_path / "divergent"
    divergent.mkdir()
    _git(divergent, "init", "-q")
    _git(divergent, "config", "user.email", "test@example.com")
    _git(divergent, "config", "user.name", "Test")
    _git(divergent, "commit", "--allow-empty", "-q", "-m", "other")
    other = _git(divergent, "rev-parse", "HEAD", capture=True)
    _git(checkout, "fetch", "-q", str(divergent), f"{other}:refs/tags/other-v1")

    monkeypatch.setattr(updates, "REPO_ROOT", checkout)
    monkeypatch.setattr(updates, "_fetch_channel_tags", lambda *args, **kwargs: ("", True))
    monkeypatch.setattr(
        updates, "_select_apply_compare_ref", lambda *args: "other-v1"
    )
    monkeypatch.setattr(
        updates,
        "_restart_blocker_snapshot",
        lambda: {"restart_blocked": False, "active_streams": 0, "active_runs": 0},
    )
    result = updates.apply_force_update("webui", channel="kaladin")

    assert result["ok"] is False
    assert result.get("refused_kaladin_checkout") is True, result
    assert "preserve" in result["message"].lower()
    assert _git(checkout, "rev-parse", "HEAD", capture=True) != other


def test_config_accepts_kaladin_update_channel(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(config, "SETTINGS_FILE", settings_file)
    saved = config.save_settings({"update_channel": "kaladin"})
    assert saved["update_channel"] == "kaladin"
    assert json.loads(settings_file.read_text(encoding="utf-8"))["update_channel"] == "kaladin"


def test_frontend_exposes_and_normalizes_all_three_channels():
    root = updates.REPO_ROOT
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    panels = (root / "static" / "panels.js").read_text(encoding="utf-8")
    i18n = (root / "static" / "i18n.js").read_text(encoding="utf-8")
    assert '<option value="kaladin"' in html
    assert "settings_update_channel_kaladin" in i18n
    assert "function _normalizeUpdateChannel(" in panels
    assert "['stable','experimental','kaladin']" in panels


def test_agent_remains_channel_neutral_when_webui_uses_kaladin(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updates, "REPO_ROOT", tmp_path / "webui")
    monkeypatch.setattr(updates, "_AGENT_DIR", tmp_path / "agent")
    monkeypatch.setattr(
        updates,
        "_check_repo",
        lambda path, name, channel="stable": calls.append((name, channel))
        or {"name": name, "behind": 0},
    )
    updates.check_for_updates(force=True, include_agent=True, channel="kaladin")
    assert calls == [("webui", "kaladin"), ("agent", "stable")]
