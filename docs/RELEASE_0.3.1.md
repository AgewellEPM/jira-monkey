Historical release description; superseded by README.md for 0.4.0.

# 🐒 Monkey 0.3.1 — your ticket desk

Monkey handles ticket, sprint, story, bug, support and general request drafts.
It has its own local conversation model, live terminal prompt, durable event
journal, bounded response worker and explicit Jira comment outbox. It has no Kist
dependency. It drafts responses and recommendations; it does not edit code, run
tests, execute shell commands or resolve Jira issues. No repository is required.

## Open the installed application

Type **`Monkey`** in Terminal. `~/bin/jira-monkey` remains available. You can also
open `~/Applications/Jira Monkey.app`, or open
`~/.local/share/jira-monkey/Jira Monkey.command`. It works from any directory.
One process owns `~/.jira-monkey` for its lifetime. Use its open prompt while work
runs; another process using the same state refuses to start. Closing Monkey stops
foreground execution. There is no daemon or attach mode.

The installed model routes are local Ollama at `http://127.0.0.1:11434`:

- Chat: `jira-monkey-chat:latest`.
- Triage, drafting and review: `jira-monkey-worker:latest`.

These are Monkey's own tags copied from installed `qwen3-vl:4b-instruct` weights.
No model was downloaded. The tested digest is pinned in configuration and captured
in each job. Jira itself needs site/email/token configuration before live access.
Conversation remains experimental: the frozen control interpreter scored 189/200 on the
held-out fixture, below the specification's 95% release gate. Exact commands remain
available independently of Ollama. See the validation report for the missed cases.

## Use one prompt

The live navigator uses a Monkey theme:

- 🍌 **Start ticket** — Enter starts a request, or simply type what you need.
- 🌳 **Current tickets** — active work and items still requiring attention.
- 🌅 **Finished tickets** — saved review outcomes, with sign-off shown separately.
- 📅 **Past / completed tickets** — a calendar of signed-off local drafts, verified
  Jira deliveries, and explicitly closed requests. Cancelled/rejected work is
  labeled with its actual outcome; uncertain deliveries remain current.

With an empty input line, use **↑/↓** to choose and **→/Enter** to open. **Esc**
returns. In a ticket, **←/→** or **Tab** switches Overview, Draft, Events and
Sign-off; **↑/↓** and **Page Up/Down** scroll the saved evidence. On Sign-off,
Enter approves the exact displayed candidate locally; a changed candidate must
be reopened. Approval never posts a comment. In the calendar, arrows move by day
or week, Page Up/Down changes month, and Enter opens that day's saved tickets.
Typing a message returns to conversation while the worker keeps running.

Start without Jira configuration:

```text
Draft a sprint ticket for password reset with acceptance criteria
/request Write a support reply asking for reproduction steps
/ticket Prepare a bug report for intermittent login errors
/sprint Plan the account recovery work
```

These create durable local requests and start bounded drafting/review. Original
wording is retained as evidence. Local and other imported tracker drafts can be
copied for use in those systems. The live publishing adapter is Jira; Monkey does
not claim it created or updated tickets in unconnected services.

```text
/fetch APP-123
/work
what are you doing?
stop after review
show teh draft
/approve JOB_ID --note "Reviewed the exact response"
post it
```

Use the real job ID printed by Monkey; `JOB_ID` is a placeholder. `/focus` selects
an unambiguous job and puts its issue key in the prompt. Natural publication
requests display the exact site, ticket, candidate hash, visibility, text and
operation metadata, then require the displayed `confirm TOKEN KEY` within 120
seconds. A plain `yes` does not publish. The existing explicit form also works:

```text
/publish JOB_ID --confirm APP-123
```

`run` performs one candidate attempt and review; triage is retained when valid.
`work` also advances eligible queue work and bounded revisions. Neither implies
publication. Default original limits are 3 candidates, 2 infrastructure retries
and 12 worker provider calls per job. Human retries share these limits. Chat calls
are accounted separately and never remove access to deterministic controls.

Pause requests first report acceptance. The applied event appears after completed
stage output is saved. Pausing the active job stops queue draining. `resume`
continues a retained mid-attempt boundary without consuming another candidate.
After a completed review, the paused candidate remains inspectable. Revision
feedback given during a request applies to a bounded next candidate after review.
Cancellation stops further local stages and cannot undo a remote comment.

See [the command reference](docs/COMMANDS.md) or `/help` for all bare/slash forms,
including events, refresh, recover, reconciliation, models, usage and capabilities.
Exact slash commands bypass inference. Ordinary prose mistakes may be interpreted;
issue IDs, paths, hashes and confirmation tokens are never silently corrected.
Ambiguous references require selection.

Ctrl+C clears input or cancels the conversational request, not the worker. Use
`/pause` or `/cancel` for work control. Ctrl+D or `/quit` shuts down. Tab completes
commands and job identifiers. `/multiline` sends on a single dot line; `/abort`
discards. The rail renders recorded facts without inference or confidence scores.
Non-TTY mode emits no cursor controls; `jira-monkey --json repl` uses JSON lines.

## Configure routes and import evidence

Public JSON configuration is at `~/.jira-monkey/config.json`. Existing preview
keys are accepted. Each job freezes its recipe, local digest pins, allowed
drafting destination, visibility, prompt version and limits. Global changes affect
new jobs. There is no provider substitution or silent cloud fallback. Local mode
requires installed GGUF weights at a loopback origin.

Substitute your real site/email, and provide `JIRA_API_TOKEN` through the launch
environment. Do not enter credentials into Monkey's prompt.

```sh
~/bin/jira-monkey setup --site https://YOUR-SITE.atlassian.net --email you@example.com
~/bin/jira-monkey doctor
```

Optional cloud adapters read only their own `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
or `DEEPSEEK_API_KEY` when explicitly configured. Initial local drafting uses none
of these. Cloud drafting sends bounded ticket evidence to the chosen provider;
conversation stays local. Those live cloud routes were not exercised in this build.
Public settings, including project scope and group/role visibility, are shown in
[config.local.json](examples/config.local.json). Existing jobs keep their captured
visibility. Changing model tags through setup clears old pins for the new settings;
capture resolves installed digests, and explicit pins reject later tag changes.

Imports preserve exactly `source`, `instance`, `key`, `revision`, `title`, `body`.
Jira imports retain their original strict validation. Other source labels such as
`linear`, `github`, `zendesk` or a custom tracker are accepted for local drafting
with an HTTPS origin and an exact ticket reference. Local requests use source
`local` and origin `local://monkey`; they cannot enter the Jira publishing path.
A sole `{"ticket": ...}` wrapper remains supported. See [ticket.json](examples/ticket.json).
The original payload is retained inside provenance with capture time, origin,
hashes and available immutable issue identity. An import is an explicitly chosen
bounded regular file; Monkey does not crawl the home directory. Imported targets
must be verified through Jira before publishing. Identity verification against
unchanged material evidence preserves the draft but requires exact target approval.

## Delivery and recovery

Work and delivery have separate states. Review PASS means eligible for operator
review. Approval binds site, key, immutable issue ID, source snapshot, payload hash,
visibility, policy and operator. It is not permission for a different draft.

Publishing rechecks freshness and author identity, commits the outbox before POST,
saves a creation receipt as `POSTED_UNVERIFIED`, then reads the comment back.
Matching body, visibility, operation metadata and author yield `POSTED_VERIFIED`.
A failed read stays unverified; mismatches require attention. A disconnect,
malformed receipt or interrupted POST becomes `POST_UNKNOWN`. No comment POST is
automatically retried.

Reconciliation checks the issue identity, paginates up to a 10,000-comment bound,
and requires one strong visible match. Zero visible matches, multiple matches or
an incomplete scan remain blocked. The property is a correlation marker, not
server-enforced idempotency. Preflight is not an atomic Jira read/write lock.
No automatic edits, deletes or resends repair uncertainty. `resolve-delivery`
records operator evidence and keeps uncertain sends blocked; V1 has no resend
unlock command.

Restart classifies unfinished work as `INTERRUPTED`, unresolved `POSTING` as
`POST_UNKNOWN`, and durable creation receipts as `POSTED_UNVERIFIED`. Budgets
survive. Inspect evidence and explicitly use recover/retry or reconcile.

## Storage, upgrade and restore

SQLite uses WAL, full synchronous commits, foreign keys, short transactions,
optimistic job versions and a lifetime lock. State, matching events and command
receipts commit together. No write transaction spans a model/network call. UI
subscribers replay journal sequence numbers. Storage errors stop new mutations
and external writes. Chat is separate from authoritative work records. Local
records are retained until the operator archives/removes a closed state directory.

First startup backs up preview JSON and imports completed evidence, counters and
uncertain deliveries. Old approvals require renewal because they lacked exact
payload binding. Original JSON and its backup remain intact. Legacy sends lacking
author/property proof stay unresolved even if similar text is observed. Original
source is also retained in `backups/preview-20260913`.

`install.py --upgrade` checks recognized installed files, then backs up the prior
payload, launcher and app under `~/.local/share/jira-monkey-backup-*`. State is not
deleted. For a manual database backup, `/quit` first, then copy the whole state
directory including WAL/SHM files. Restore matching application and state backups
together. Do not run the preview against migrated state or restore a pre-publication
backup after new remote effects; retain the current database and reconcile those
effects instead. Never delete a failing database as recovery.

## Develop and verify

Python 3.11+, prompt_toolkit and httpx are required. The installed environment is
`~/.local/share/jira-monkey-venv`.

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/benchmark_runtime.py
.venv/bin/python scripts/test_jira_http.py
.venv/bin/python jira_monkey.py demo --scenario response-loss
.venv/bin/python jira_monkey.py demo --repl --scenario pause
.venv/bin/python install.py --upgrade
```

Offline scenarios: pass, response-loss, readback-error, no-match, revise,
revise-once, pause. They exercise the real core using explicitly fake adapters,
with zero sockets and no credentials. The separate HTTP test uses a temporary
loopback server and verifies cleanup. Neither proves live Jira behavior; a real
Jira test requires an explicitly authorized issue.

See [measured validation and limits](docs/VALIDATION.md) and
[the implementation record](docs/BUILD_NOTES.md). Reports distinguish raw-model,
development-interface and held-out results, preserve failures, and state exactly
which timing boundary was measured.
