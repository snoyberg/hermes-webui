"""Coverage for per-cron completion toast notification settings."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
PANELS_JS = (REPO / "static" / "panels.js").read_text(encoding="utf-8")
I18N_JS = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")


class _JSONHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.response_headers = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass


def _payload(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def _function_body(name: str) -> str:
    marker = f"function {name}("
    start = PANELS_JS.find(marker)
    assert start != -1, f"{name} not found"
    paren = PANELS_JS.find("(", start)
    assert paren != -1, f"{name} params not found"
    depth = 0
    for idx in range(paren, len(PANELS_JS)):
        ch = PANELS_JS[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                brace = PANELS_JS.find("{", idx)
                break
    else:
        raise AssertionError(f"{name} params did not terminate")
    assert brace != -1, f"{name} body not found"
    depth = 0
    for idx in range(brace, len(PANELS_JS)):
        ch = PANELS_JS[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return PANELS_JS[brace + 1 : idx]
    raise AssertionError(f"{name} body did not terminate")


def test_cron_recent_marks_muted_jobs_without_requesting_toast(monkeypatch):
    import api.routes as routes

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.list_jobs = lambda include_disabled=True: [
        {
            "id": "loud",
            "name": "Loud job",
            "last_run_at": 20,
            "last_status": "success",
        },
        {
            "id": "muted",
            "name": "Muted job",
            "last_run_at": 30,
            "last_status": "success",
            "toast_notifications": False,
        },
    ]
    monkeypatch.setattr(
        routes,
        "_latest_cron_session_info_for_jobs",
        lambda job_ids, completed_job_ids=None: {
            str(job_id): {
                "session_id": f"cron_{job_id}_latest",
                "message_count": 3 if str(job_id) == "loud" else 5,
            }
            for job_id in (completed_job_ids or job_ids)
        },
    )
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_recent(handler, SimpleNamespace(query="since=10"))

    body = _payload(handler)
    assert handler.status == 200
    by_id = {item["job_id"]: item for item in body["completions"]}
    assert by_id["loud"]["toast_notifications"] is True
    assert by_id["loud"]["session_id"] == "cron_loud_latest"
    assert by_id["loud"]["message_count"] == 3
    assert by_id["muted"]["toast_notifications"] is False
    assert by_id["muted"]["session_id"] == "cron_muted_latest"
    assert by_id["muted"]["message_count"] == 5


def test_cron_create_persists_muted_toast_setting_after_create(monkeypatch):
    import api.routes as routes

    created = {"id": "job-toast", "name": "Muted", "prompt": "ping"}
    calls = []
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.create_job = lambda **kwargs: calls.append(("create", kwargs)) or dict(created)
    cron_jobs.update_job = lambda job_id, updates: calls.append(("update", job_id, updates)) or {**created, **updates}
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_create(
        handler,
        {
            "prompt": "ping",
            "schedule": "every 1h",
            "toast_notifications": False,
            "completion_attention": "failures",
        },
    )

    assert handler.status == 200
    assert calls[0][0] == "create"
    assert calls[1] == (
        "update",
        "job-toast",
        {"toast_notifications": False, "completion_attention": "failures"},
    )
    assert _payload(handler)["job"]["toast_notifications"] is False
    assert _payload(handler)["job"]["completion_attention"] == "failures"


def test_cron_form_has_toast_toggle_and_saves_boolean_setting():
    render_body = _function_body("_renderCronForm")
    save_body = _function_body("saveCronForm")
    edit_body = _function_body("openCronEdit")
    detail_body = _function_body("_renderCronDetail")

    assert "cronFormToastNotifications" in render_body
    assert "cronFormCompletionAttention" in render_body
    assert "cron_toast_notifications_label" in render_body
    assert "completion_attention" in edit_body
    assert "toast_notifications" in detail_body
    assert "const toastNotifications" in save_body
    assert "toast_notifications: toastNotifications" in save_body


def test_cron_completion_attention_policy_is_returned_and_persisted(monkeypatch):
    import api.routes as routes

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.list_jobs = lambda include_disabled=True: [
        {
            "id": "failures-only",
            "name": "Failures only",
            "last_run_at": 20,
            "last_status": "success",
            "completion_attention": "failures",
        },
        {
            "id": "legacy",
            "name": "Legacy",
            "last_run_at": 30,
            "last_status": "error",
        },
    ]
    monkeypatch.setattr(routes, "_latest_cron_session_info_for_jobs", lambda *_: {})
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_recent(handler, SimpleNamespace(query="since=10"))

    by_id = {item["job_id"]: item for item in _payload(handler)["completions"]}
    assert by_id["failures-only"]["completion_attention"] == "failures"
    assert by_id["legacy"]["completion_attention"] == "all"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_cron_polling_marks_only_error_completions_for_failures_policy():
    body = _function_body("startCronPolling")
    script = f"""
const src = {json.dumps(body)};
const _cronNewJobIds = new Set();
let _cronPollSince = 10;
let _cronPollTimer = null;
let _cronPollGeneration = 0;
global.document = {{ hidden: false }};
global.setInterval = (fn) => {{ global.tick = fn; return 1; }};
async function api() {{ return {{ completions: [
  {{job_id:'success', status:'success', completed_at:20, completion_attention:'failures'}},
  {{job_id:'failure', status:'error', completed_at:30, completion_attention:'failures'}},
  {{job_id:'muted', status:'error', completed_at:40, completion_attention:'never'}}
] }}; }}
function showToast() {{}}
function t() {{ return ''; }}
function updateCronBadge() {{}}
function _markSessionCompletionUnreadIfBackground() {{ throw new Error('unexpected session unread'); }}
eval('async function startCronPolling(){{' + src + '}}');
(async()=>{{ startCronPolling(); await global.tick(); console.log(JSON.stringify({{unread:[..._cronNewJobIds], since:_cronPollSince}})); }})().catch(e=>{{console.error(e);process.exit(1);}});
"""
    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "cron-poll.js"
        script_path.write_text(script, encoding="utf-8")
        proc = __import__("subprocess").run(
            ["node", str(script_path)], capture_output=True, text=True, check=False
        )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {"unread": ["failure"], "since": 40}


def test_cron_toast_i18n_keys_exist():
    assert "cron_toast_notifications_label" in I18N_JS
    assert "cron_toast_notifications_hint" in I18N_JS
    assert "cron_toast_notifications_enabled" in I18N_JS
    assert "cron_toast_notifications_disabled" in I18N_JS
