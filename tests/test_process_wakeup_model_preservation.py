"""A server-initiated wakeup continues the session's model; it does not choose one.

Regression that motivated this file: every user turn carries ``explicit_model_pick``
from the browser, but the async-delegation completion wakeup resolved the session's
persisted model through the profile-aware resolver without that flag. When the
session's model belonged to a different provider family than the profile default
(session on ``openai-codex``/``gpt-5.6-sol``, profile default ``deepseek-flash``),
the resolver treated it as a stale cross-family artifact and substituted the profile
default. The wakeup then ran on the default and persisted it, so the session — and
every following turn — was silently downgraded mid-conversation.

These tests drive the real ``start_session_turn`` and assert what the run is started
with, so they fail on the previous resolver call and pass on the fixed one.
"""
from __future__ import annotations

import pytest

from api import models, routes
from api.models import Session

WAKEUP_MESSAGE = "[IMPORTANT: Background process completed.]"


def _make_session(tmp_path, *, model: str, provider: str | None) -> Session:
    session = Session(
        session_id="wakeup_model_preservation",
        title="Wakeup model preservation",
        workspace=str(tmp_path),
        model=model,
        model_provider=provider,
        messages=[{"role": "user", "content": "Earlier prompt", "timestamp": 1}],
        context_messages=[{"role": "user", "content": "Earlier prompt"}],
    )
    session.save()
    models.SESSIONS[session.session_id] = session
    return session


def _drive_wakeup(monkeypatch, tmp_path, session: Session):
    """Run one process wakeup and capture the route the run was started with."""
    captured: dict[str, object] = {}

    def _capture_start_run(
        _session,
        *,
        msg,
        attachments,
        workspace,
        model,
        model_provider,
        normalized_model,
        source,
        route,
        **_kwargs,
    ):
        captured.update(
            model=model,
            model_provider=model_provider,
            source=source,
            msg=msg,
        )
        return {"_status": 200, "stream_id": "stream-wakeup-model", "started": True}

    monkeypatch.setattr(
        routes, "_resolve_chat_workspace_with_recovery", lambda _s, _w: str(tmp_path)
    )
    # The owning profile whose default differs from the session's route: this is the
    # context the wakeup threads into the resolver.
    monkeypatch.setattr(
        routes, "_read_profile_model_config", lambda _s, _p: ("deepseek", "deepseek-flash", {})
    )
    # The resolver also consults the provider catalogue, and where that catalogue
    # names ``deepseek`` as the active provider is exactly the state a session on
    # another provider is measured against. Without it the resolver has no active
    # provider to compare with and takes a different branch, so the shape below
    # mirrors the live catalogue this profile resolves against.
    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda *_a, **_k: {
            "active_provider": "deepseek",
            "default_model": "deepseek-flash",
            "aliases": {},
            "configured_model_badges": {},
            "groups": {},
        },
    )
    monkeypatch.setattr(routes, "_start_run", _capture_start_run)

    response = routes.start_session_turn(session.session_id, WAKEUP_MESSAGE, source="process_wakeup")
    return response, captured


def test_wakeup_keeps_the_session_on_its_own_provider(tmp_path, monkeypatch):
    """The reported regression: session on openai-codex, profile default deepseek.

    The picked-model shape the UI stores is provider-qualified
    (``@openai-codex:gpt-5.6-sol``), and that is the shape the resolver rewrites
    to the profile default unless the turn is marked an explicit pick.
    """
    session = _make_session(tmp_path, model="@openai-codex:gpt-5.6-sol", provider="openai-codex")

    response, captured = _drive_wakeup(monkeypatch, tmp_path, session)

    assert response.get("_status") == 200, response
    assert captured["model"] == "@openai-codex:gpt-5.6-sol", (
        "a server-initiated wakeup must not rewrite the session's model to the "
        f"profile default; run started with {captured['model']!r}"
    )
    assert captured["model_provider"] == "openai-codex"


def test_wakeup_keeps_a_bare_session_model_on_its_own_provider(tmp_path, monkeypatch):
    """A session stored without the provider qualifier keeps its route too."""
    session = _make_session(tmp_path, model="gpt-5.6-sol", provider="openai-codex")

    response, captured = _drive_wakeup(monkeypatch, tmp_path, session)

    assert response.get("_status") == 200, response
    assert captured["model"] == "gpt-5.6-sol"
    assert captured["model_provider"] == "openai-codex"


def test_wakeup_still_resolves_an_empty_session_to_the_profile_default(tmp_path, monkeypatch):
    """The behaviour the profile defaults were threaded through for: a session that
    spawned a background task before its first human turn has no model of its own,
    and that is the case allowed to adopt the profile default."""
    session = _make_session(tmp_path, model="", provider=None)

    response, captured = _drive_wakeup(monkeypatch, tmp_path, session)

    assert response.get("_status") == 200, response
    assert captured["model"] == "deepseek-flash"
    assert captured["model_provider"] == "deepseek"
