# Independent supervisor completion receipts

This opt-in local transport reports an **already authorized** independent job's
terminal outcome to its existing WebUI conversation. It never launches jobs,
approves actions or replays commands. Ordinary terminal processes continue to
use native Agent checkpoint recovery; this transport does not replace it.

Use the WebUI source and Python environment with the **same explicit**
`HERMES_WEBUI_STATE_DIR` as the intended server. Before starting the supervisor:

```sh
python -m api.supervisor_completion register --session EXISTING_SESSION_ID --profile EXACT_PROFILE
```

Save the returned receipt ID in the supervisor's existing transaction evidence.
After committing and verifying terminal evidence, the independent supervisor
seals it, even while WebUI is absent:

```sh
python -m api.supervisor_completion complete RECEIPT_ID \
  --outcome 'Terminal outcome summary; verified evidence location'
python -m api.supervisor_completion status
```

Keep the outcome short and non-secret; do not copy raw logs or credentials.
The local SQLite database is private to the OS user and lives in WebUI's state
directory. These commands are same-user local authority, not an unauthenticated
network endpoint. Retain the database with the owning WebUI state. One serving
WebUI process owns each state directory. Receipts are retained for diagnostics;
there is no automatic deletion or scheduling service.

The existing completion drain sweeps up to eight eligible receipts per tick.
It calls normal `start_session_turn`, retaining session model/provider/workspace
resolution, human interruption, busy-session and provider-pause/admission fences.
The exact profile captured at registration is checked again before admission.
Missing or changed owners are held, never recreated or routed to another chat.
Busy/pause/fence refusals defer five seconds; other refused starts have a six
attempt limit. Repeated identical completion writes do not cause another wake.

States distinguish `registered`, `pending`, `dispatching`, `admitted` and `held`.
`admitted` proves normal server-side turn acceptance, **not** successful model
execution, verified job success, phone delivery or human observation. Restart
reconciliation uses the exact receipt prompt in persisted pending/messages or
its submitted turn-journal stream. A dispatch interrupted before its admission
hook can retry; uncertain dispatch after that hook is held if no durable
admission evidence exists. Held rows need inspection, not blind replay. This
conservative ambiguity policy avoids duplicate turns; it does not promise
exactly-once successful model execution across every instruction-level crash.

Installation and a real model wake through the live serving runtime remain
separate acceptance gates. A recording-worker restart fixture proves transport
and normal server admission only. This module does not enroll devices, access
push keys or send push test notifications.
