"""Isolated restart/admission probe; worker is an explicit recording fixture.

No provider response is synthesized. Real server boot/drain/admission/session
persistence run; this does NOT prove a model API call or live serving acceptance.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

repo = Path(__file__).resolve().parents[2]
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
python = sys.executable
import uuid
state = root / ('completion-193-restart-state-' + uuid.uuid4().hex[:8])
state.mkdir(exist_ok=True)
(state / 'webui' / 'sessions').mkdir(parents=True, exist_ok=True)
workspace = state / 'workspace'
workspace.mkdir(exist_ok=True)
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
env = {k:v for k,v in os.environ.items() if not any(x in k for x in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'AUTH'))}
env.update(HERMES_HOME=str(state), HERMES_BASE_HOME=str(state),
           HERMES_CONFIG_PATH=str(state/'config.yaml'), HERMES_WEBUI_STATE_DIR=str(state/'webui'),
           HERMES_WEBUI_HOST='127.0.0.1', HERMES_WEBUI_PORT=str(port),
           HERMES_WEBUI_AGENT_DIR=os.environ['HERMES_WEBUI_AGENT_DIR'],
           HERMES_WEBUI_DEFAULT_WORKSPACE=str(workspace), HERMES_WEBUI_TEST_NETWORK_BLOCK='1',
           PYTHONPATH=str(repo))
env['HERMES_WEBUI_PASSWORD'] = ''
(state/'config.yaml').write_text('model:\n  default: openai/gpt-4.1-nano\n  provider: openai\n')
host = '''import server
from api import routes
from pathlib import Path
import json, os

def recording_worker(*args, **kwargs):
    # Only the agent worker is a fixture; normal admission already persisted
    # its pending turn and submitted journal before this function is reached.
    s = routes.get_session(args[0])
    Path(os.environ['HERMES_HOME'], 'worker-admitted.json').write_text(json.dumps({
        'session_id': s.session_id, 'profile': s.profile,
        'pending': s.pending_user_message, 'stream_id': s.active_stream_id}))
routes._run_agent_streaming = recording_worker
server.main()
'''

def run(code):
    p = subprocess.run([python, '-c', code], cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise RuntimeError(p.stdout+p.stderr)
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''

def start(label):
    log = open(root/f'completion-193-{label}.log','w')
    p = subprocess.Popen([python,'-c',host], cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        if p.poll() is not None:
            raise RuntimeError(f'host failed {p.returncode}; see {label} log')
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as r:
                if r.status == 200:
                    return p,log
        except Exception:
            time.sleep(.1)
    p.kill();p.wait()
    raise RuntimeError('boot timeout')

receipt = run("from api.models import Session; from api.supervisor_completion import register; "
              f"s=Session(session_id='receipt-owner',profile='default',workspace={str(workspace)!r},model='openai/gpt-4.1-nano',model_provider='openai'); s.save(); "
              "Session(session_id='decoy',profile='default').save(); print(register(s.session_id,'default'))")
p, log = start('before-restart')
old_pid = p.pid
# SIGKILL models disappearance of the owning process, not graceful queue flush.
p.kill(); p.wait(timeout=5); log.close()
run(f"from api.supervisor_completion import complete; complete({receipt!r}, 'Isolated supervisor verified receipt canary; no command replay.')")
p,log = start('after-restart')
try:
    marker = state/'worker-admitted.json'
    for _ in range(100):
        if marker.exists(): break
        time.sleep(.1)
    assert marker.exists(), 'no real server-side worker admission after restart'
    data = json.loads(marker.read_text())
    assert data['session_id'] == 'receipt-owner' and receipt in data['pending'], data
    receipts = json.loads(run("from api.supervisor_completion import main; import sys; sys.argv=['probe','status']; main()"))
    assert receipts[0]['state']=='admitted', receipts
    evidence = {'old_pid':old_pid,'new_pid':p.pid,'receipt':receipt,'worker':data,'receipts':receipts,
                'proof':'real server restart/drain/start_session_turn/admission; recording worker, not model API'}
    (root/'completion-193-restart-evidence.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps(evidence,indent=2))
finally:
    p.kill();p.wait(timeout=5);log.close()
