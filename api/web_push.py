"""Opt-in, content-free Web Push delivery. No Agent/config imports at module load."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from contextlib import contextmanager

KINDS = frozenset(('complete', 'approval', 'clarify', 'test'))
TTL = 300
MAX_ATTEMPTS = 4
MAX_RECIPIENTS = 128
MAX_DELIVERIES = 10000


def validate_endpoint(endpoint):
    """Only documented browser providers, not arbitrary HTTPS webhook URLs."""
    from urllib.parse import urlsplit
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or any(ord(c) < 33 for c in endpoint):
        raise ValueError('Invalid browser push endpoint')
    url = urlsplit(endpoint)
    providers = {'web.push.apple.com': '/Q', 'fcm.googleapis.com': '/fcm/send/',
                 'updates.push.services.mozilla.com': '/wpush/v2/'}
    prefix = providers.get(url.hostname)
    if (url.scheme != 'https' or url.netloc != url.hostname or not prefix
            or not url.path.startswith(prefix) or url.query or url.fragment):
        raise ValueError('Unsupported browser push provider')
    return url


def resolve_endpoint(endpoint, *, resolver=None):
    import ipaddress
    import socket
    url = validate_endpoint(endpoint)
    addresses = (resolver or socket.getaddrinfo)(url.hostname, 443, type=socket.SOCK_STREAM)
    ips = [ipaddress.ip_address(a[4][0]) for a in addresses]
    if not ips or any(not ip.is_global or ip.is_multicast or ip.is_reserved
                      or getattr(ip, 'is_site_local', False) for ip in ips):
        raise ValueError('Push provider must resolve only to public addresses')
    return url, addresses[0][4][0]


class PinnedSession:
    """Pinned public IP, verified TLS/SNI, no redirects or environment proxies.

    This is pywebpush's requests boundary. Resolve again for every send.
    """
    def post(self, endpoint, data, headers, timeout):
        import socket
        import ssl
        import requests
        import urllib3
        from urllib3.connection import HTTPSConnection
        url, address = resolve_endpoint(endpoint)

        class Connection(HTTPSConnection):
            def _new_conn(self):
                return socket.create_connection((address, 443), self.timeout)

        pool = urllib3.HTTPSConnectionPool(url.hostname, port=443, cert_reqs=ssl.CERT_REQUIRED,
                                          ca_certs=requests.certs.where(), maxsize=1)
        pool.ConnectionCls = Connection
        try:
            result = pool.urlopen('POST', url.path, body=data, headers=headers,
                                  timeout=timeout, redirect=False, retries=False)
            response = requests.Response()
            response.status_code = result.status
            response._content = b''
            response.reason = 'Push transport'
            return response
        finally:
            pool.close()


def send_push(subscription, payload, ttl, private_key, subject):
    from pywebpush import webpush, WebPushException
    validate_endpoint(subscription['endpoint'])
    try:
        return webpush(subscription_info=subscription, data=payload,
                       vapid_private_key=private_key, vapid_claims={'sub': subject},
                       ttl=min(TTL, ttl), timeout=10, requests_session=PinnedSession(),
                       content_encoding='aes128gcm', verbose=False).status_code
    except WebPushException as exc:
        return exc.response.status_code if exc.response is not None else 0


def event_key(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


class PushStore:
    """One private SQLite owner; all payloads are constructed from typed routing IDs."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.directory.stat().st_mode & 0o077:
            raise ValueError('Push directory must be private')
        self.path = self.directory / 'delivery.sqlite3'
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise ValueError('Push database must be private')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS recipients (
                    device TEXT, profile TEXT, subscription TEXT NOT NULL,
                    PRIMARY KEY(device, profile));
                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY, device TEXT, profile TEXT, session TEXT,
                    event TEXT, kind TEXT, payload TEXT, state TEXT,
                    attempts INTEGER, due REAL, expires REAL);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enroll(self, device, profile, subscription):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            exists = db.execute('SELECT 1 FROM recipients WHERE device=? AND profile=?', (device, profile)).fetchone()
            if not exists and db.execute('SELECT COUNT(*) FROM recipients').fetchone()[0] >= MAX_RECIPIENTS:
                raise ValueError('Push device limit reached; disable unused devices first')
            db.execute('INSERT OR REPLACE INTO recipients VALUES (?,?,?)',
                       (device, profile, json.dumps(subscription)))

    def enabled(self, device, profile):
        with self.connect() as db:
            return db.execute('SELECT 1 FROM recipients WHERE device=? AND profile=?',
                              (device, profile)).fetchone() is not None

    def disable(self, device, profile):
        with self.connect() as db:
            db.execute('DELETE FROM recipients WHERE device=? AND profile=?', (device, profile))
            db.execute('DELETE FROM deliveries WHERE device=? AND profile=?', (device, profile))

    def enqueue(self, profile, session, event, kind, *, device=None, now=None, expires_at=None):
        if kind not in KINDS:
            raise ValueError('Unknown push kind')
        now = time.time() if now is None else now
        expires = min(now + TTL, expires_at) if expires_at else now + TTL
        if expires <= now:
            return 0
        # No free-text title/body/snippet/error can enter the persistence API.
        payload = json.dumps(dict(v=1, kind=kind, profile=profile, session=session,
                                  event=event_key(profile, session, event, kind)), separators=(',', ':'))
        count = 0
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM deliveries WHERE expires<?', (now - 86400,))
            for row in db.execute('SELECT device FROM recipients WHERE profile=?', (profile,)).fetchall():
                if device is not None and row['device'] != device:
                    continue
                if db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] >= MAX_DELIVERIES:
                    break
                count += db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                    (event_key(row['device'], profile, session, event, kind),
                                     row['device'], profile, session, event_key(event), kind,
                                     payload, 'pending', 0, 0, expires)).rowcount
        return count

    def pending(self, *, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM deliveries WHERE state='pending' AND due<=? AND expires>? ORDER BY due LIMIT 32",
                (now, now))]

    def cancel_attention(self, profile, session, kind, *, keep=None):
        with self.connect() as db:
            db.execute("UPDATE deliveries SET state='resolved' WHERE profile=? AND session=? AND kind=? "
                       "AND state='pending' AND event!=?", (profile, session, kind, event_key(keep)))

    def prune(self, now):
        with self.connect() as db:
            db.execute('DELETE FROM deliveries WHERE expires<?', (now - 86400,))
            db.execute("UPDATE deliveries SET state='expired' WHERE expires<=? AND state='pending'", (now,))

    def deliver(self, sender, *, now=None, still_current=None):
        clock = time.time if now is None else lambda: now
        started = clock()
        self.prune(started)
        for row in self.pending(now=started):
            current = clock()
            if row['expires'] <= current:
                continue
            # Claim is durable before I/O. A restart retries after the lease, bounded
            # by TTL/attempts. Holding a transaction through network I/O would block
            # resolution and enrollment. Device display is intentionally not exactly-once.
            with self.connect() as db:
                claimed = db.execute("UPDATE deliveries SET due=?, attempts=attempts+1 WHERE id=? "
                                     "AND state='pending' AND due<=? AND attempts<?",
                                     (current + 30, row['id'], current, MAX_ATTEMPTS)).rowcount
                recipient = db.execute('SELECT subscription FROM recipients WHERE device=? AND profile=?',
                                       (row['device'], row['profile'])).fetchone()
            if not claimed or recipient is None:
                continue
            # Recheck resolution/disable immediately before transport. A resolution
            # racing an already in-flight request cannot retract a provider acceptance.
            with self.connect() as db:
                live = db.execute("SELECT 1 FROM deliveries WHERE id=? AND state='pending'", (row['id'],)).fetchone()
            if not live:
                continue
            if still_current is not None and not still_current(row):
                with self.connect() as db:
                    db.execute("UPDATE deliveries SET state='resolved' WHERE id=? AND state='pending'", (row['id'],))
                continue
            try:
                status = sender(json.loads(recipient['subscription']), row['payload'], max(1, int(row['expires'] - current)))
            except Exception:
                status = 0  # Never log subscription, provider response or exception text.
            with self.connect() as db:
                if status in (404, 410):
                    db.execute('DELETE FROM recipients WHERE device=? AND profile=?', (row['device'], row['profile']))
                    db.execute("UPDATE deliveries SET state='invalid' WHERE device=? AND profile=? AND state='pending'",
                               (row['device'], row['profile']))
                else:
                    state = ('accepted' if 200 <= status < 300 else
                             'failed' if row['attempts'] + 1 >= MAX_ATTEMPTS or (400 <= status < 500 and status != 429)
                             else 'pending')
                    db.execute("UPDATE deliveries SET state=?, due=? WHERE id=? AND state='pending'",
                               (state, current + min(120, 5 * 2 ** row['attempts']), row['id']))


_service = None
_owners = {}
_owner_lock = threading.RLock()


class PushService:
    def __init__(self, store, private_key, public_key, subject):
        self.store = store
        self.private_key = private_key
        self.public_key = public_key
        self.subject = subject
        self.stopped = threading.Event()
        self.thread = None

    def tick(self):
        # Retry notification admission from the existing pending owners. This
        # does not depend on a phone tab, SSE subscriber or browser poll.
        with _owner_lock:
            sessions = list(_owners)
        for sid in sessions:
            for kind in ('approval', 'clarify'):
                try:
                    attention(sid, kind, current_attention_head(sid, kind))
                except Exception:
                    pass
        self.store.deliver(lambda subscription, payload, ttl: 0 if self.stopped.is_set() else send_push(
            subscription, payload, ttl, self.private_key, self.subject), still_current=attention_is_current)

    def loop(self):
        while not self.stopped.wait(2):
            try:
                self.tick()
            except Exception:
                pass  # A store/transport failure must not affect the chat server.


def canonical_profile(profile):
    import re
    profile = profile or 'default'
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', profile):
        raise ValueError('Invalid profile')
    from api.profiles import _is_root_profile
    return 'default' if _is_root_profile(profile) else profile


def bind_run(session, run_id):
    """Bind at the existing run owner, not at browser session subscription."""
    if _service is None or session is None:
        return
    try:
        sources = [getattr(session, name, None) for name in ('raw_source', 'source_tag', 'session_source')]
        from api.agent_sessions import _branch_markers
        marker_state, markers = _branch_markers({'model_config': getattr(session, 'model_config', None)})
        # Only runs in a user-facing WebUI conversation. Never process/delegate
        # children; their meaningful final parent wakeup uses this same run owner.
        if (getattr(session, 'read_only', False) or marker_state == 'unknown' or '_delegate_from' in markers
                or getattr(session, 'relationship_type', None) == 'child_session'
                or any(s and s not in ('webui', 'fork', 'cli') for s in sources)):
            return
        sid = session.session_id
        profile = canonical_profile(session.profile)
        with _owner_lock:
            _owners[sid] = (profile, run_id)
    except Exception:
        pass


def current_attention_head(sid, kind):
    if kind == 'clarify':
        from api.clarify import get_pending
        return get_pending(sid)
    from api.route_approvals import _lock, reconcile_gateway_pending_mirror_locked
    with _lock:
        head, _, _ = reconcile_gateway_pending_mirror_locked(sid)
        return dict(head) if head else None


def attention_is_current(row):
    """Revalidate native attention at send time, independent of page polling.

    Never hold _owner_lock while acquiring an approval/clarify owner lock.
    """
    kind = row['kind']
    if kind not in ('approval', 'clarify'):
        return True
    try:
        sid = row['session']
        head = current_attention_head(sid, kind)
        with _owner_lock:
            owner = _owners.get(sid)
            if not owner or owner[0] != row['profile'] or not head:
                return False
            prompt = head.get('approval_id' if kind == 'approval' else 'clarify_id')
            event = event_key(owner[1], kind, prompt, head.get('run_id'), head.get('_gateway_mirror_token'))
            return bool(prompt) and row['event'] == event_key(event)
    except Exception:
        return False


def attention(session_id, kind, head):
    """Called under the canonical attention lock; copy routing identities only."""
    if _service is None or kind not in ('approval', 'clarify'):
        return
    try:
        with _owner_lock:
            owner = _owners.get(session_id)
            if owner is None:
                return
            profile, run_id = owner
            prompt = (head or {}).get('approval_id' if kind == 'approval' else 'clarify_id')
            event = event_key(run_id, kind, prompt, (head or {}).get('run_id'),
                              (head or {}).get('_gateway_mirror_token')) if prompt else None
            _service.store.cancel_attention(profile, session_id, kind, keep=event)
            if event:
                _service.store.enqueue(profile, session_id, event, kind, expires_at=head.get('expires_at'))
    except Exception:
        pass  # Do not mark a failed enqueue seen; the owner can retry its projection.


def finish_run(session_id, run_id, *, completed=False):
    if _service is None:
        return
    try:
        with _owner_lock:
            owner = _owners.get(session_id)
            if owner is None or owner[1] != run_id:
                return
            profile, _ = owner
            for kind in ('approval', 'clarify'):
                _service.store.cancel_attention(profile, session_id, kind)
            if completed:
                _service.store.enqueue(profile, session_id, run_id, 'complete')
            _owners.pop(session_id, None)
    except Exception:
        pass


def validate_subscription(raw):
    import base64
    from cryptography.hazmat.primitives.asymmetric import ec
    if not isinstance(raw, dict) or not isinstance(raw.get('keys'), dict):
        raise ValueError('Invalid browser subscription')
    validate_endpoint(raw.get('endpoint'))
    try:
        keys = {key: raw['keys'][key] for key in ('p256dh', 'auth')}
        decoded = {}
        for key, value in keys.items():
            if not isinstance(value, str) or len(value) > 128:
                raise ValueError('Invalid subscription key')
            decoded[key] = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        if len(decoded['auth']) != 16:
            raise ValueError('Invalid subscription key')
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded['p256dh'])
    except (ValueError, KeyError, TypeError):
        raise ValueError('Invalid subscription key') from None
    return {'endpoint': raw['endpoint'], 'keys': keys}


def device_api(action, body, request_profile):
    """Called only after existing authentication, CSRF and body-size guards."""
    import re
    if not isinstance(body, dict):
        raise ValueError('Invalid push request')
    profile = canonical_profile(request_profile)
    if canonical_profile(body.get('profile')) != profile:
        raise ValueError('Profile changed; reopen settings')
    device = body.get('device', '')
    if not isinstance(device, str) or not re.fullmatch('[a-f0-9]{64}', device):
        raise ValueError('Invalid device identity')
    if _service is None:
        return {'available': False, 'enabled': False}
    store = _service.store
    if action == 'enable':
        subscription = validate_subscription(body.get('subscription'))
        store.enroll(device, profile, subscription)
    elif action == 'disable':
        store.disable(device, profile)
    elif action == 'test':
        if not store.enabled(device, profile):
            raise ValueError('Enable this device and profile first')
        import uuid
        if not store.enqueue(profile, '', uuid.uuid4().hex, 'test', device=device):
            raise ValueError('Push test was not queued; check device enrollment and queue capacity')
    elif action != 'status':
        raise ValueError('Unknown push operation')
    return {'available': True, 'enabled': store.enabled(device, profile),
            'public_key': _service.public_key, 'profile': profile,
            'queued': action == 'test'}


def _private_file(path):
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError('Push configuration/key must be owner-only regular files')
        return handle.read(16384)


def start(state_dir):
    """Explicit config only; never generate keys or install dependencies at runtime."""
    global _service
    config_path = os.environ.get('HERMES_WEBUI_PUSH_CONFIG')
    if not config_path or _service is not None:
        return
    try:
        import base64
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid
        from pywebpush import webpush  # noqa: F401
        config = json.loads(_private_file(config_path))
        subject = config['subject']
        if not isinstance(subject, str) or not subject.startswith('mailto:') or len(subject) > 254:
            raise ValueError('A VAPID mailto contact is required')
        key = serialization.load_pem_private_key(_private_file(config['private_key_file']).encode(), None)
        vapid = Vapid(private_key=key)
        public = base64.urlsafe_b64encode(vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)).rstrip(b'=').decode()
        service = PushService(PushStore(Path(state_dir) / 'push'), vapid, public, subject)
        # Pending attention is process-owned, unlike settled completion. On restart
        # fail closed until a restored run's canonical owner projects it again.
        with service.store.connect() as db:
            db.execute("UPDATE deliveries SET state='resolved' WHERE kind IN ('approval','clarify') AND state='pending'")
        _service = service
        service.thread = threading.Thread(target=service.loop, name='webui-push', daemon=True)
        service.thread.start()
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Web Push unavailable: check private configuration and optional dependencies')
        _service = None


def stop():
    global _service
    service, _service = _service, None
    if service:
        service.stopped.set()
        if service.thread:
            service.thread.join(timeout=12)
    with _owner_lock:
        _owners.clear()
