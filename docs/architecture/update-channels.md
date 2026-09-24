# WebUI update channels

The WebUI updater supports three channels. The setting applies only to the WebUI; Hermes Agent update checks and applies remain channel-neutral.

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
WebUI process restarts and loads the new revision.

Switching channels changes only the release family used for later WebUI checks;
it does not rewrite the checkout immediately. Stable and Experimental retain
their existing behavior.
