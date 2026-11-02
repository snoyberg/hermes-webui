# WebUI update channels

The WebUI updater supports three channels. The setting applies only to the WebUI.

## Agent maintenance boundary

Hermes Agent updates belong to the native Agent maintenance workflow, not this
WebUI's generic Git updater. Until a native action/receipt integration exists,
Agent checks report `status: native-maintenance-required`, `behind: null` and
explicit guidance in `error`; this is unknown update availability, not an
up-to-date verdict. Cached legacy Agent offers are replaced by the same status.
The opt-out setting continues to return an ignored Agent target.

Agent apply, force and clear-lock return `ok: false` with
`native_maintenance_required: true` before Git, lock recovery or restart
admission, including direct combined (`both`) helper requests. HTTP mutation
routes accept only a single `webui` or `agent` target and continue rejecting
combined/invalid target shapes. A stale browser offer for both targets tries
Agent first and stops on refusal, without applying WebUI or claiming completion.
A new WebUI-only offer remains usable independently.

This is a fail-closed safety boundary, not native widget update integration:
no native action is started, no channel is changed, and no completion receipt
is synthesized. Native selection, dependency lifecycle, action ownership and
matching receipt/reconnect acceptance remain separate work. WebUI-only
Stable, Experimental and Kaladin updates retain their existing behavior.

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
