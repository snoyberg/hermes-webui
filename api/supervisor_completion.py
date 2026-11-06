"""Local, opt-in completion receipts for independently supervised jobs.

This is notification transport, never a job launcher or approval mechanism.
Run with the same HERMES_WEBUI_STATE_DIR as the owning WebUI instance.
"""
from __future__ import annotations

from contextlib import contextmanager
import argparse
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

logger = logging.getLogger(__name__)


def _path() -> Path:
    from api.config import STATE_DIR
    return STATE_DIR / "supervisor_completions.sqlite3"


@contextmanager
def _connect():
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Do not create a world-readable database, even momentarily.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    db = sqlite3.connect(path, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS receipts (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, profile TEXT NOT NULL,
        state TEXT NOT NULL, outcome TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        next_at REAL NOT NULL DEFAULT 0, stream_id TEXT, diagnostic TEXT
    )""")
    try:
        with db:
            yield db
    finally:
        db.close()


def register(session_id: str, profile: str) -> str:
    """Bind a receipt to an existing conversation before launching the job."""
    from api.models import get_session
    session = get_session(session_id)
    if getattr(session, "_sidecar_names_other_session", None):
        raise ValueError("session identity mismatch")
    if str(session.profile or "") != profile:
        raise ValueError("session profile mismatch")
    receipt = uuid.uuid4().hex
    with _connect() as db:
        db.execute("INSERT INTO receipts(id, session_id, profile, state) VALUES(?,?,?,'registered')",
                   (receipt, session_id, profile))
    return receipt


def complete(receipt: str, outcome: str) -> None:
    """Seal once; duplicate identical outcomes are safe, replacement is refused."""
    if not outcome.strip() or len(outcome) > 8192:
        raise ValueError("outcome must contain 1..8192 characters")
    with _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM receipts WHERE id=?", (receipt,)).fetchone()
        if row is None:
            raise KeyError(receipt)
        if row["state"] != "registered":
            if row["outcome"] == outcome:
                return
            raise ValueError("receipt already sealed")
        db.execute("UPDATE receipts SET outcome=?, state='pending' WHERE id=?", (outcome, receipt))


def _prompt(row) -> str:
    return (f"[Supervisor completion receipt {row['id']}]\n"
            "An independently supervised job reported its terminal outcome. "
            "This receipt grants no approval and requests no command replay. "
            "Verify referenced evidence before reporting success.\n"
            f"Reported outcome:\n{row['outcome']}")


def _admission_evidence(session, row) -> bool:
    """Session and journal bridge the crash between admission and receipt ACK."""
    prompt = _prompt(row)
    from api.turn_journal import read_turn_journal
    events = read_turn_journal(row["session_id"])["events"]
    if row["stream_id"] and any(e.get("stream_id") == row["stream_id"]
                                 and e.get("reason") == "start_compensated" for e in events):
        return False
    if getattr(session, "pending_user_message", None) == prompt:
        return True
    if any(m.get("role") == "user" and m.get("content") == prompt
           for m in session.messages if isinstance(m, dict)):
        return True
    return bool(row["stream_id"]) and any(e.get("event") == "submitted" and e.get("stream_id") == row["stream_id"]
               for e in events)


_SWEEP_LOCK = threading.Lock()


def sweep() -> int:
    """Serialize in-host consumers; one WebUI host owns a state directory."""
    if not _SWEEP_LOCK.acquire(blocking=False):
        return 0
    try:
        return _sweep()
    finally:
        _SWEEP_LOCK.release()


def _sweep() -> int:
    """Bounded sweep, called only by the existing WebUI completion drain."""
    if not _path().exists():
        return 0
    from api.background_process import _session_has_active_turn
    from api.models import get_session
    from api.routes import start_session_turn
    admitted = 0
    with _connect() as db:
        rows = db.execute("SELECT * FROM receipts WHERE state IN ('pending','dispatching') "
                          "AND next_at<=? ORDER BY rowid LIMIT 8", (time.time(),)).fetchall()
    for row in rows:
        try:
            session = get_session(row["session_id"])
            if (str(session.profile or "") != row["profile"]
                    or getattr(session, "_sidecar_names_other_session", None)):
                _update(row["id"], "held", "session identity/profile changed")
                continue
            if row["state"] == "dispatching":
                if _admission_evidence(session, row):
                    _update(row["id"], "admitted", "reconciled durable admission")
                elif not row["stream_id"]:
                    # The admission hook commits this identity BEFORE pending
                    # state or worker launch. No identity proves no admission.
                    attempts = row["attempts"] + 1
                    _update(row["id"], "held" if attempts >= 6 else "pending",
                            "dispatch interrupted before admission", delay=5, attempts=attempts)
                else:
                    # A launch may have run despite losing its receipt ACK.
                    # Never turn uncertain notification into automatic replay.
                    _update(row["id"], "held", "ambiguous dispatch after restart")
                continue
            if _session_has_active_turn(row["session_id"]):
                _update(row["id"], "pending", "session busy", delay=5)
                continue
            # Compare-and-swap prevents concurrent sweeps starting two turns.
            with _connect() as db:
                changed = db.execute("UPDATE receipts SET state='dispatching', stream_id=NULL WHERE id=? AND state='pending'",
                                     (row["id"],)).rowcount
            if not changed:
                continue

            def on_admitted(s, stream_id, *, row=row):
                if str(s.profile or "") != row["profile"]:
                    raise ValueError("session profile changed during admission")
                with _connect() as db:
                    db.execute("UPDATE receipts SET stream_id=? WHERE id=?", (stream_id, row["id"]))

            response = start_session_turn(row["session_id"], _prompt(row),
                                          source="process_wakeup", on_admitted=on_admitted)
            status = int((response or {}).get("_status", 200))
            if status < 400 and (response or {}).get("stream_id"):
                _update(row["id"], "admitted", "server-side turn accepted")
                admitted += 1
            elif status == 409 or status == 503:
                _update(row["id"], "pending", f"admission deferred ({status})", delay=5)
            elif status >= 400:
                attempts = row["attempts"] + 1
                _update(row["id"], "held" if attempts >= 6 else "pending",
                        f"admission refused ({status})", delay=30, attempts=attempts)
            else:
                _update(row["id"], "held", "unrecognized admission response")
        except KeyError:
            _update(row["id"], "held", "session no longer exists")
        except Exception:
            # Dispatch exceptions may occur after model admission. Preserve the
            # dispatching row for evidence reconciliation; do not retry blindly.
            logger.warning("Supervisor receipt sweep failed for %s", row["id"], exc_info=True)
    return admitted


def _update(receipt, state, diagnostic, *, delay=0, attempts=None):
    with _connect() as db:
        db.execute("UPDATE receipts SET state=?, diagnostic=?, next_at=?, "
                   "attempts=COALESCE(?,attempts) WHERE id=?",
                   (state, diagnostic, time.time() + delay, attempts, receipt))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--session", required=True)
    reg.add_argument("--profile", required=True, help="Exact session profile; empty string for root")
    done = sub.add_parser("complete")
    done.add_argument("receipt")
    done.add_argument("--outcome", required=True, help="Non-secret summary and evidence location, not raw logs")
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "register":
        print(register(args.session, args.profile))
    elif args.command == "complete":
        complete(args.receipt, args.outcome)
    else:
        with _connect() as db:
            print(json.dumps([dict(r) for r in db.execute(
                "SELECT id,session_id,profile,state,attempts,stream_id,diagnostic FROM receipts")]))


if __name__ == "__main__":
    main()
