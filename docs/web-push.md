# Private closed-app Web Push

Web Push is optional. With no explicit server configuration, WebUI keeps its
existing page-only notifications and sounds. Nothing installs packages, creates
VAPID keys, enrolls a device, or requests permission automatically.

## Administrator setup (explicit activation)

Use the **same Python environment as the WebUI server** to install
`requirements-push.txt` (`python -m pip install -r requirements-push.txt`).
The transport is `pywebpush` (Web Push encryption and VAPID); WebUI does not
implement those cryptographic protocols itself. Removing the configuration
variable disables this feature without changing conversations or Agent state.

Provision one stable P-256 VAPID private key with your normal secret-management
procedure. Do not rotate it on each restart. Store it as an owner-only PEM file
(mode `0600`, protected parent directory). No key-generation or installation
operation is performed by the application. Create an owner-only JSON file:

```json
{
  "private_key_file": "/secure/location/vapid-private.pem",
  "subject": "mailto:administrator@example.com"
}
```

Set `HERMES_WEBUI_PUSH_CONFIG` to that JSON file in the WebUI service environment,
then use your normal authorized installation/restart procedure. The JSON and PEM
must be owner-only regular files owned by the server user; symlinks are refused.
Never commit either file. Public application-server key material is derived from
the private key and returned only through the authenticated settings API.
Missing dependencies, invalid keys, or unsafe configuration leave push unavailable.

The existing WebUI server owns the delivery thread and stops it on shutdown.
There is no separate service or scheduler. Its private `push/` directory lives
under the existing WebUI state directory (`0700`; SQLite database `0600`). Back up
that directory and the stable VAPID key as secrets. Subscriptions include
capability-bearing endpoint URLs and encryption keys. Do not publish the database,
raw transport exceptions, or subscription JSON in diagnostics.

Only these browser-provided HTTPS endpoints are supported:

- Apple: `web.push.apple.com/Q…`
- Chrome/FCM: `fcm.googleapis.com/fcm/send/…`
- Firefox: `updates.push.services.mozilla.com/wpush/v2/…`

Other providers fail closed. Enrollment checks scheme, exact hostname, path,
absence of credentials/ports/query/fragment, and subscription key shape. Every
send repeats endpoint validation, resolves the hostname, rejects any non-public
address, and connects to that exact public IP with the original hostname for TLS
verification and SNI. Redirects, environment proxies and HTTP retries are disabled.
A custom push proxy is deliberately unsupported.

## Phone and desktop settings

1. Serve WebUI over HTTPS. On iPhone, add WebUI to the Home Screen and open that
   installed app ([WebKit: Web Push for Web Apps on iOS and iPadOS](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/),
   iOS/iPadOS 16.4 or later with Web Push support).
2. Authenticate, select the intended profile, then open Settings → notifications.
3. Under **Private closed-app notifications**, tap **Enable** and explicitly allow
   the browser permission prompt. Enrollment belongs to this device and selected
   profile, not whichever conversation happens to be open.
4. Tap **Send test**. “Queued” is not a delivery receipt. Check the actual phone.
5. **Disable** removes this device/profile's server enrollment and pending records
   and disables that profile in the worker. It does not affect other devices or
   other explicitly enrolled profiles. The underlying browser subscription can
   remain because other profiles may use it. Browser/OS permission can also be
   revoked independently.

Configure normal authentication before enabling push. Push endpoints refuse enrollment on
an authentication-disabled server, even if ordinary no-push WebUI operation is
intentionally unauthenticated.

Repeat Enable separately for every profile/device you intend to receive alerts.
A phone does not have to open a desktop-started conversation before receiving its
result. Profile selection changes are checked on the server on every mutation;
a stale settings operation fails rather than enrolling the wrong profile.
Authentication and same-origin/CSRF checks protect writes. A random device token
is stored in browser localStorage; clearing site storage loses that device's local
identity and opt-in. Disable before clearing site storage when possible. A VAPID
key change can require clearing/recreating the browser subscription and explicitly
re-enabling every desired profile.

While this device/profile is enrolled, the server is the sole notification
producer: page notifications and alert sounds are suppressed. Existing page-only
controls continue working for unenrolled devices/profiles. Desktop work is not
suppressed just because the phone has no live tab. No attempt is made to infer
consent from a session-open event or a subscription left by another profile.

## Privacy, ownership, and delivery limits

All notification types use a fixed `Hermes` title and fixed generic copy. The
server payload schema contains only version, enumerated kind, profile ID, session
ID and a hashed event identity. There is no title/body/snippet/output field to
redact. The worker constructs its own fixed text even if a payload contains extra
fields. Neither pushes nor delivery records contain replies, commands, questions,
filenames, session titles or tool output. Profile/session identifiers remain
routing metadata visible to the local store and browser push service; this is not
an anonymity system.

Completion is emitted from the existing local/Gateway terminal run owners;
approval/clarify projections follow the canonical pending owners. Internal
workers/child chatter and raw process-completion events are not alert producers.
A meaningful result delivered through the parent WebUI run notifies normally.
Taps open an authenticated profile/conversation deep link; they never approve,
answer, replay a command, or submit a message. A different open profile's composer
is not overwritten just to reuse its window.

The SQLite queue deduplicates by recipient/profile/session/event/kind. Sends are
leased, limited to four attempts, use a five-minute maximum TTL (or the prompt's
earlier deadline), and retry only transient failures with bounded backoff. `404`
and `410` prune invalid recipients. Resolution cancels unsent attention records;
a request already in flight cannot be recalled. After restart, unverifiable
process-owned attention records are retired; restored run owners may project
current requests again. Settled completion deliveries survive restart. Records
are pruned after their bounded retention window, with caps of 128 enrollments
and 10,000 retained deliveries. Admission at capacity fails closed rather than
allowing unbounded growth.

`pending` means queued locally; `accepted` means the push provider accepted the
request. Neither proves OS display or that the user answered. Each valid received
push for an enrolled profile calls `showNotification`, including transport retries:
[WebKit requires user-visible notifications for received pushes](https://webkit.org/blog/12945/meet-web-push/).
The worker uses a stable event tag and `renotify: false` to replace the same generic
notification. IndexedDB retains attempted events across worker restarts; replays
also request `silent: true` instead of another audible alert. If a notification
was dismissed, a retry can recreate it silently. An uncertain/failed display keeps
its attempt marker, so a later retry is silent even if the first attempt never
became visible. An aborted storage transaction does not claim an attempted display.
No exactly-once display or platform-independent sound guarantee is made: browsers
and operating systems control replacement, sound, and supported options.

Disable stops future server admission and removes unsent deliveries, but cannot
recall an already received/in-flight push or a display already underway. Invalid
payloads, foreign/disabled profiles and unavailable consent storage do not get a
notification: the worker never infers consent, uses supplied content, or re-enables
a profile to satisfy visibility policy. Those exceptional receipts cannot meet
WebKit's visibility requirement; the browser may revoke the shared subscription,
affecting other enrolled profiles until explicit re-enrollment. This privacy/consent
boundary takes precedence over inventing a fallback alert. Disable or OS permission
revocation cannot guarantee recall of an alert the OS already accepted.

Refresh is fenced by its captured profile and operation generation before worker
writes and after awaited acknowledgments, before changing page suppression or
settings. Consent writes are ordered per profile in the page and worker, including
slow storage/acknowledgment paths. A stale refresh cannot undo a completed Disable
or overwrite a different profile's settings; actions still finish for their captured
profile. A timeout/storage failure is an error, not confirmation of Disable; retry
when the worker is available. Browser storage loss, OS settings, expired TTL, a
server outage, network failure or a crash can lose or duplicate an alert. Phone
lock-screen delivery, actual sound/replacement behavior and tap routing require
physical-device acceptance after authorized activation.

## Synthetic verification

Install dev dependencies normally (`requirements-dev.txt`, `npm install --ignore-scripts`), then run:

```sh
./scripts/test.sh tests/test_private_web_push.py -q
```

This executes real store/policy code, isolated HTTP auth/CSRF/profile dispatch,
real `pywebpush` encryption with synthetic in-memory keys and a mocked transport,
and Node fixtures that execute the real service-worker/page JavaScript. The
worker fixture uses the dev-only `fake-indexeddb` package; alternatively set
`PUSH_FAKE_INDEXEDDB` to an isolated installation. No Apple endpoint or user device
is contacted. These fixtures are not evidence of physical iPhone delivery.
