# Runtime implementation record

## Historical releases through 0.3.1

Baseline, September 13, 2026: `python3 -m unittest discover -s tests -v`
passed 30 tests (0.243s), using Xcode Python 3.9.6. Source is this directory;
the installed command runs a copied payload in `~/.local/share/jira-monkey`.
There is no Git repository here. Original source/tests are preserved in
`backups/preview-20260913`. No existing `~/.jira-monkey` state was present.

The preview has synchronous urllib provider adapters, signal-based deadlines,
one JSON file per job, an exclusive per-command flock and a blocking input REPL.
Its exact snapshot keys are source, instance, key, revision, title, body.
Import also accepts a sole `ticket` wrapper. Its provider routes are Claude
Messages, OpenAI Responses and DeepSeek Chat; Ollama performs triage/review.
It reserves attempts, requires notes for manual decisions, confirms exact Jira
keys, saves before POST, and reconciles lost responses without resending.
It lacks bound approval hashes, read-back verification, async input, an event
journal, total-call budgets and a conversational model.

Compatibility: keep snapshot validation and provider payload contracts; retain
bare command forms and `run` as one attempt. Slash commands are aliases.
The runtime adds explicit work/delivery states; a passing reviewer never grants
operator approval, including under the old `queue` policy. Legacy JSON records
are backed up and imported without resetting counters; old approvals require
renewal because they did not bind an exact payload. Historical uncertain sends
stay blocked. The original Python helpers remain for compatibility/baseline
tests; the installed entrypoint uses the new runtime exclusively.

Implementation uses Python 3.11+, prompt_toolkit 3.0.52, httpx 0.28.1, stdlib
strict schema validation, SQLite WAL and lifetime ownership of the old lock.
There is one foreground worker and no implicit service. No repository is needed.
No real Jira writes are authorized as part of this build.

Completed runtime: transactional SQLite records and recovery, foreground queue,
typed commands and local conversation, explicit bound approvals, one-shot outbox,
read-back and reconciliation, offline fault demonstrations and actual loopback
HTTP response-loss tests. The original preview backup from the first upgrade is
`~/.local/share/jira-monkey-backup-cw8feo2x`; subsequent 0.2/0.3 payloads also have
separate installer backups. User state has not been populated with synthetic jobs.

Luke chose installed Ollama for all model work. Monkey owns the tags
`jira-monkey-chat:latest` and `jira-monkey-worker:latest`, copied from existing
Qwen3 VL 4B instruction weights and pinned to the measured digest. At that stage there was no download,
cloud fallback, Kist dependency, repository worker or real Jira post.

Version 0.3.1 follows Luke's Terminal feedback: `Monkey` launches from PATH;
banana Start ticket, tree Current tickets, sunset Finished tickets and calendar
Past/completed tickets replace the static ideas. A responsive keyboard browser
opens overview/draft/event/sign-off tabs and dates recorded outcomes. Ordinary
ticket, sprint and support requests now create local work directly; imports can
carry non-Jira source labels while only Jira snapshots can enter the Jira outbox.

Validation: 93 automated tests passed. `Monkey --version` and launcher resolution
were checked from another directory; real installed Ollama drafted and reviewed
the plain sprint-ticket example. The actual native 80×24 screen was captured with
TinkyVision. Its Terminal deny-list blocks native typing automation, so those
writes stopped; arrow/input behavior was tested within PromptSession. Test REPLs
were stopped, and no isolated guest was launched. The unrelated existing Windows
guest and other Terminal applications were left alone. See VALIDATION.md for
language accuracy limits, raw records and the exact measurement boundaries.


## 0.4.0 — dated work and connected execution

Luke subsequently requested Kist/SML integration and generic API/MCP tools. The
original standalone drafting mode remains; project/tool execution now invokes the
actual installed Kist durable SML runtime through a private foreground HTTP host.
The Monkey capability bridge accepts no model-supplied authority or call arguments.
It verifies the runtime's persisted approval claim before the host-selected effect,
then checks durable observed receipts against the exact returned artifact.

Monkey compiles a private helper against four unchanged Kist source files:
CaptainFacts.swift, CaptainRule.swift, CaptainRuleSet.swift, SymbolicCaptain.swift.
Source and binary hashes are retained. Kist canonical source, production binary
and global autonomy configuration were not edited or promoted. Monkey's source
remains outside a Git repository.

New modules: scheduling (schema 2 and backed-up migration), project_tools
(confined files and pinned Seatbelt verifier), execution (contracts, bounded plans,
HMAC records and sign-off), sml/runtime_child (actual runtime and owner lifecycle),
connectors/api_tools (official MCP SDK and explicit HTTP/OpenAPI operations), and
learning (advisory recall, bounded local specialists, native DGM readiness).

The receipt seal protects against untrusted model/tool artifacts crossing the
host boundary; it is not protection from the owning user with access to the
sealing key. The local verifier sandbox does not provide aggregate resource
containment required by Kist DGM. The actual DGM gate refuses execution, including
manual evolution: no bypass or substitute self-improvement loop was introduced.

Project planning, MCP proposal and specialist smoke checks used installed local
Ollama. Separate tests use actual stdio/HTTP/SSE MCP servers and a loopback HTTP
API, including write response loss, schema drift, exact read-back sign-off and
operator-attested uncertainty resolution. Native Terminal input automation remains
blocked by the configured TinkyVision policy; PromptSession tests are distinct.
See VALIDATION.md and CONNECTORS.md for measured results and supported boundaries.

## 0.4.1 — bounded acting missions

`missions.py` adds local-model mission planning, exact sealed sequence authorization,
one foreground asyncio worker per used role, and host-selected tool execution through
the existing Captain/SML connector path. The shared original job budget covers
planning, clarification, advisory specialists and acting agents. No agent can change
tool arguments, approve itself, reset limits or replay a consumed step.

Mission records retain assignments, model assessments, calls, exact JSON predicates
and final operator sign-off. Questions retain the original objective; a revised
proposal needs new approval. A lost response stops the sequence without advancing.
The ticket browser includes a Mission tab and guided forms. Terminal status follows
committed stages and pause boundaries; completed planning survives restart as ready.

Exact connector plans now retain their job's Kist binary and Captain source recipe.
Later global configuration edits cannot substitute a different runtime for an approved
request. The local helper's evidence scope distinguishes tool admission and operator
predicate attestation from independent service correctness. The four Kist source files
and global autonomy configuration remain unchanged. DGM blockers are unchanged.

## 0.5.0 — Monkey-owned MCP/API and service setup

`services.py` ships native HTTP/GraphQL operation catalogs for Asana, Teams/Planner,
monday.com, Salesforce and QuickBooks Online. Guided setup constructs the adapter
configuration, including exact routes and schemas, without a user-authored file.
The terminal adds Connections and a hidden credential field whose history wrapper
suppresses both in-memory and disk entries. Saved catalog/event records exclude
tokens; private credential-bearing connector files keep mode 0600.

`gateway.py` hosts the official MCP SDK's MCPServer and a small HTTP RPC surface
on loopback, in the live application's event loop. The app retains single database
ownership. Generated client settings contain an ephemeral bearer token; Host and
Origin checks reject browser-origin use and unexpected hostnames. Both transports
share command handlers and client provenance. Clients can capture local work,
retain advisory notes, inspect evidence and propose tools. Only a mission already
authorized through Monkey can start; approvals and sign-off are not exposed.
Shutdown settles the gateway and removes its current client file. A new start
rotates credentials. No daemon, downloaded server package or browser was added.

API operations support explicitly declared conditional headers for Planner ETags.
GraphQL errors and QuickBooks fault envelopes cannot masquerade as success solely
because their HTTP status is 200. All service effects continue through the existing
exact approval, Captain and real SML path. Native operation request shapes are
fixture-tested; production account validation still requires account credentials.
