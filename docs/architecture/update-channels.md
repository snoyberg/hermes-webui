# WebUI update channels

The WebUI updater supports three channels. The setting applies only to the WebUI.

## Agent maintenance boundary

Agent checks and ordinary apply use `api/native_agent_update.py`, not the
WebUI Git updater. A fresh update check calls the existing native
`GET /api/hermes/update/check` (which may mutate the native source-check cache).
Explicit manual Check now propagates `force=true` to the native source check;
ordinary checks retain its cache policy. Cached WebUI GETs never contact native
maintenance, and expose only the locally computed installation/profile owner
identity even before the first fresh check. Offers are scoped to the
exact installation and active profile; WebUI channel selection is never sent to
Agent. Native `held`, unknown availability and non-applyable guidance are retained.
The opt-out setting returns an ignored Agent target.

The server-only bridge identifies an already-running owner through the private
control socket, validates the existing native endpoint/profile protocol, and
uses `_session_ticket(..., purpose='native-http')` for each fixed loopback HTTP
request. It never ensures/starts a gateway, repairs discovery permissions,
forwards arbitrary URLs/argv, follows redirects, uses proxy environment settings,
or exposes tickets to the browser, logs or persistent files. Current installation
proof is deliberately Linux-only: the native client source must match the
configured checkout, and the same-user owner process must carry the native
isolated launcher's `sys.path.insert(0, root)` source binding and gateway/run
entry point. Its cwd may be the identified control home (the native systemd
layout) or source root. The descriptor must name that control home and serve the
requested profile. This proves launch provenance, not loaded-version equality.
Unsupported/unverified launch layouts refuse without Git fallback.
A profile ticket scopes HTTP state; it does not change the installation's update
subscription or updater process environment.

`POST /api/updates/apply` with `target: agent` submits only the existing native
`POST /api/hermes/update`, with no invented offer/request-id arguments. Its
`status: accepted` and `action_id` do not mean completion. Duplicates have only
the native process-local live-action reuse guarantee, not durable idempotency
across owner restarts. Network/invalid acceptance is non-replayable unknown.
Force, clear-lock, direct inner Git apply and combined (`both`) helpers remain
refused; WebUI-only Stable, Experimental and Kaladin behavior is unchanged.

The browser persists only action ID, installation/profile digest, submission
tracking token and presentation state under `hermes-native-agent-action-v2:<owner>`.
It resolves the current owner through the inert cached GET, not a stale offer,
and persists that owner before submission. A lost acceptance with no ID remains
unknown and non-replayable within that owner, without blocking unrelated owners.
Profile/installation changes and newer tracking fence late save/render/continuation
paths. Boot resolves the active profile before restoring tracking; profile switches
invalidate the old view and resume reads for the selected owner. An unscoped
`hermes-native-agent-action-v1` record is explicitly unknown: it is retained for
manual inspection, never assigned to a new owner, replayed, or globally blocking.
Reload restores tracking, never submission. `POST /api/updates/agent/status` queries the
existing native latest-action status (`GET /api/actions/hermes-update/status`),
then compares the native start header/action ID and receipt ID. Only a matching
receipt certifies `success`, `partial`, `failed` or `refused`; exit zero, process
absence and another action's latest receipt cannot. A missing bounded log header,
unavailable history, disconnect or superseding action yields explicit unknown or
superseded, with manual native inspection rather than hidden retries. Matching
native follow-ups and user-action guidance remain visible even on success.

For mixed targets the browser waits for matching Agent success before applying
WebUI. Page restoration does not resume the WebUI mutation. Agent-only completion
does not automatically restart WebUI: a separate **Reload WebUI separately**
control calls `/api/updates/agent/reload`, rechecks matching native success and the
existing WebUI restart blockers, then uses the existing WebUI restart mechanism.
This is not native certification of WebUI recovery. Accepted policy is deliberate
human interruption timing, bounded native failure/occasional manual repair and
healthy-without-WebUI-recovery; no global admission lease, permanent coordinator,
Agent rollback or coupled release train is added. Independent installation
observation remains unchanged.

## WebUI channels

| Channel | Tag family | Source | Apply behavior |
| --- | --- | --- | --- |
| Stable | `v*` | configured `origin` | Existing release/branch behavior |
| Experimental | `exp-v*` | configured `origin` | Existing release/branch behavior |
| Kaladin | `kaladin-v*` | `https://github.com/snoyberg/hermes-webui.git` | Tag-only; normal apply fast-forwards, force reset is guarded |

Kaladin is a fixed-source channel. Each check or apply fetches the trusted URL in
a temporary bare repository with inherited Git configuration, transport rewrites,
and prompt helpers disabled. Fetched objects are imported without consulting the
checkout's remotes or repository-local configuration. The fetched tag map is then
published atomically under `refs/hermes-webui/kaladin/tags/`; ordinary shared
`refs/tags/kaladin-v*` refs are never authoritative. Tags absent from the trusted
source are pruned from that namespace, while a previously observed tag that moves
causes the fetch to fail without changing the authoritative map.

The updater resolves the selected trusted tag to its full commit object ID once
and uses that immutable ID for graph checks, compare links, fast-forward merges,
force-update protection, and resets. Later mutation of either shared tags or the
isolated namespace therefore cannot change the selected object. Kaladin never
falls back to an upstream or default branch. A moved tag, fetch failure, absent
tag, unverifiable commit, or non-fast-forward tag fails closed; stale local data
is not returned to the UI as an available update. Compare links use the trusted
source repository rather than the checkout's `origin`.

Normal Kaladin apply uses a channel-specific hardened Git runner for every graph,
status, stash, merge, checkout, clean, and reset operation. The runner scrubs
inherited `GIT_*` process overrides, explicitly binds both the checkout's resolved
Git directory and intended work tree, overrides `core.worktree`, and disables
repository-configured hooks and filesystem monitors. It then runs
`git merge --ff-only <selected-object-id>` after the isolated fetch and follows
the updater's existing drain, restart, and reconnect flow. The destructive
force-update path uses the same pinned object ID and hardened runner. It may
replace ordinary divergent untagged commits, but refuses a reset that would make
an authoritative Kaladin tag currently reachable from `HEAD` unreachable from
the selected target.

`/health` includes `webui_revision` when the process can resolve its loaded
checkout to an exact 40-hex commit. The value is captured once at process import:
an in-place checkout update does not change the reported identity until the
WebUI process restarts and loads the new revision. When the server imported
`run_agent` or the dependency-only `hermes_bootstrap` from a Git checkout and its
actual process import path includes the PM-selected dependency environment,
`/health` also reports `agent_generation` (source commit, selected environment
path, SHA-256 of the PM-resolved lock). Bootstrap-based capture leaves `run_agent`
deferred until profile selection; it does not import profile-sensitive modules
for identity reporting. The generation is captured once at import, not refreshed
from later changes to the checkout, PM selection, or lock.
Missing or unresolvable Agent/dependency identity is omitted, not inferred from
configuration, version strings, or the current checkout alone. The external
Kaladin release producer binds this exact identity into its verification evidence;
the independent observer must compare it before adopting an installed release or
claiming recovery. This is not an Agent rollback mechanism. Native Update Now
moves the checkout and restarts; it does not write an external deployment adapter's
revision file. An independent observer must reconcile that record from exact
loaded-process health, not assume native apply has updated it.

Switching channels changes only the release family used for later WebUI checks;
it does not rewrite the checkout immediately. Stable and Experimental retain
their existing behavior.
