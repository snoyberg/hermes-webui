"""Internal standalone workers are retained for diagnostics, not sidebar lists."""
import json
import sqlite3
from unittest import mock

import pytest

from api import agent_sessions, gateway_watcher, models


@pytest.fixture
def worker_db(tmp_path):
    db = tmp_path / "state.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, title TEXT,
                model TEXT, started_at REAL, message_count INTEGER,
                parent_session_id TEXT, project_id TEXT);
            CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                role TEXT, content TEXT, timestamp REAL);
            CREATE INDEX messages_session_timestamp ON messages(session_id, timestamp);
        """)
        for i, (sid, source, parent) in enumerate([
            ("human", "cli", None), ("child", "subagent", "human"),
            ("worker", "tool", None), ("assigned-worker", "tool", None),
        ]):
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?, 'model', ?, 1, ?, ?)",
                         (sid, source, sid, i + 1, parent,
                          "project" if sid == "assigned-worker" else None))
            conn.execute("INSERT INTO messages VALUES (?, ?, 'user', 'retained', ?)",
                         (i, sid, i + 1))
    return db


def test_default_projection_hides_workers_but_diagnostics_keep_transcripts(worker_db):
    rows = agent_sessions.read_importable_agent_session_rows(worker_db, limit=None)
    assert {r['id'] for r in rows} == {'human', 'child'}
    diagnostic = agent_sessions.read_importable_agent_session_rows(
        worker_db, include_sources=('tool',), exclude_sources=None)
    assert {r['id'] for r in diagnostic} == {'worker', 'assigned-worker'}
    with sqlite3.connect(worker_db) as conn:
        assert conn.execute("SELECT content FROM messages WHERE session_id='worker'").fetchone() == ('retained',)


def test_cli_loader_excludes_workers_including_project_recovery(worker_db, tmp_path):
    with (
        mock.patch.object(models, 'get_claude_code_sessions', return_value=[]),
        mock.patch.object(models, 'get_last_workspace', return_value=tmp_path),
        mock.patch.object(models, 'load_projects', return_value=[{'id': 'project'}]),
        mock.patch.object(models.Session, 'load_metadata_only', return_value=None),
    ):
        rows = models._load_cli_sessions_uncached(tmp_path, worker_db, _cli_profile=None)
        diagnostic = models._load_cli_sessions_uncached(
            tmp_path, worker_db, _cli_profile=None, source_filter='tool')
    assert {r['session_id'] for r in diagnostic} == {'worker', 'assigned-worker'}
    assert 'human' in {r['session_id'] for r in rows}
    assert not {'worker', 'assigned-worker'} & {r['session_id'] for r in rows}


def test_watcher_ignores_worker_churn_but_detects_human_changes(worker_db):
    assert {r['session_id'] for r in gateway_watcher._get_agent_sessions_from_db(worker_db)} == {'human', 'child'}
    before = gateway_watcher._cheap_change_fingerprint(worker_db)
    assert before is not None
    with sqlite3.connect(worker_db) as conn:
        conn.execute("UPDATE sessions SET title='changed', message_count=2 WHERE source='tool'")
        conn.execute("INSERT INTO messages VALUES (99, 'worker', 'assistant', 'done', 99)")
    assert gateway_watcher._cheap_change_fingerprint(worker_db) == before
    with sqlite3.connect(worker_db) as conn:
        conn.execute("UPDATE sessions SET title='human changed' WHERE id='human'")
    assert gateway_watcher._cheap_change_fingerprint(worker_db) != before


@pytest.mark.parametrize('indexed', [False, True])
@pytest.mark.parametrize('lineage', [False, True])
def test_old_imported_sidecars_never_rescue_workers(worker_db, tmp_path, monkeypatch, indexed, lineage):
    directory = tmp_path / 'sessions'
    directory.mkdir()
    index = directory / '_index.json'
    monkeypatch.setattr(models, 'SESSION_DIR', directory)
    monkeypatch.setattr(models, 'SESSION_INDEX_FILE', index)
    monkeypatch.setattr(models, 'SESSIONS', {})
    monkeypatch.setattr(models, '_active_state_db_path', lambda: worker_db)
    monkeypatch.setenv('HERMES_WEBUI_LINEAGE_TOP_N', '1')
    monkeypatch.setattr(models, '_start_session_index_rebuild_thread', lambda: None)
    monkeypatch.setattr(models, '_persisted_session_ids_snapshot', lambda: {'human', 'worker', 'child'})
    entries = []
    for sid, source in [('human', 'cli'), ('worker', 'cli'), ('child', 'subagent')]:
        # The worker's legacy sidecar has stale CLI classification: state.db owns source.
        session = models.Session(session_id=sid, title=sid, source_tag=source,
                                 raw_source=source, is_cli_session=source == 'cli',
                                 project_id='project', messages=[{'role': 'user', 'content': 'retained'}])
        session.save(skip_index=True)
        entries.append(session.compact())
    if indexed:
        index.write_text(json.dumps(entries))
    rows = models.all_sessions(include_lineage_metadata=lineage)
    assert {r['session_id'] for r in rows} == {'human', 'child'}
    assert (directory / 'worker.json').exists()
    assert models.Session.load('worker') is not None
