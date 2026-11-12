# Non-destructive authentication recovery

Classification: upstream-candidate
Implementation base: dec0c9ba38b3ffbe6ab1c266221d638e4de208fd.
Maintenance owner: fork maintainers until upstream adoption.
Upstream status: not-filed. Private details removed: yes.

## Approved behavior / Contract Change

An old chat tab must distinguish authentication required from an unavailable
connection, without throwing away its conversation, composer draft, or unsent
File references. Previously API 401s navigated the current tab to login and
failed soft recovery could reload it. Recovery now never navigates that tab.
A session-expired banner offers **Sign in again**, opening a same-origin,
subpath-aware login URL in a new tab with no opener. No proxy configuration
changes, credential handling, automatic reload, failed mutation/upload replay,
or provider/backend health claim based on a 401 are permitted.

## Architecture / Contract Routing

The existing offline monitor in `static/ui.js` owns transient connection state
and its serialized, bounded probe. Its fetch boundary adds
`X-Requested-With: XMLHttpRequest` only to same-origin app API and health URLs,
preserving Request/init headers and bodies. Existing header values are explicit
caller overrides; cross-origin requests and non-app URLs are excluded.
`webuiAuthRecovery: false` suppresses automatic auth presentation for callers
that already own 401 handling; `api(..., {redirect401:false})` retains that opt-out.

401 means authentication is required somewhere in the serving chain, not that
the backend is healthy or that a particular authentication layer expired.
403, 5xx, network failures and malformed/HTML health responses remain connection
failures, not session expiry. Health success must be confirmed by a protected
read before soft recovery, since native WebUI health is public. Native
EventSource cannot set headers: its error paths use the same header-bearing
probe. Stream ownership, replay cursors, persisted sessions, drafts and upload
recovery remain with their existing owners; the monitor never submits a turn.
Successful recovery only restarts observation and refreshes the current session.
The refresh rejects malformed results and late responses for a replaced pane.
Default API network retries are limited to GET/HEAD; explicit caller retry policies
remain explicit. Dedicated login/share documents and service-worker requests do
not run the chat monitor and are not changed by this patch.
Failed recovery leaves a retryable banner rather than reloading the page.

The run-state consistency RFC is a guardrail for preserving browser state, not
an authorization to change runtime admission or replay semantics. Initial boot's
bounded profile/login routing remains separate from recovery in an existing tab.

## Verification and maintenance

Run `./scripts/test.sh -q tests/test_access_reauthentication.py` plus neighboring
offline, API, subpath, login-loop and upload-recovery tests. The browser fixture
uses production markup/CSS and the actual monitor/API functions with synthetic
HTTP responses; it must demonstrate baseline failure and candidate success.
It is not proof of a real proxy login, real auth revocation, physical mobile
behavior or production installation.

Observed candidate checks: the header-preservation regression failed on the base
because `X-Requested-With` was absent; all 17 browser cases pass through the
normal runner. A separate 287-test component/Node sweep passes with
`--noconftest` and isolated HOME/state (offline, subpath, login-loop, API timeout,
upload recovery, boot redirect budget, pending-user ordering, read-only refresh,
and update banner suites). This deliberately avoids the unrelated autouse Agent
server; it is not a backend integration pass. Production markup, translations and
CSS were rendered at 1280×900 and 390×900; the real new-tab click has no opener.
JavaScript syntax checks and whitespace checks pass. Full-suite/real identity
provider authentication and installation are not exercised.

Rollback is reverting this commit; there
is no migration or new persistent state. Retire when upstream provides equivalent
non-destructive authentication recovery.
