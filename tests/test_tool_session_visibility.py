"""Regression coverage for internal tool-source session visibility."""

import sqlite3

import pytest

import api.models as models


def _make_state_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            model TEXT,
            message_count INTEGER,
            started_at REAL,
            source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp REAL
        )
        """
    )
    rows = [
        ("interactive-cli", "Interactive work", "cli", 2),
        ("internal-tool", "Internal integration work", "tool", 2),
    ]
    for sid, title, source, message_count in rows:
        conn.execute(
            "INSERT INTO sessions (id, title, model, message_count, started_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, title, "gpt-x", message_count, 1700000000.0, source),
        )
        for offset in range(message_count):
            conn.execute(
                "INSERT INTO messages (session_id, timestamp) VALUES (?, ?)",
                (sid, 1700000001.0 + offset),
            )
    conn.commit()
    conn.close()


def _make_tool_saturated_state_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT,
            model TEXT,
            message_count INTEGER,
            started_at REAL,
            source TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp REAL
        )
        """
    )
    interactive_started = 1700000000.0
    conn.execute(
        "INSERT INTO sessions (id, title, model, message_count, started_at, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("older-interactive", "Older interactive work", "gpt-x", 1, interactive_started, "cli"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, timestamp) VALUES (?, ?)",
        ("older-interactive", interactive_started + 1),
    )
    for index in range(models.CLI_VISIBLE_SESSION_LIMIT + 1):
        sid = f"newer-tool-{index:02d}"
        started_at = interactive_started + 100 + index
        conn.execute(
            "INSERT INTO sessions (id, title, model, message_count, started_at, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sid, "Internal integration work", "gpt-x", 1, started_at, "tool"),
        )
        conn.execute(
            "INSERT INTO messages (session_id, timestamp) VALUES (?, ?)",
            (sid, started_at + 1),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def fake_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    _make_state_db(home / "state.db")

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: None)
    return home


def test_tool_source_sessions_are_absent_from_user_sidebar_projection(fake_hermes_home):
    sessions = models.get_cli_sessions()

    assert [session["session_id"] for session in sessions] == ["interactive-cli"]


def test_tool_sessions_do_not_consume_bounded_interactive_window(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    _make_tool_saturated_state_db(home / "state.db")

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: home)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: None)

    sessions = models.get_cli_sessions()

    assert [session["session_id"] for session in sessions] == ["older-interactive"]


def test_tool_source_sessions_remain_available_to_explicit_diagnostic_filter(fake_hermes_home):
    sessions = models.get_cli_sessions(source_filter="tool")

    assert [session["session_id"] for session in sessions] == ["internal-tool"]


def test_gateway_watcher_projection_excludes_tool_sessions(fake_hermes_home):
    from api import gateway_watcher

    sessions = gateway_watcher._get_agent_sessions_from_db(fake_hermes_home / "state.db")

    assert [session["session_id"] for session in sessions] == ["interactive-cli"]


def test_previously_imported_tool_sidecar_is_absent_from_sidebar_payload(monkeypatch):
    from api import routes

    tool_sidecar = {
        "session_id": "previously-imported-tool",
        "title": "Internal integration work",
        "profile": "default",
        "updated_at": 1700000002.0,
        "last_message_at": 1700000002.0,
        "message_count": 2,
        "source": "tool",
        "source_tag": "tool",
        "raw_source": "tool",
        "session_source": "other",
        "source_label": "Tool",
        "is_cli_session": False,
    }
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: [tool_sidecar])
    monkeypatch.setattr(
        routes,
        "get_cli_sessions",
        lambda source_filter=None, all_profiles=False: [],
    )
    monkeypatch.setattr(
        routes,
        "_reconcile_stale_stream_state_for_session_rows",
        lambda _sessions: False,
    )
    monkeypatch.setattr(
        routes,
        "agent_session_rows_existing",
        lambda ids, profile=None: frozenset({"previously-imported-tool"}),
    )

    payload = routes._build_session_list_cache_payload(
        active_profile="default",
        all_profiles=False,
        show_cli_sessions=True,
        show_previous_messaging_sessions=False,
        show_cron_sessions=False,
    )

    assert payload["sessions"] == []
