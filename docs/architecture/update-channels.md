# WebUI update channels

The WebUI updater supports three channels. The setting applies only to the WebUI; Hermes Agent update checks and applies remain channel-neutral.

| Channel | Tag family | Source | Apply behavior |
| --- | --- | --- | --- |
| Stable | `v*` | configured `origin` | Existing release/branch behavior |
| Experimental | `exp-v*` | configured `origin` | Existing release/branch behavior |
| Kaladin | `kaladin-v*` | `https://github.com/snoyberg/hermes-webui.git` | Tag-only, fast-forward only |

Kaladin is a fixed-source channel. Checks and applies fetch only
`refs/tags/kaladin-v*:refs/tags/kaladin-v*` from the trusted source, without
`--force`. It never falls back to an upstream or default branch. A moved tag,
fetch failure, absent tag, or non-fast-forward tag therefore fails closed.
Compare links also use the trusted source repository rather than the checkout's
`origin`.

Normal Kaladin apply uses `git merge --ff-only <selected-tag>` after the narrow
fetch, then follows the updater's existing drain, restart, and reconnect flow.
The destructive force-update path refuses to abandon a checkout whose HEAD is
preserved by a reachable `kaladin-v*` tag and directs the operator to resolve tag
provenance manually.

Switching channels changes only the release family used for later WebUI checks;
it does not rewrite the checkout immediately. Stable and Experimental retain
their existing behavior.
