"""Receipt lifecycle coverage; no production state, providers or push sends."""
from types import SimpleNamespace

import pytest

from api import background_process as bp, models, routes
from api import supervisor_completion as sc


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_path", lambda: tmp_path / "receipts.sqlite3")
    session = SimpleNamespace(profile="work", messages=[], pending_user_message=None)
    monkeypatch.setattr(models, "get_session", lambda sid: session if sid == "owner" else None)
    monkeypatch.setattr(bp, "_session_has_active_turn", lambda sid: False)
    calls = []

    def admit(sid, prompt, **kwargs):
        calls.append((sid, prompt))
        kwargs["on_admitted"](session, "stream")
        session.messages.append({"role": "user", "content": prompt})
        return {"stream_id": "stream", "_status": 200}

    monkeypatch.setattr(routes, "start_session_turn", admit)
    return session, calls


def state(receipt):
    with sc._connect() as db:
        return dict(db.execute("SELECT * FROM receipts WHERE id=?", (receipt,)).fetchone())


def test_duplicate_and_owner(env):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "Verified: evidence/location")
    sc.complete(receipt, "Verified: evidence/location")
    with pytest.raises(ValueError):
        sc.complete(receipt, "replacement")
    assert sc.sweep() == 1
    assert sc.sweep() == 0
    assert len(env[1]) == 1 and env[1][0][0] == "owner"
    assert state(receipt)["state"] == "admitted"
    assert sc._path().stat().st_mode & 0o077 == 0


def test_busy_then_idle(env, monkeypatch):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    monkeypatch.setattr(bp, "_session_has_active_turn", lambda sid: True)
    assert sc.sweep() == 0 and not env[1]
    assert state(receipt)["state"] == "pending"
    monkeypatch.setattr(bp, "_session_has_active_turn", lambda sid: False)
    with sc._connect() as db:
        db.execute("UPDATE receipts SET next_at=0")
    assert sc.sweep() == 1


def test_profile_mismatch_never_wakes(env):
    with pytest.raises(ValueError):
        sc.register("owner", "other")
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    env[0].profile = "other"
    assert sc.sweep() == 0 and not env[1]
    assert state(receipt)["state"] == "held"


def test_deleted_session_never_wakes(env, monkeypatch):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    def gone(sid):
        raise KeyError(sid)
    monkeypatch.setattr(models, "get_session", gone)
    assert sc.sweep() == 0 and not env[1]
    assert state(receipt)["state"] == "held"


@pytest.mark.parametrize("status", [409, 503])
def test_transient_refusal_retained(env, monkeypatch, status):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    monkeypatch.setattr(routes, "start_session_turn", lambda *a, **k: {"_status": status})
    assert sc.sweep() == 0
    assert state(receipt)["state"] == "pending"
    assert state(receipt)["next_at"] > 0
    assert state(receipt)["attempts"] == 0


def test_bounded_failure(env, monkeypatch):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    monkeypatch.setattr(routes, "start_session_turn", lambda *a, **k: {"_status": 500})
    for _ in range(6):
        with sc._connect() as db:
            db.execute("UPDATE receipts SET next_at=0")
        sc.sweep()
    assert state(receipt)["state"] == "held"
    assert state(receipt)["attempts"] == 6


def test_crash_after_admission_reconciles_no_duplicate(env, monkeypatch):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    real_update = sc._update
    def crash(receipt, status, *args, **kwargs):
        if status == "admitted":
            raise OSError("lost ACK")
        return real_update(receipt, status, *args, **kwargs)
    monkeypatch.setattr(sc, "_update", crash)
    assert sc.sweep() == 0
    assert state(receipt)["state"] == "dispatching"
    monkeypatch.setattr(sc, "_update", real_update)
    assert sc.sweep() == 0
    assert state(receipt)["state"] == "admitted"
    assert len(env[1]) == 1


def test_interrupted_before_admission_retries(env):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    with sc._connect() as db:
        db.execute("UPDATE receipts SET state='dispatching' WHERE id=?", (receipt,))
    assert sc.sweep() == 0
    assert state(receipt)["state"] == "pending"
    with sc._connect() as db:
        db.execute("UPDATE receipts SET next_at=0")
    assert sc.sweep() == 1
    assert len(env[1]) == 1


def test_uncertain_dispatch_is_held_not_replayed(env, monkeypatch):
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    def fail(*a, **k):
        k["on_admitted"](env[0], "ambiguous-stream")
        raise OSError("ambiguous")
    monkeypatch.setattr(routes, "start_session_turn", fail)
    assert sc.sweep() == 0
    assert state(receipt)["state"] == "dispatching"
    assert sc.sweep() == 0
    assert state(receipt)["state"] == "held" and not env[1]


def test_compensated_journal_is_not_admission(env, monkeypatch):
    from api import turn_journal
    receipt = sc.register("owner", "work")
    sc.complete(receipt, "done")
    with sc._connect() as db:
        db.execute("UPDATE receipts SET state='dispatching',stream_id='s'")
    monkeypatch.setattr(turn_journal, "read_turn_journal", lambda sid: {"events": [
        {"event": "submitted", "stream_id": "s"},
        {"event": "interrupted", "stream_id": "s", "reason": "start_compensated"}]})
    assert sc.sweep() == 0
    assert state(receipt)["state"] == "held" and not env[1]


def test_concurrent_sealing_cannot_replace_outcome(env):
    from concurrent.futures import ThreadPoolExecutor
    receipt = sc.register("owner", "work")
    def seal(outcome):
        try:
            sc.complete(receipt, outcome)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(seal, [str(i) for i in range(8)]))
    assert sum(results) == 1
