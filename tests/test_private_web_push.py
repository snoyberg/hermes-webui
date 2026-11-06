"""Private push policy/store tests; transport is synthetic, never a user push."""
import importlib
import json
import pytest


@pytest.fixture(scope='session', autouse=True)
def test_server():
    """These unit/transport tests deliberately do not start the Agent app."""
    yield


@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    yield


@pytest.fixture(autouse=True)
def _reset_push_owner_registry(monkeypatch):
    push = module()
    monkeypatch.setattr(push, '_owners', {})


def module():
    try:
        return importlib.import_module('api.web_push')
    except ModuleNotFoundError as exc:
        if exc.name == 'api.web_push':
            pytest.fail('private direct Web Push is not implemented')
        raise


def test_enrolled_profile_receives_unopened_desktop_session_once(tmp_path):
    push = module()
    store = push.PushStore(tmp_path / 'push')
    subscription = {'endpoint': 'https://web.push.apple.com/Qsynthetic',
                    'keys': {'p256dh': 'synthetic', 'auth': 'synthetic'}}
    store.enroll('phone', 'work', subscription)
    store.enroll('other-phone', 'personal', subscription | {'endpoint': subscription['endpoint']+'2'})
    assert store.enqueue('work', 'desktop-session', 'run-1', 'complete') == 1
    assert store.enqueue('work', 'desktop-session', 'run-1', 'complete') == 0
    rows = store.pending(now=100)
    assert len(rows) == 1
    assert rows[0]['profile'] == 'work'
    assert rows[0]['session'] == 'desktop-session'
    assert rows[0]['kind'] == 'complete'
    assert set(json.loads(rows[0]['payload'])) == {'v', 'kind', 'profile', 'session', 'event'}


def test_attention_resolution_disable_expiry_retry_and_invalid_endpoint(tmp_path):
    push = module()
    store = push.PushStore(tmp_path / 'push')
    store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    store.enqueue('work', 'session', 'prompt1', 'approval', now=100)
    store.cancel_attention('work', 'session', 'approval')
    calls = []
    store.deliver(lambda *args: calls.append(args) or 201, now=101)
    assert calls == []
    store.enqueue('work', 'session', 'run', 'complete', now=100)
    store.deliver(lambda *args: calls.append(args) or 503, now=101)
    store.deliver(lambda *args: calls.append(args) or 201, now=102)
    assert len(calls) == 1
    store.deliver(lambda *args: calls.append(args) or 201, now=110)
    store.deliver(lambda *args: calls.append(args) or 201, now=120)
    assert len(calls) == 2
    store.enqueue('work', 'session', 'run2', 'complete', now=100)
    store.deliver(lambda *args: calls.append(args) or 410, now=120)
    assert not store.enabled('phone', 'work')
    store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    store.enqueue('work', 'session', 'run3', 'complete', now=100)
    store.deliver(lambda *args: pytest.fail('expired push sent'), now=401)
    store.disable('phone', 'work')
    assert not store.enabled('phone', 'work')


@pytest.mark.parametrize('endpoint', [
    'http://web.push.apple.com/Qx', 'https://web.push.apple.com.evil/Qx',
    'https://web.push.apple.com@127.0.0.1/Qx', 'https://web.push.apple.com:444/Qx',
    'https://127.0.0.1/Qx', 'https://evil.test/Qx', 'https://web.push.apple.com/Qx?q=1',
])
def test_transport_refuses_non_browser_provider_endpoint(endpoint):
    with pytest.raises(ValueError):
        module().validate_endpoint(endpoint)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '224.0.0.1', '::1', 'fec0::1'])
def test_send_time_dns_rebinding_is_refused(address):
    with pytest.raises(ValueError):
        module().resolve_endpoint('https://web.push.apple.com/Qx', resolver=lambda *a, **k: [
            (2, 1, 6, '', (address, 443))])


def test_real_pywebpush_encrypts_generic_payload_and_uses_pinned_transport(monkeypatch):
    push = module()
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    import base64
    from py_vapid import Vapid
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=').decode()
    vapid = Vapid()
    vapid.generate_keys()
    sent = []
    class Response:
        status_code = 201
        text = ''
        headers = {}
    def post(self, endpoint, data, headers, timeout):
        sent.append((endpoint, data, headers, timeout))
        return Response()
    monkeypatch.setattr(push.PinnedSession, 'post', post)
    status = push.send_push({'endpoint': 'https://web.push.apple.com/Qx',
                            'keys': {'p256dh': enc(key), 'auth': enc(b'x'*16)}},
                           '{"v":1,"kind":"test"}', 30, vapid, 'mailto:admin@example.com')
    assert status == 201
    assert len(sent) == 1
    assert b'"kind"' not in sent[0][1]
    assert sent[0][2]['content-encoding'] == 'aes128gcm'
    assert 'vapid' in sent[0][2]['Authorization']


def test_canonical_run_attention_and_parent_policy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    session = SimpleNamespace(session_id='desktop', profile='work', raw_source='webui',
                              source_tag='webui', read_only=False)
    push.bind_run(session, 'run1')
    push.attention('desktop', 'approval', {'approval_id': 'a1', 'command': 'TOP SECRET'})
    assert len(service.store.pending()) == 1
    push.attention('desktop', 'approval', None)
    assert not service.store.pending()
    push.finish_run('desktop', 'run1', completed=True)
    assert len(service.store.pending()) == 1
    assert 'TOP SECRET' not in service.store.path.read_bytes().decode(errors='ignore')
    child = SimpleNamespace(session_id='child', profile='work', raw_source='subagent', read_only=False)
    push.bind_run(child, 'run2')
    push.finish_run('child', 'run2', completed=True)
    assert len(service.store.pending()) == 1
    # A late terminal from an old worker may not retire a newer owner.
    push.bind_run(session, 'run3')
    push.finish_run('desktop', 'run1', completed=False)
    push.attention('desktop', 'clarify', {'clarify_id': 'c1', 'question': 'PRIVATE QUESTION'})
    assert len(service.store.pending()) == 2
    push.finish_run('desktop', 'run3', completed=False)
    assert len(service.store.pending()) == 1


def test_profile_bound_device_api_rejects_foreign_enrollment(tmp_path, monkeypatch):
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    with pytest.raises(ValueError):
        push.device_api('enable', {'device': 'a'*64, 'profile': 'foreign'}, 'work')
    assert not service.store.enabled('a'*64, 'foreign')
    service.store.enroll('a'*64, 'work', {'endpoint': 'synthetic'})
    assert push.device_api('status', {'device': 'a'*64, 'profile': 'work'}, 'work')['enabled']
    push.device_api('disable', {'device': 'a'*64, 'profile': 'work'}, 'work')
    assert not service.store.enabled('a'*64, 'work')


def test_attention_expires_at_canonical_prompt_deadline(tmp_path, monkeypatch):
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    monkeypatch.setattr(push.time, 'time', lambda: 100)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    push.bind_run(SimpleNamespace(session_id='desktop', profile='work', read_only=False), 'run1')
    push.attention('desktop', 'clarify', {'clarify_id': 'short', 'expires_at': 101})
    service.store.deliver(lambda *a: pytest.fail('resolved/expired prompt sent'), now=102)


@pytest.mark.parametrize('filename,function_name', [('streaming.py', 'put'), ('gateway_chat.py', 'put_gateway_event')])
def test_real_terminal_emitter_queues_completion_without_page(tmp_path, monkeypatch, filename, function_name):
    import ast
    import queue
    import threading
    from pathlib import Path
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    push.bind_run(SimpleNamespace(session_id='desktop', profile='work', read_only=False), 'run1')
    tree = ast.parse((Path(__file__).parents[1] / 'api' / filename).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function_name)
    namespace = dict(cancel_event=threading.Event(), _success_writeback_committed=True,
                     success_writeback_committed=True, run_journal=None, q=queue.Queue(),
                     session_id='desktop', stream_id='run1', _settle_pending_steer=lambda: None,
                     s=SimpleNamespace(messages=[]), _active_turn_identity={}, active_turn_identity={})
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    namespace[function_name]('done', {'session': {'messages': ['PRIVATE OUTPUT']}})
    assert len(service.store.pending()) == 1
    assert service.store.pending()[0]['kind'] == 'complete'
    assert 'PRIVATE OUTPUT' not in service.store.path.read_bytes().decode(errors='ignore')


@pytest.mark.parametrize('filename,function_name', [('streaming.py', 'put'), ('gateway_chat.py', 'put_gateway_event')])
def test_terminal_emitter_previews_only_current_settled_final(tmp_path, monkeypatch, filename, function_name):
    import ast
    import queue
    import threading
    from pathlib import Path
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com',
                               completion_preview=True)
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    session = SimpleNamespace(session_id='desktop', profile='work', read_only=False, messages=[
        {'role': 'user', 'content': 'OLD PROMPT', '_active_turn_token': 'old'},
        {'role': 'assistant', 'content': 'OLD ANSWER'},
        {'role': 'user', 'content': 'PRIVATE PROMPT', '_active_turn_token': 'run1:100'},
        {'role': 'assistant', 'content': 'INTERMEDIATE', 'tool_calls': [{'name': 'shell'}]},
        {'role': 'tool', 'content': 'PRIVATE TOOL RESULT'},
        {'role': 'assistant', 'content': 'Final answer 🐕'},
    ])
    push.bind_run(session, 'run1')
    tree = ast.parse((Path(__file__).parents[1] / 'api' / filename).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function_name)
    namespace = dict(cancel_event=threading.Event(), _success_writeback_committed=True,
                     success_writeback_committed=True, run_journal=None, q=queue.Queue(), s=session,
                     session_id='desktop', stream_id='run1', _settle_pending_steer=lambda: None,
                     _active_turn_identity={'token': 'run1:100'}, active_turn_identity={'token': 'run1:100'})
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    namespace[function_name]('token', {'text': 'MIDSTREAM'})
    assert not service.store.pending()
    namespace[function_name]('done', {'session': {'messages': session.messages}})
    row, = service.store.pending()
    assert json.loads(row['payload'])['preview'] == 'Final answer 🐕'
    assert all(text not in row['payload'] for text in ('PRIVATE', 'OLD', 'INTERMEDIATE', 'MIDSTREAM'))
    sent = []
    service.store.deliver(lambda sub, payload, ttl: sent.append(json.loads(payload)) or 201)
    assert sent[0]['preview'] == 'Final answer 🐕'


@pytest.mark.parametrize('text', ['😀' * 10000, '\"\\\n\x00' * 10000, 'שלום é' * 10000, '\ud800' * 5000])
def test_excerpt_json_utf8_and_encrypted_payload_ceiling(tmp_path, monkeypatch, text):
    push = module()
    store = push.PushStore(tmp_path / 'push')
    store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    store.enqueue('work', 'session', 'run', 'complete', preview=text)
    row, = store.pending()
    assert len(row['payload'].encode('utf-8')) <= push.MAX_PAYLOAD_BYTES
    assert json.loads(row['payload'])['preview'].endswith('…')
    # Exercise actual aes128gcm framing, not only a plaintext size estimate.
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid
    import base64
    key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    enc = lambda b: base64.urlsafe_b64encode(b).rstrip(b'=').decode()
    vapid = Vapid()
    vapid.generate_keys()
    from types import SimpleNamespace
    def post(self, endpoint, data, headers, timeout):
        assert len(data) <= 4096
        assert headers['content-encoding'] == 'aes128gcm'
        return SimpleNamespace(status_code=201, text='', headers={})
    monkeypatch.setattr(push.PinnedSession, 'post', post)
    assert push.send_push({'endpoint': 'https://web.push.apple.com/Qsynthetic',
                           'keys': {'p256dh': enc(key), 'auth': enc(b'x'*16)}},
                          row['payload'], 30, vapid, 'mailto:a@example.com') == 201


@pytest.mark.parametrize('final', [
    {'role': 'tool', 'content': 'TOOL'}, {'role': 'user', 'content': 'PROMPT'},
    {'role': 'assistant', 'content': 'CALL', 'tool_calls': [{}]},
    {'role': 'assistant', 'content': 'ERROR', '_error': True},
    {'role': 'assistant', 'content': ''},
])
def test_no_preview_of_nonfinal_or_previous_turn(final):
    push = module()
    messages = [{'role': 'assistant', 'content': 'PREVIOUS ANSWER'},
                {'role': 'user', '_active_turn_token': 'current', 'content': 'PROMPT'}, final]
    assert push.completion_text(messages, 'current') is None
    assert push.completion_text(messages, 'old') is None


def test_preview_consent_stale_owner_and_attention_fences(tmp_path, monkeypatch):
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    messages = [{'role': 'user', '_active_turn_token': 'current', 'content': 'PROMPT'},
                {'role': 'assistant', 'content': 'Final'}]
    session = SimpleNamespace(session_id='desktop', profile='work', read_only=False)
    push.bind_run(session, 'run1')
    push.finish_run('desktop', 'run1', completed=True, messages=messages, turn_token='current')
    assert 'preview' not in json.loads(service.store.pending()[0]['payload'])  # default opt-out
    service.completion_preview = True
    push.bind_run(session, 'run2')
    push.finish_run('desktop', 'run1', completed=True, messages=messages, turn_token='current')
    assert len(service.store.pending()) == 1
    service.store.disable('phone', 'work')
    push.finish_run('desktop', 'run2', completed=True, messages=messages, turn_token='current')
    assert not service.store.pending()
    for kind in ('approval', 'clarify', 'test'):
        with pytest.raises(ValueError):
            service.store.enqueue('work', 'desktop', 'run', kind, preview='PRIVATE')


def test_completion_redacts_credentials_and_ignores_reasoning_blocks():
    push = module()
    key = 'sk-' + 'a' * 48
    messages = [{'role': 'user', '_active_turn_token': 'current', 'content': 'PROMPT'},
                {'role': 'assistant', 'content': [
                    {'type': 'thinking', 'thinking': 'PRIVATE REASONING'},
                    {'type': 'text', 'text': 'Answer ' + key}]}]
    excerpt = push.completion_text(messages, 'current')
    assert excerpt.startswith('Answer ')
    assert key not in excerpt
    assert 'PRIVATE REASONING' not in excerpt


def test_real_attention_owner_projection_hooks(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import clarify, route_approvals
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    push.bind_run(SimpleNamespace(session_id='desktop', profile='work', read_only=False), 'run1')
    route_approvals._approval_sse_notify('desktop', {'approval_id': 'a1', 'command': 'PRIVATE'}, 1)
    assert len(service.store.pending()) == 1
    route_approvals._approval_sse_notify('desktop', None, 0)
    assert not service.store.pending()
    entry = clarify.submit_pending('desktop', {'question': 'PRIVATE'})
    assert len(service.store.pending()) == 1
    clarify.resolve_clarify_by_id('desktop', entry.clarify_id, 'PRIVATE ANSWER')
    assert not service.store.pending()


def test_push_http_uses_existing_auth_csrf_and_profile_dispatch(tmp_path, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import http.client
    import threading
    from urllib.parse import urlsplit
    from api import auth, routes, profiles
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    monkeypatch.setattr(auth, 'is_auth_enabled', lambda: True)
    cookie = auth.create_session()
    csrf = auth.csrf_token_for_session(cookie)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            if not auth.check_auth_or_close(self, urlsplit(self.path)):
                return
            profiles.set_request_profile('work')
            try:
                if not routes.handle_post(self, urlsplit(self.path)):
                    self.send_error(404)
            finally:
                profiles.clear_request_profile()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def request(headers, profile='work', *, action='status', extra=None):
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        body = {'profile': profile, 'device': 'a'*64, **(extra or {})}
        connection.request('POST', '/api/push/'+action, json.dumps(body), headers)
        response = connection.getresponse()
        status, data = response.status, response.read()
        connection.close()
        return status, data
    try:
        assert request({})[0] == 401
        assert request({'Cookie': 'hermes_session='+cookie,
                        'Origin': 'http://127.0.0.1:'+str(server.server_port)})[0] == 403
        headers = {'Cookie': 'hermes_session='+cookie, 'X-Hermes-CSRF-Token': csrf,
                   'Origin': 'http://127.0.0.1:'+str(server.server_port),
                   'Content-Type': 'application/json'}
        assert request(headers)[0] == 200
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        import base64
        key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        enc = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
        subscription = {'endpoint': 'https://web.push.apple.com/Qsynthetic',
                        'keys': {'p256dh': enc(key), 'auth': enc(b'x'*16)}, 'untrusted_title': 'PRIVATE'}
        assert request(headers, action='enable', extra={'subscription': subscription})[0] == 200
        assert service.store.enabled('a'*64, 'work')
        assert request(headers, action='test')[0] == 200
        assert len(service.store.pending()) == 1
        assert request(headers, action='disable')[0] == 200
        assert not service.store.enabled('a'*64, 'work')
        assert 'PRIVATE' not in service.store.path.read_bytes().decode(errors='ignore')
        assert request(headers, 'foreign')[0] == 400
        assert request(headers | {'Origin': 'https://evil.invalid'})[0] == 403
        assert request({'Cookie': 'hermes_session='+cookie})[0] == 403
        monkeypatch.setattr(auth, 'is_auth_enabled', lambda: False)
        # Boot probes availability even on deliberately unauthenticated servers.
        # Report unavailable without a console-producing HTTP error or disclosure.
        status, payload = request(headers)
        assert status == 200
        assert json.loads(payload) == {'available': False, 'enabled': False}
        for action in ('enable', 'disable', 'test'):
            assert request(headers, action=action)[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize('fixture', ['worker', 'worker_failures', 'page', 'races'])
def test_service_worker_actual_javascript(fixture):
    import os
    import subprocess
    from pathlib import Path
    env = os.environ.copy()
    import shutil
    if shutil.which('node') is None:
        pytest.skip('Node is required for executable push JavaScript fixtures')
    if fixture != 'page' and not env.get('PUSH_FAKE_INDEXEDDB'):
        found = subprocess.run(['node', '-e', "require.resolve('fake-indexeddb')"],
                               cwd=Path(__file__).parents[1], capture_output=True)
        if found.returncode:
            pytest.skip('npm ci provides fake-indexeddb for the executable worker fixture')
    result = subprocess.run(['node', f'tests/private_push_{fixture}.cjs'], cwd=Path(__file__).parents[1],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('completion_preview', [False, True])
def test_configuration_is_opt_in_and_private_and_lifecycle_stops(tmp_path, monkeypatch, completion_preview):
    push = module()
    monkeypatch.setattr(push, '_service', None)
    monkeypatch.delenv('HERMES_WEBUI_PUSH_CONFIG', raising=False)
    push.start(tmp_path / 'webui')
    assert not (tmp_path / 'webui').exists()
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    key = ec.generate_private_key(ec.SECP256R1())
    private = tmp_path / 'vapid.pem'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private.chmod(0o600)
    config = tmp_path / 'push.json'
    config.write_text(json.dumps({'private_key_file': str(private), 'subject': 'mailto:admin@example.com',
                                 'completion_preview': completion_preview}))
    config.chmod(0o600)
    monkeypatch.setenv('HERMES_WEBUI_PUSH_CONFIG', str(config))
    push.start(tmp_path / 'webui')
    try:
        assert push._service is not None
        assert push._service.completion_preview is completion_preview
        assert push._service.thread.is_alive()
        assert push._service.store.path.stat().st_mode & 0o777 == 0o600
    finally:
        push.stop()
    assert push._service is None
    config.chmod(0o644)
    push.start(tmp_path / 'webui')
    assert push._service is None


def test_store_admission_is_bounded(tmp_path):
    push = module()
    store = push.PushStore(tmp_path / 'push')
    for n in range(push.MAX_RECIPIENTS):
        store.enroll(str(n), 'work', {'endpoint': 'synthetic'})
    with pytest.raises(ValueError):
        store.enroll('overflow', 'work', {'endpoint': 'synthetic'})
    # Existing enrollment remains editable at capacity.
    store.enroll('0', 'work', {'endpoint': 'updated'})


def test_slow_batch_rechecks_deadline_before_each_send(tmp_path, monkeypatch):
    push = module()
    store = push.PushStore(tmp_path / 'push')
    store.enroll('one', 'work', {'endpoint': 'synthetic-one'})
    store.enroll('two', 'work', {'endpoint': 'synthetic-two'})
    clock = [100]
    monkeypatch.setattr(push.time, 'time', lambda: clock[0])
    store.enqueue('work', 'session', 'run', 'complete', expires_at=101)
    calls = []
    def send(*args):
        calls.append(args)
        clock[0] = 102
        return 201
    store.deliver(send)
    assert len(calls) == 1


def test_pinned_transport_keeps_original_tls_hostname_and_disables_redirects(monkeypatch):
    import socket
    import ssl
    from types import SimpleNamespace
    import urllib3
    push = module()
    addresses = []
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('17.57.144.10', 443))])
    monkeypatch.setattr(socket, 'create_connection', lambda address, timeout: addresses.append(address))
    class Pool:
        def __init__(self, host, **kwargs):
            assert host == 'web.push.apple.com'
            assert kwargs['cert_reqs'] == ssl.CERT_REQUIRED
            assert kwargs['ca_certs']
        def urlopen(self, method, path, **kwargs):
            assert kwargs['redirect'] is False and kwargs['retries'] is False
            assert path == '/Qsynthetic'
            connection = self.ConnectionCls('web.push.apple.com', timeout=1)
            assert connection.host == 'web.push.apple.com'
            connection._new_conn()
            return SimpleNamespace(status=201)
        def close(self):
            pass
    monkeypatch.setattr(urllib3, 'HTTPSConnectionPool', Pool)
    response = push.PinnedSession().post('https://web.push.apple.com/Qsynthetic', b'ciphertext', {}, 1)
    assert response.status_code == 201
    assert addresses == [('17.57.144.10', 443)]


def test_native_delegate_marker_suppresses_child_even_with_webui_source(tmp_path, monkeypatch):
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    child = SimpleNamespace(session_id='child', profile='work', raw_source='webui',
                            model_config={'_delegate_from': 'parent'}, read_only=False)
    push.bind_run(child, 'child-run')
    push.finish_run('child', 'child-run', completed=True)
    assert not service.store.pending()


def test_transport_revalidates_canonical_attention_without_browser_poll(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import clarify
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    push.bind_run(SimpleNamespace(session_id='desktop', profile='work', read_only=False), 'run1')
    clarify.submit_pending('desktop', {'question': 'private'})
    assert service.store.pending()
    # Simulate a native owner retiring without another page/mirror projection.
    with clarify._lock:
        clarify._gateway_queues.pop('desktop', None)
        clarify._pending.pop('desktop', None)
    calls = []
    monkeypatch.setattr(push, 'send_push', lambda *a: calls.append(a) or 201)
    service.tick()
    assert not calls
    assert not service.store.pending()


def test_concurrent_profile_runs_keep_explicit_recipient_ownership(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    barrier = threading.Barrier(2)
    for profile in ('work', 'personal'):
        service.store.enroll(profile+'-phone', profile, {'endpoint': 'synthetic-'+profile})
    def run(profile):
        sid = profile+'-desktop-session'
        push.bind_run(SimpleNamespace(session_id=sid, profile=profile, read_only=False), profile+'-run')
        barrier.wait(timeout=3)
        push.finish_run(sid, profile+'-run', completed=True)
    threads = [threading.Thread(target=run, args=(profile,)) for profile in ('work', 'personal')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    rows = service.store.pending()
    assert {(row['profile'], row['device'], row['session']) for row in rows} == {
        (p, p+'-phone', p+'-desktop-session') for p in ('work', 'personal')}


def test_durable_completion_reopens_and_retries_are_bounded(tmp_path):
    push = module()
    store = push.PushStore(tmp_path / 'push')
    store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    store.enqueue('work', 'desktop', 'run', 'complete', now=100)
    store = push.PushStore(tmp_path / 'push')
    calls = []
    for attempt in range(push.MAX_ATTEMPTS + 2):
        store.deliver(lambda *args: calls.append(args) or 503, now=100 + 30*attempt)
    assert len(calls) == push.MAX_ATTEMPTS
    assert not store.pending(now=280)


def test_send_test_targets_only_explicit_device(tmp_path, monkeypatch):
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    for token in ('a'*64, 'b'*64):
        service.store.enroll(token, 'work', {'endpoint': 'synthetic'})
    limit = push.MAX_DELIVERIES
    monkeypatch.setattr(push, 'MAX_DELIVERIES', 0)
    with pytest.raises(ValueError):
        push.device_api('test', {'device': 'a'*64, 'profile': 'work'}, 'work')
    monkeypatch.setattr(push, 'MAX_DELIVERIES', limit)
    result = push.device_api('test', {'device': 'a'*64, 'profile': 'work'}, 'work')
    assert result['queued'] is True
    assert [row['device'] for row in service.store.pending()] == ['a'*64]


def test_continued_cli_conversation_is_not_mistaken_for_internal_worker(tmp_path, monkeypatch):
    from types import SimpleNamespace
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    session = SimpleNamespace(session_id='continued-cli', profile='work', raw_source='cli',
                              source_tag='cli', read_only=False, is_cli_session=True)
    push.bind_run(session, 'continued-run')
    push.finish_run('continued-cli', 'continued-run', completed=True)
    assert len(service.store.pending()) == 1


def test_failed_attention_admission_retries_from_canonical_owner_without_page(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api import clarify
    push = module()
    service = push.PushService(push.PushStore(tmp_path / 'push'), None, 'public', 'mailto:a@example.com')
    monkeypatch.setattr(push, '_service', service)
    service.store.enroll('phone', 'work', {'endpoint': 'synthetic'})
    push.bind_run(SimpleNamespace(session_id='admission', profile='work', read_only=False), 'run1')
    enqueue = service.store.enqueue
    monkeypatch.setattr(service.store, 'enqueue', lambda *a, **k: (_ for _ in ()).throw(OSError('synthetic store unavailable')))
    entry = clarify.submit_pending('admission', {'question': 'private'})
    assert not service.store.pending()
    monkeypatch.setattr(service.store, 'enqueue', enqueue)
    calls = []
    monkeypatch.setattr(push, 'send_push', lambda *args: calls.append(args) or 201)
    try:
        service.tick()
        assert len(calls) == 1
        service.tick()
        assert len(calls) == 1
    finally:
        clarify.resolve_clarify_by_id('admission', entry.clarify_id, 'synthetic')
