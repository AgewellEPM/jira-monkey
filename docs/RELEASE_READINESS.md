# Worldwide release qualification

The goal is the full standalone Monkey workdesk: local conversation and drafting,
live banana-themed terminal navigation, general task execution, its own MCP/API,
service integrations, model routing, evidence-backed learning and reviewed
evolution, portable deployment and signed work traces. Completing one subset
does not complete this goal. The default installed command selects the local
0.6.0+dashboard.1 interface refresh; the source candidate is 0.7.0.dev14.
Public deployment is not qualified.

## Required evidence

| Gate | Evidence required for completion | Current evidence and remaining work |
| --- | --- | --- |
| Original standalone contract | Every T01–T36 case in the supplied build specification, plus the exact installed demo, restart and model-outage scenarios | `tests/test_runtime.py` retains the original case mapping. Source results and installed results have distinct scope; see VALIDATION.md. |
| Live terminal and ticket navigation | Actual installed prompt, concurrent typing/activity, banana menu, current/finished trees, sign-off calendar, narrow terminal, past and planned dates, cancellation and clean exit on supported terminals | PromptSession fixtures exist. Physical Terminal/PowerShell and complete Linux interactive qualification remain required. No browser-runtime substitute is permitted. |
| Web face for the same worker | Authenticated same-session commands, live journal, ticket/draft review, calendar, scoped approvals, response-loss handling, usable native browser layout and cleanup | Implemented in dev12 and backported to the local installed worker. Actual installed CLI/HTTP shared-state and lifecycle checks passed. IsolatedTester launched the installed system-WebKit NativeBrowser but found no capturable window; native visual verification remains blocked. Remote multi-user hosting is separate work. |
| Local models | Pinned installed models for chat and drafting, actual worker outcomes, responsive exact control during inference/outage, original budgets, held-out intent/target gate | Prior held-out interpretation was 189/200 (94.5%), below the specification's 95% gate. This is not task completion accuracy. General coding trials have not passed. |
| Owned MCP/API and integrations | Real stdio, Streamable HTTP and SSE lifecycle; explicit HTTP/OpenAPI operations; managed secret setup; exact schema/authority checks; real service-account reads, approved effects, response-loss reconciliation and cleanup | Local protocol fixtures exist. Teams/Planner, Asana, monday.com, Salesforce and QuickBooks account operations remain unqualified. Arbitrary API/schema compatibility has not been proved. |
| General execution and research | Real plans, scoped file changes, installed multiprocess build/test tools, retained sources, bounded reconsideration and verified outcomes in new and existing workspaces | Dev13 passed ten real container cases. Dev14 passed installed managed setup/reuse, killed-owner cleanup, restart and captured-environment worker checks on macOS. Model decisions were scripted. General dependency acquisition, Linux/Windows management and actual model outcomes remain open. See MANAGED_BUILDER.md, BUILD_RUNNER.md and CONTAINER_RUNNER_PLAN.md. |
| Model routing and specialists | Captured per-role rosters, measured success, bounded transport fallback, retained usage, bounded specialists, unchanged permissions across model changes | Routing fixtures exist. Real multi-model task-quality and performance qualification remain. Provider refusals and permission denials cannot grant a different model more authority. |
| Learning and self-improvement | Source-linked reusable procedures, held-out outcome improvement, isolated candidate evaluation, operator-reviewed promotion and demonstrated rollback | Advisory experience exists. General evaluation/promotion and independent native DGM requirements remain unfinished. Existing Kist compatibility does not establish standalone self-improvement. |
| Windows execution and observation | Exact enrolled Windows 10 and 11 guests, authoritative provider enrollment/admission, before/after observations, independent proofs, uncertain-effect recovery and verified guest cleanup | Windows 10 console timed out; Windows 11 failed to start. Native provider and independent verification gaps remain. All prior test sessions were closed. See ENTERPRISE_BLOCKERS.md. |
| Trace coverage | Durable effect claims and receipts, exact arguments or protected references, file pre/postimages, processes/network destinations, cancellation/unknown effects, public signature verification, tamper rejection and retained external evidence | Application-level signed journals pass fixtures. Exhaustive OS/remote-internal tracing is not established; a local signing key supplies integrity, not independent attestation or correctness. See SIGNED_TRACE.md. |
| Platforms and delivery | One frozen artifact per supported OS/architecture; fresh install, upgrade, state compatibility, backup/restore, rollback, uninstall, signing/distribution policy and usable diagnostics | Prior macOS ARM64/Linux ARM64 CLI subsets passed. Windows 10/11 and Linux x64 remain incomplete. Dev9 is separately qualified before promotion. Package resolution alone proves no native result. |
| Release security and operations | Exact-release adversarial and recovery tests, reviewed permissions/secrets, dependency inventory and current advisory check, operator recovery procedures and real integration validation | SECURITY.md describes prior checks and remaining boundaries. New changes need relevant regression tests and installed evidence; historical green checks do not certify a new artifact. |

The original specification is
`~/Downloads/Jira_Monkey_Standalone_Build_Spec.md`; the accompanying coding-agent
prompt is beside it. GENERAL_AGENT_PLAN.md and ENTERPRISE_PLAN.md retain the
later implementation scope. VALIDATION.md records dated measurements and links
to durable private reports, including failures. A gate remains open when its
evidence is absent, indirect, scoped to another artifact, or contradicted by a
failure. Publication waits for the full required evidence; this table is not a
release certificate.

## Current implementation work

Dev9 removed unnecessary MCP, server and UI imports from exact command startup.
First-use SDK initialization runs off the prompt event loop. Completion and
approval checks are shared application logic rather than terminal-browser
dependencies. Fresh-process tests block optional imports and network attempts
while checking help, status, pause/cancel, signed history and public verification.
The MCP memory watchdog now retains its exact bound and stop reason. Installed
benchmarking compares complete new-process commands with private empty states
and disabled bytecode caches. Its installed macOS results are in VALIDATION.md.
Dev10 adds modern terminal styling while retaining those startup and control
changes. Physical Terminal testing remains denied by TinkyVision's sensitive-app
policy; formatted-output checks do not replace that gate.

Dev11 adds bounded local-model stream receipts and committed provisional progress
for status and the terminal rail. Cancellation, response loss and rejected output
retain hashes/counts without creating a completed decision. The installed coding
qualification helper now requires both the authored tests and separate arithmetic
cases withheld from model context; an awaiting-review state alone is insufficient.
These changes improve diagnosis and evidence. They do not establish model task
quality, general build containment or the other open release gates.

Dev12 adds the local web workspace and modern terminal refresh to the installed
0.6.0 worker as 0.6.0+dashboard.1. Its browser commands retain separate operator
provenance, exact review bindings and durable request IDs. The existing MCP/API
does not inherit browser operator authority. This UI installation does not
promote the unfinished general build backend or qualify public deployment.
