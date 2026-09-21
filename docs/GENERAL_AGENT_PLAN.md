# General Monkey agent — active implementation requirements

Objective: give Monkey full planning, execution, research and reconsideration;
support freeform coding/building without ticket setup; learn and evolve through
repeated work. A connector catalog or remembered prose alone does not satisfy it.

1. General work intake: `build`, `research`, and natural language start foreground
   work without a Jira ticket, external account, pre-existing source file or verifier.
2. Real agent cycle: retain a plan, choose typed tools, observe their actual results,
   evaluate progress, revise after failure or operator steering, and verify an
   outcome before reporting readiness. Preserve pause/cancel/recovery and budgets.
3. Coding tools: scoped project navigation, search/read, create/edit/move/remove,
   run real installed build/test tools, inspect output and retain before/after evidence.
   Existing projects and new empty workspaces both work.
4. Research tools: public search and URL reading with actual dated source snapshots,
   citations, uncertainty and follow-up research; available inside coding work too.
5. Connected tools: use discovered MCP/API schemas, retain result references, and
   request concrete external-action authority in the same prompt when needed.
6. Reflection and learning: inspect failures and successful corrections; retain
   source-linked, versioned procedures; retrieve/apply relevant prior procedures
   to repeated tasks; evaluate candidate changes against held-out task outcomes.
   Demonstrate changed behavior and measured results, not claimed improvement.
7. Self-improvement: support isolated implementation/evaluation proposals for
   Monkey itself, with reviewed promotion and rollback evidence. Existing native
   DGM gates remain real requirements wherever that runtime is invoked.
8. Installed experience: useful progress, plan/tools/results/lessons in the live
   REPL; exact commands survive model outage; no background terminals to manage.
9. Evidence: adversarial and recovery tests plus installed local-model coding,
   live research, failure/rethinking, repeated-task learning and self-improvement
   demonstrations. Separate fixture results from real performance measurements.

Dev7 captures the application Python runtime in each new general scope, checks it
at inference/tool boundaries and binds it into the result. Upgrade review records
the execution and review hashes without authorizing more actions. Older unpinned
jobs retain their unknown runtime provenance and cannot implicitly resume on new
code. This is an upgrade control, not self-improvement evaluation or qualification
of dependencies, native programs, containers or another operating system.

Inherited controls: signed traces, explicit external-effect authority, original
budgets, no browser-runtime tooling, no secret/host-policy expansion from source
content or memory. Resource/OS limitations must be reported and resolved where
they prevent the requested behavior; do not relabel a draft as executed work.
