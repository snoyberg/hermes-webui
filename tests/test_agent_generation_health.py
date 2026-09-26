"""Agent source and dependency generation health evidence."""
import json
import subprocess
from types import SimpleNamespace
from urllib.parse import urlparse

import api.routes as routes
from api import runtime_identity


def test_loaded_agent_generation_requires_exact_committed_source_and_dependency_fact(tmp_path):
    agent = tmp_path / "agent"
    agent.mkdir()
    subprocess.run(["git", "init", "-q", str(agent)], check=True)
    subprocess.run(["git", "-C", str(agent), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(agent), "config", "user.name", "Test"], check=True)
    (agent / "run_agent.py").write_text("# source\n")
    subprocess.run(["git", "-C", str(agent), "add", "run_agent.py"], check=True)
    subprocess.run(["git", "-C", str(agent), "commit", "-qm", "source"], check=True)
    rev = subprocess.check_output(["git", "-C", str(agent), "rev-parse", "HEAD"], text=True).strip()
    installed = tmp_path / "generation" / "venv"
    installed.mkdir(parents=True)
    lock = installed.parent / "workspace" / "uv.lock"
    lock.parent.mkdir()
    lock.write_text("resolved dependencies")
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"packages": {"venv": {"environment": str(installed), "resolved_lock": str(lock)}}}))
    value = runtime_identity._capture_agent_generation(agent / "run_agent.py", facts, installed)
    assert value is not None
    assert value["source_revision"] == rev
    assert value["environment"] == str(installed)
    assert len(value["dependency_lock_sha256"]) == 64
    (agent / "run_agent.py").write_text("# changed after commit\n")
    assert runtime_identity._capture_agent_generation(agent / "run_agent.py", facts, installed) is None
    (agent / "run_agent.py").write_text("# source\n")
    assert runtime_identity._capture_agent_generation(agent / "run_agent.py", facts, tmp_path / "other") is None
    lock.unlink()
    assert runtime_identity._capture_agent_generation(agent / "run_agent.py", facts, installed) is None


def test_health_only_reports_captured_agent_generation(monkeypatch):
    responses = []
    monkeypatch.setattr(routes, "_streams_lock_health", lambda: {"status": "ok", "active_streams": 0})
    monkeypatch.setattr(routes, "_run_lifecycle_health", lambda: {"active_runs": 0, "runs": []})
    monkeypatch.setattr(routes, "_accept_loop_health", lambda _: {"status": "ok"})
    monkeypatch.setattr(routes, "j", lambda _, payload, **kwargs: responses.append(payload))
    generation = {"source_revision": "a" * 40, "environment": "/isolated/venv",
                  "dependency_lock_sha256": "b" * 64}
    monkeypatch.setattr(routes, "AGENT_GENERATION", generation)
    routes._handle_health(SimpleNamespace(), urlparse("/health"))
    assert responses[-1]["agent_generation"] == generation
    monkeypatch.setattr(routes, "AGENT_GENERATION", None)
    routes._handle_health(SimpleNamespace(), urlparse("/health"))
    assert "agent_generation" not in responses[-1]
