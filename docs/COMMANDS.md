# Monkey commands

All exact commands accept a leading slash. JOB, CALL and HASH are placeholders; use the IDs Monkey records.

```text
Monkey · tickets, sprints & requests
Tell me what you need. I draft tickets, acceptance criteria, plans and replies.

request TEXT                 Draft any ticket or request from your description
task TEXT                    Capture an execution task; choose Project, Tools or Mission
ticket TEXT | sprint TEXT    Ticket and sprint request shortcuts
schedule JOB --start DATETIME --finish DATETIME [--timezone ZONE]
                             Assign a planned start and finish; revisions retained
dates JOB                    Inspect planned dates and schedule history
return JOB --note TEXT       Pause and request new dates; preserve prior evidence
project JOB --path ROOT --objective TEXT --write FILE --verify 'EXEC FILE' --expect FILE=TEXT
                             Select an exact project contract; /project --help
explore JOB | plan JOB       Recorded reads, then a proposed local Ollama plan
authorize JOB --hash HASH --note TEXT   Approve only the inspected plan
execute JOB                 Run approved project tools with durable receipts
execution JOB               Inspect project contracts, plans, tools and receipts
signoff JOB --hash HASH --note TEXT     Sign the exact verified local result
connect [SERVICE]            Guided setup; Monkey generates its own adapter configuration
connect NAME --file PATH     Optional advanced configuration import
services | disconnect NAME  Inspect available adapters / disconnect a service
api start [--port NUMBER]    Open Monkey's own local MCP/API in this foreground app
api status | api stop        Inspect or stop it; client settings are generated privately
trace [JOB] | audit [JOB]    Verify and inspect the signed work journal
audit-export [JOB]          Export a private signed journal bundle
audit-verify PATH --fingerprint HASH   Verify using an independently trusted public key
routes                      Inspect model role rosters and recorded measurements
route ROLE --model NAME [--provider PROVIDER] [--append]
                            Select an exact candidate for future work
setup --routing-policy ordered|measured   Configured order / bounded measured ordering
tools [--server NAME]        Connected tools and their exact input schemas
tool JOB --server NAME --name TOOL --args-file PATH   Preview an exact MCP request
tool-request JOB TEXT        Ask local Ollama to propose a connected tool request
tool-run JOB --hash HASH --note TEXT   Approve and call that exact MCP request
tool-result JOB              Inspect retained MCP results and uncertain outcomes
tool-signoff JOB --help      Sign off against a separate recorded verification call
tool-resolve JOB --help      Resolve an uncertain call using inspected evidence
remember TEXT | recall TEXT  Save advisory notes / search recorded work
delegate JOB TEXT            Queue a bounded local specialist; no tool authority
mission JOB TEXT [--file PATH]  Propose up to six exact tool steps and outcome checks
mission-review JOB           Inspect assignments, literal requests and checks
mission-answer JOB TEXT      Clarify an unexecuted mission; keep its objective
mission-authorize JOB --hash HASH --note TEXT   Approve this exact sequence
mission-run JOB              Start only its assigned bounded foreground agents
agents JOB                   Inspect agent assessments, calls and retained budgets
mission-signoff JOB --hash HASH --note TEXT     Sign off the exact observed outcome
evolve-status [JOB]          Read Kist's actual DGM readiness and blockers
improve JOB TEXT             Retain an improvement proposal with parent evidence

fetch KEY | import PATH       Capture a ticket; no implicit publishing
run [JOB_ID]                  One candidate attempt and review
work                          Process queue and bounded revisions while open
status | jobs [--state STATE] Queue, activity and delivery facts
focus JOB_ID                  Select conversational job (visible in prompt)
show | draft | events JOB_ID  Source, exact candidate, or committed timeline
pause JOB_ID --after review   Request a pause at a stage boundary
resume | cancel JOB_ID        Resume boundary / stop future local stages
retry JOB_ID --note TEXT      Another candidate within original limits
reject JOB_ID --note TEXT     Close local work without posting
approve JOB_ID --note TEXT    Approve exact reviewed payload; no publication
publish JOB_ID --confirm KEY  Explicitly send the currently approved payload
reconcile JOB_ID              Read uncertain delivery evidence; never resend
resolve-delivery JOB_ID --note TEXT  Record operator evidence; keep resend blocked
refresh JOB_ID                Capture fresh ticket evidence, invalidate if changed
recover JOB_ID --note TEXT    Acknowledge interrupted work; budgets retained
models | usage | caps         Actual routes, measured calls, capability limits
doctor | setup --help         Non-mutating diagnostics / public configuration
demo [--scenario NAME]        Offline fixtures with no sockets or credentials
multiline                     Compose until a single dot line; /abort discards
help | quit                   Commands / orderly shutdown; no hidden worker

All commands accept / aliases. Natural language uses a local model, while exact
commands remain usable if it is unavailable. Ticket drafting requires no project.
Project edits require a scoped contract, dates and an exact authorized plan.
```

See [signed tracing](SIGNED_TRACE.md), [model roles](MODEL_ROUTING.md),
[Monkey-owned setup and MCP/API](OWNED_MCP_API.md), [advanced connector configuration](CONNECTORS.md)
and [the ticket navigator](../README.md).
