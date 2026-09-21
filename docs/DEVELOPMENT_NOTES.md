# 🍌 Monkey 0.7.0 development — general work and portable admission

This source is the 0.7.0.dev14 development candidate. The local installed command
uses 0.6.0+dashboard.1, which adds the terminal/web refresh to its existing worker.
General coding/research tools, portable private storage and offline packaging
are under qualification. The September 15 recovery run passed specific macOS
and Linux ARM64 CLI/state checks and five Windows 10 file/state checks. Windows
10 console qualification timed out; Windows 11 did not start. Those test sessions
are closed. Dev9 passed separate macOS installed startup and service checks.
Dev10 adds a modern banana-themed terminal layout; current-candidate and other
platform qualification remain separate. Dev11 adds bounded local-model stream
progress and interruption receipts, with provisional output kept separate from
completed decisions. See [current release gates](docs/RELEASE_READINESS.md); no worldwide
deployment readiness is claimed.

Dev13 adds an offline container runner for real multiprocess builds, checked
source imports and retained syscall evidence. Ten installed live cases passed,
including a full worker run with explicitly scripted model decisions. General
dependency acquisition and model task quality remain release work. See
[the build runner contract](docs/BUILD_RUNNER.md).

Dev14 adds `/builder setup`, `start` and `stop` to prepare and own a private
foreground build environment. The prompt remains available during setup; a
guardian holds the environment lease and signs its cleanup receipt even if the
foreground owner is killed. Completed work verifies and seals a captured audit
prefix without running its signature checks on the input loop. See
[managed build environments](docs/MANAGED_BUILDER.md) for the contract and
qualification limits.

Run **`Monkey dashboard`** for the web workspace and the terminal prompt in one
foreground session. Or type **`/dashboard`** inside the updated Monkey prompt.
Open the private link it prints to browse tickets, review drafts, use the calendar,
watch recorded activity and talk to Monkey. Both interfaces share the same jobs
and controls. See [the web dashboard guide](docs/WEB_DASHBOARD.md).

New MCP/API work uses Monkey's own durable admission and signed receipts by
default. New scoped project work also uses Monkey's own admission. Kist is an
optional compatibility adapter for previously captured recipes and native DGM.
Existing captured Kist requests keep their pinned recipe. See
[deployment progress](docs/ENTERPRISE_PLAN.md) and
[general-agent requirements](docs/GENERAL_AGENT_PLAN.md) for remaining work.

Type `Monkey` in Terminal. Keep using its prompt while it drafts, plans, executes
approved project tools or calls a connected service. Chat, drafting, planning and
bounded agents use installed local Ollama models by default. Optional, explicitly
configured role rosters can use other supported providers. Monkey checks exact
scope and authority and retains a consumed claim before each connected or
approved project effect. Ordinary ticket response work needs no repository.

Monkey includes its own MCP server, HTTP API and built-in adapters for Teams /
Planner, Asana, monday.com, Salesforce and QuickBooks Online. Choose **🍌 Connections**
or type `/connect`; Monkey builds and owns the configuration. Credentials go into
a hidden setup field. You do not need to prepare a configuration file.

`/api start` opens Monkey's own authenticated local MCP/API while you keep using
the prompt. It generates private client settings automatically. See
[owned MCP/API setup](docs/OWNED_MCP_API.md). Production account operations remain
untested; no external service writes were made during this build.

## Your terminal

The terminal uses a banana-gold and charcoal theme, a workspace navigation column,
a separate content panel and clear selection states. Narrow windows collapse
navigation around the selected section. The calendar keeps all seven weekdays
visible, and detail tabs adapt to the available width. Status and sign-off values
still come from recorded work.

- 🍌 **Start ticket** starts a task, sprint, story, bug or support request.
- 🌳 **Current tickets** shows active work and outstanding decisions.
- 🌅 **Finished tickets** shows recorded outcomes and their sign-off status.
- 📅 **Past / completed tickets** shows signed-off work and explicitly closed
  requests. Tab switches between completed history and planned sprint dates.
- 🍌 **Connections** sets up built-in services or another MCP/API endpoint.

With empty input, ↑↓ selects, →/Enter opens, and Esc returns. Inside a ticket,
←→ or Tab moves through Overview, Draft, Events, Sign-off, Dates, Project, Plan
Tools, Mission and Trace. Narrow terminals show nearby tabs. ↑↓ and Page Up/Down scroll evidence.
Typing uses the same prompt while work runs. There is no background daemon.

```text
Draft a sprint ticket for password reset with acceptance criteria
/focus JOB
what are you doing?
/schedule JOB --start "2026-09-14 09:00" --finish "2026-09-25 17:00" --timezone America/New_York
/return JOB --note "Dependency delayed; choose a new sprint"
```

Use actual job IDs and current dates. Dates require a timezone, start before
finish, and an explicit offset for ambiguous daylight-saving times. Returning work
pauses it and invalidates prior authorization. Set new dates and resume; original
budgets and schedule history remain retained. Dates also have guided browser forms.

## Project work with actual evidence

For general work, `/build TEXT` creates an ordinary local workspace and starts
a bounded plan/tool/check/reflection loop. `/research TEXT` uses retained public
source snapshots and citations. `/agent JOB` shows its actual steps and
`/steer JOB TEXT` changes the direction within its original scope and budget.
The development model trials have not yet passed autonomous coding acceptance;
generated source alone is not a verified outcome. See the active qualification
record before relying on this development feature.

In **Project**, specify the directory, objective, editable/reference files, an
existing verifier and expected result. Missing information produces clarification.
Explore, Plan, inspect the diff in **Plan**, authorize that exact plan, and Execute.
**Tools** shows observed writes, verifier output and durable receipts. **Sign-off**
binds your review to the task, source, plan, dates, rules, files and actual receipts.

```text
/project JOB --path /absolute/project --objective "Correct the calculation" --write calc.py --verify '/absolute/python test_calc.py' --expect 'calc.py=return a + b'
/explore JOB
/plan JOB
/authorize JOB --hash EXACT_PLAN_HASH --note "Inspected the proposed change"
/execute JOB
/execution JOB
/signoff JOB --hash EXACT_RESULT_HASH --note "Inspected the actual result"
```

Writes are confined to named UTF-8 files, with prior-hash checks and read-back.
Verification uses a pinned existing executable and verifier in a macOS sandbox:
project read-only, scratch writable, host state/network denied, no process forks.
Complex test harnesses needing subprocesses are currently unsupported. This is
not arbitrary shell access or DGM-grade containment. Failed verification preserves
actual edits and failure evidence; Monkey does not silently undo or repeat effects.

Ancestor `AGENTS.md` files are captured with hashes. Drift requires a new contract.
Learned restrictions retain provenance and stay advisory until `/admit-rule` adds
an explicitly reviewed forbidden path. A model cannot expand its write scope.
Symbols enforce authority and evidence checks; they cannot guarantee every model
interpretation or business decision is correct.

## Connected services, memory and specialists

```text
/task Handle the specified task in our connected service
/connect asana
/api start
/tools
/tool-request JOB "Look up the exact task ID specified in this ticket"
/tool JOB --server asana --name get_task --args-file /absolute/arguments.json
/tool-run JOB --hash EXACT_REQUEST_HASH --note "Checked service, operation and arguments"
/tool-result JOB
/remember Ask for reproduction steps when support evidence is missing
/recall reproduction
/delegate JOB "Review recorded evidence and list unanswered questions"
```

MCP uses the official Python SDK with stdio, Streamable HTTP and SSE. HTTP APIs
use explicit operations or selected operations from a local OpenAPI 3 JSON file.
JSON and explicit raw request bodies are supported, including a configured GraphQL
POST. There is no automatic OAuth login, server download, shell launcher,
server-requested model sampling or server-granted approval. See
[connection examples and verification](docs/CONNECTORS.md).

After connecting a service, plain requests such as “Create a task in work”
capture a work ticket and propose a tool request. `/task TEXT` also captures a
work ticket without starting response drafting.

Each action requires approval of its exact request, individually or as part of an
inspected mission. Schemas are rechecked and the captured admission runtime
claims the operation before its effect. Responses begin as server reports,
`RETURNED_UNVERIFIED`. Sign-off requires a separate recorded verification call,
an exact expected value and your review. Tools and Sign-off also expose these
controls. An uncertain call is never automatically resent; `tool-resolve` records
inspected evidence without replaying the consumed call.

Memory contains source-linked notes, work and recommendations. Planning and
specialists receive bounded advisory recall. Memory cannot grant tools, change
host rules or establish completion. At most two specialist tasks queue concurrently,
three per ticket; local inference is serialized. Specialists provide evidence-linked
recommendations and questions, with no independently delegated mutation authority.

For agents that perform tool work, use a **mission**:

```text
/mission JOB "Describe the exact task, destination and how to verify the result"
/mission-review JOB
/mission-answer JOB "Answer the recorded question without changing the objective"
/mission-authorize JOB --hash EXACT_MISSION_HASH --note "Reviewed every step and outcome check"
/mission-run JOB
/agents JOB
/mission-signoff JOB --hash EXACT_RESULT_HASH --note "Inspected calls and recorded outcomes"
```

The Mission tab also guides review, authorization, running and sign-off. Monkey
starts only the needed researcher, worker, reviewer or vision agents, with at most three
agents per ticket and six exact tool steps per mission. Agents assess their assigned
step, cite records and may stop for clarification. The host calls only the approved
literal arguments through the captured admission runtime. Roles use their model rosters;
another model invocation does not establish independent correctness. Vision requires
an image from a previously approved capture tool. Effects run in the approved order.

A mission cannot invent an ID from a previous response or substitute new arguments.
When later work needs an unknown result, first inspect discovery, then review a new
concrete plan. Missing facts or conflicting instructions stop work. Clarification
retains the original objective and budgets; a revised plan needs fresh approval.
An interrupted or consumed mission never replays itself. All calls, agent assessments
and exact outcome checks remain inspectable before final sign-off.

`/evolve-status` reads real Kist DGM readiness. `/improve JOB TEXT` retains a
proposal with parent recipe, source, memory and readiness evidence. **Native Kist
currently refuses DGM execution (0/6 gates live)**: hard containment, external
rollback witness and qualifying private held-out fitness are among the blockers.
Monkey does not bypass the gates, self-promote or claim measured improvement.
Without an explicitly configured Kist adapter, this command reports that native
DGM is unconfigured. Monkey's own self-improvement implementation is unfinished.

## Signed traces and model switching

Every foreground work run records its observed effects and closes with an
Ed25519-signed SHA-256 journal checkpoint. The Trace tab, `/trace JOB` and
`/audit-export JOB` expose model selections, scoped file operations, HTTP/MCP
requests, child-process boundaries, outcomes and approval/state changes.
`Monkey audit-verify PATH --fingerprint HASH` verifies an exported bundle without
the private signing key or live Monkey state. Preserve the fingerprint separately.

`/routes` shows model choices and recorded measurements. `/route ROLE --model NAME`
sets an exact installed local model; `--append` adds a fallback. Cloud providers
require explicit configuration. `/setup --routing-policy measured` may reorder
future job rosters after three operator-signed verified jobs per candidate. It does
not alter existing jobs, grant authority or penalize refusals. Only classified
transport failures permit fallback, within the original call/retry limits.

The trace covers Monkey's observed boundaries, not every system call or hidden
remote-service action. Someone controlling the signing key and all local state
can replace local history; independently retained exports provide a comparison.
Read [audit coverage and verification](docs/SIGNED_TRACE.md) and
[model routing and measurement limits](docs/MODEL_ROUTING.md).

The 0.7.0.dev8 candidate also adds `/windows-bind`, `/windows-plan`, `/windows`
and `/windows-sync` for exact Windows session/workflow supervision with retained
signed evidence. Native GhostBridge consent and owner enrollment remain required.
See [Windows coordination and its qualification limits](docs/WINDOWS_COORDINATOR.md).

## Drafting, publishing and persistence

`/run JOB` creates one candidate and review; `/work` advances the drafting queue.
Original limits: 3 candidates, 12 worker model calls, 24 execution tool calls and
3 project execution runs. Retries and specialists retain the original counters.
Exact commands work without Ollama. A pause acknowledgement precedes its actual
saved boundary. Cancellation cannot undo a remote effect.

Imports preserve `source`, `instance`, `key`, `revision`, `title`, `body`. Jira
keeps strict identity validation; other sources can draft locally. `/approve` and
`/publish JOB --confirm ISSUE-KEY` stay separate. Approval binds exact payload,
source, dates, destination, visibility and operator. The outbox is durable before
POST. Creation receipts are stored before read-back. Lost responses become
`POST_UNKNOWN`; `/reconcile` never resends. A reviewed draft is not proof of code
changes or publication.

Routes are `jira-monkey-chat:latest` and `jira-monkey-worker:latest`, copied from
already installed Qwen3 VL 4B instruction weights and pinned per job. No model
download or silent cloud fallback was added. The prior held-out control score remains
**189/200 (94.5%)**, below the 95% gate; new integration tests do not replace it.

`~/bin/Monkey`, `~/bin/jira-monkey`, `~/Applications/Jira Monkey.app` and
`~/.local/share/jira-monkey/Jira Monkey.command` run the installed payload.
One process owns `~/.jira-monkey`. `/quit` ends foreground execution. SQLite WAL
commits state, events and signed mutation records together. Interrupted operations are retained
without automatic replay; schema migration creates a backup. Connection files
are generated or imported privately into state; enter credentials only in the hidden setup field.

`install.py --upgrade` checks the old manifest and creates a reversible payload/app
backup, preserving configuration and history. Back up the whole state directory
after `/quit`. Never discard uncertain delivery records as recovery.

Portable wheels and offline dependency bundles are described in
[deployment and platform limits](docs/DEPLOYMENT.md). `/doctor` and `/caps` expose
the current platform's implemented execution boundary. Linux and Windows native
qualification is still pending; dependency resolution alone does not establish it.

```sh
~/.local/share/jira-monkey-venv/bin/python -m pip install -r requirements-lock.txt
~/.local/share/jira-monkey-venv/bin/python -m unittest discover -s tests -v
~/.local/share/jira-monkey-venv/bin/python scripts/live_symbolic_smoke.py
~/.local/share/jira-monkey-venv/bin/python scripts/live_mission_smoke.py
~/.local/share/jira-monkey-venv/bin/python install.py --upgrade
```

Tests use private fixtures, actual Kist/SML, local protocol servers and, in the
explicit live smoke, installed Ollama. No browser runtime is used. Native appearance
is inspected through TinkyVision; its Terminal policy blocks native typing.
PromptSession keyboard tests are reported separately. Read
[validation](docs/VALIDATION.md), [commands](docs/COMMANDS.md) and
[security findings and limits](docs/SECURITY.md).
