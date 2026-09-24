"""Regression coverage for the fixed Kaladin WebUI update channel."""

import json
import re
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


def _trusted_refs(repo):
    out = _git(
        repo,
        "for-each-ref",
        "--format=%(refname:strip=4) %(objectname)",
        "refs/hermes-webui/kaladin/tags/",
        capture=True,
    )
    return dict(line.split(" ", 1) for line in out.splitlines() if line)


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
    monkeypatch.setattr(updates, "_fetch_channel_tags", lambda *args, **kwargs: ("network unavailable", False))
    monkeypatch.setattr(updates, "_is_dirty", lambda *args, **kwargs: False)
    result = updates._check_repo(tmp_path, "webui", "kaladin")
    assert result["stale_check"] is True
    assert result["behind"] is None
    assert "latest_sha" not in result
    assert "compare_url" not in result


def test_trusted_fetch_ignores_checkout_global_and_environment_instead_of(tmp_path, monkeypatch):
    remote, _source, checkout, first, second = _repo_with_kaladin_history(tmp_path)
    malicious = tmp_path / "malicious.git"
    subprocess.run(["git", "init", "--bare", "-q", str(malicious)], check=True)
    malicious_source = tmp_path / "malicious-source"
    malicious_source.mkdir()
    _git(malicious_source, "init", "-q")
    _git(malicious_source, "config", "user.email", "test@example.com")
    _git(malicious_source, "config", "user.name", "Test")
    _git(malicious_source, "commit", "--allow-empty", "-q", "-m", "malicious")
    _git(malicious_source, "tag", "kaladin-v9.9.9")
    _git(malicious_source, "push", "-q", str(malicious), "refs/tags/kaladin-v9.9.9")

    trusted_url = remote.as_uri()
    _git(checkout, "config", f"url.{malicious.as_uri()}.insteadOf", trusted_url)
    global_config = tmp_path / "global.gitconfig"
    global_config.write_text(
        f"[url \"{malicious.as_uri()}\"]\n\tinsteadOf = {trusted_url}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{malicious.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", trusted_url)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", trusted_url)

    out, ok = updates._fetch_channel_tags(checkout, "kaladin")

    assert ok, out
    assert _trusted_refs(checkout) == {
        "kaladin-v1.0.0": first,
        "kaladin-v1.1.0": second,
    }


def test_malicious_shared_local_tag_is_never_selected(tmp_path, monkeypatch):
    remote, _source, checkout, _first, second = _repo_with_kaladin_history(tmp_path)
    _git(checkout, "tag", "kaladin-v99.0.0", "HEAD")
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())

    out, ok = updates._fetch_channel_tags(checkout, "kaladin")
    assert ok, out
    selected = updates._select_apply_compare_ref(checkout, "kaladin", "webui")

    assert selected == second
    assert selected != _git(checkout, "rev-parse", "kaladin-v99.0.0", capture=True)


def test_deleted_trusted_tag_is_pruned_from_authoritative_namespace(tmp_path, monkeypatch):
    remote, source, checkout, _first, second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    assert updates._fetch_channel_tags(checkout, "kaladin")[1]
    _git(source, "push", "-q", "publish", ":refs/tags/kaladin-v1.1.0")

    out, ok = updates._fetch_channel_tags(checkout, "kaladin")

    assert ok, out
    assert "kaladin-v1.1.0" not in _trusted_refs(checkout)
    assert updates._select_apply_compare_ref(checkout, "kaladin", "webui") is None
    assert second not in _trusted_refs(checkout).values()


def test_moved_trusted_tag_fails_without_changing_authoritative_namespace(tmp_path, monkeypatch):
    remote, source, checkout, first, _second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    assert updates._fetch_channel_tags(checkout, "kaladin")[1]
    before = _trusted_refs(checkout)
    _git(source, "tag", "-f", "kaladin-v1.0.0", "HEAD")
    _git(source, "push", "-q", "--force", "publish", "refs/tags/kaladin-v1.0.0")

    out, ok = updates._fetch_channel_tags(checkout, "kaladin")

    assert not ok
    assert "moved" in out.lower()
    assert _trusted_refs(checkout) == before
    assert _trusted_refs(checkout)["kaladin-v1.0.0"] == first


def test_kaladin_release_uses_trusted_compare_repository(tmp_path, monkeypatch):
    remote, _source, checkout, _first, _second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    assert updates._fetch_channel_tags(checkout, "kaladin")[1]
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", KALADIN_SOURCE)
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


def test_absent_trusted_tags_fail_check_and_apply_closed(tmp_path, monkeypatch):
    remote = tmp_path / "empty.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "installed")
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    monkeypatch.setattr(updates, "REPO_ROOT", repo)

    info = updates._check_repo(repo, "webui", "kaladin")
    result = updates._apply_update_inner("webui", "kaladin")

    assert info["behind"] is None
    assert info["error"]
    assert result["ok"] is False
    assert "kaladin" in result["message"].lower() or "fetch" in result["message"].lower()


def test_kaladin_divergent_release_fails_closed(tmp_path, monkeypatch):
    remote, source, repo, _first, _second = _repo_with_kaladin_history(tmp_path)
    _git(source, "checkout", "-q", "--orphan", "release-line")
    _git(source, "commit", "--allow-empty", "-q", "-m", "divergent release")
    _git(source, "tag", "kaladin-v2.0.0")
    _git(source, "push", "-q", "publish", "refs/tags/kaladin-v2.0.0")
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    assert updates._fetch_channel_tags(repo, "kaladin")[1]

    info = updates._check_repo_release(repo, "webui", "kaladin")

    assert info["behind"] is None
    assert info["channel"] == "kaladin"
    assert "fast-forward" in info["error"].lower()
    assert updates._select_apply_compare_ref(repo, "kaladin", "webui") is None


def test_kaladin_moved_tag_fetch_fails_closed(tmp_path, monkeypatch):
    remote, source, checkout, first, _second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    assert updates._fetch_channel_tags(checkout, "kaladin")[1]
    _git(source, "tag", "-f", "kaladin-v1.0.0", "HEAD")
    _git(source, "push", "-q", "--force", "publish", "refs/tags/kaladin-v1.0.0")

    out, ok = updates._fetch_channel_tags(checkout, "kaladin")

    assert ok is False
    assert out
    assert _git(checkout, "rev-parse", "kaladin-v1.0.0", capture=True) == first


def test_kaladin_normal_apply_fast_forward_merges_selected_tag(tmp_path, monkeypatch):
    remote, _source, checkout, _first, second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    monkeypatch.setattr(updates, "REPO_ROOT", checkout)
    monkeypatch.setattr(updates, "_schedule_restart", MagicMock())

    result = updates._apply_update_inner("webui", "kaladin")

    assert result["ok"] is True
    assert _git(checkout, "rev-parse", "HEAD", capture=True) == second


def test_kaladin_apply_uses_pinned_oid_when_refs_move_after_selection(tmp_path, monkeypatch):
    remote, _source, checkout, first, second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    monkeypatch.setattr(updates, "REPO_ROOT", checkout)
    monkeypatch.setattr(updates, "_schedule_restart", MagicMock())
    real_run_git = updates._run_git
    merge_args = []

    def racing_git(args, cwd, timeout=10):
        if args[:2] == ["merge", "--ff-only"]:
            merge_args.append(list(args))
            subprocess.run(
                ["git", "update-ref", "refs/hermes-webui/kaladin/tags/kaladin-v1.1.0", first],
                cwd=checkout,
                check=True,
            )
            subprocess.run(
                ["git", "tag", "-f", "kaladin-v1.1.0", first],
                cwd=checkout,
                check=True,
                stdout=subprocess.DEVNULL,
            )
        return real_run_git(args, cwd, timeout=timeout)

    monkeypatch.setattr(updates, "_run_git", racing_git)
    result = updates._apply_update_inner("webui", "kaladin")

    assert result["ok"] is True
    assert merge_args == [["merge", "--ff-only", second]]
    assert _git(checkout, "rev-parse", "HEAD", capture=True) == second


def test_kaladin_force_reset_uses_pinned_oid_when_refs_move_after_selection(tmp_path, monkeypatch):
    remote, _source, checkout, first, second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    monkeypatch.setattr(updates, "REPO_ROOT", checkout)
    monkeypatch.setattr(updates, "_schedule_restart", MagicMock())
    monkeypatch.setattr(
        updates,
        "_restart_blocker_snapshot",
        lambda: {"restart_blocked": False, "active_streams": 0, "active_runs": 0},
    )
    real_run_git = updates._run_git
    reset_args = []

    def racing_git(args, cwd, timeout=10):
        if args[:2] == ["reset", "--hard"]:
            reset_args.append(list(args))
            subprocess.run(
                ["git", "update-ref", "refs/hermes-webui/kaladin/tags/kaladin-v1.1.0", first],
                cwd=checkout,
                check=True,
            )
            subprocess.run(
                ["git", "tag", "-f", "kaladin-v1.1.0", first],
                cwd=checkout,
                check=True,
                stdout=subprocess.DEVNULL,
            )
        return real_run_git(args, cwd, timeout=timeout)

    monkeypatch.setattr(updates, "_run_git", racing_git)
    result = updates.apply_force_update("webui", channel="kaladin")

    assert result["ok"] is True
    assert reset_args == [["reset", "--hard", second]]
    assert _git(checkout, "rev-parse", "HEAD", capture=True) == second


def test_concurrent_authoritative_namespace_change_fails_fetch_closed(tmp_path, monkeypatch):
    remote, _source, checkout, first, _second = _repo_with_kaladin_history(tmp_path)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    real_run_git_input = updates._run_git_input

    def racing_update_ref(args, cwd, stdin_text, timeout=10):
        subprocess.run(
            ["git", "update-ref", "refs/hermes-webui/kaladin/tags/kaladin-v1.0.0", first],
            cwd=checkout,
            check=True,
        )
        return real_run_git_input(args, cwd, stdin_text, timeout=timeout)

    monkeypatch.setattr(updates, "_run_git_input", racing_update_ref)
    out, ok = updates._fetch_channel_tags(checkout, "kaladin")

    assert not ok
    assert "concurrently" in out


def test_kaladin_force_refuses_to_abandon_tag_reachable_head(tmp_path, monkeypatch):
    remote, source, checkout, first, _second = _repo_with_kaladin_history(tmp_path)
    _git(source, "checkout", "-q", "--orphan", "release-line")
    _git(source, "commit", "--allow-empty", "-q", "-m", "other")
    _git(source, "tag", "kaladin-v2.0.0")
    _git(source, "push", "-q", "publish", "refs/tags/kaladin-v2.0.0")

    monkeypatch.setattr(updates, "REPO_ROOT", checkout)
    monkeypatch.setitem(updates._CHANNEL_SOURCE_URLS, "kaladin", remote.as_uri())
    monkeypatch.setattr(
        updates,
        "_restart_blocker_snapshot",
        lambda: {"restart_blocked": False, "active_streams": 0, "active_runs": 0},
    )
    result = updates.apply_force_update("webui", channel="kaladin")

    assert result["ok"] is False
    assert result.get("refused_kaladin_checkout") is True, result
    assert "preserve" in result["message"].lower()
    assert _git(checkout, "rev-parse", "HEAD", capture=True) == first


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


def test_frontend_never_advertises_stale_fetch_results():
    ui = (updates.REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    match = re.search(
        r"function _formatUpdateTargetStatus\(label,info\)\{.*?\n\}", ui, re.DOTALL
    )
    assert match
    script = match.group(0) + "\n" + """
const stale={behind:2,error:'fetch failed',stale_check:true,compare_url:'https://example.invalid'};
if(_formatUpdateTargetStatus('WebUI',stale)!==null) throw new Error('stale update advertised');
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


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
