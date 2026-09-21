# Model roles and bounded switching

Monkey defaults to installed local Ollama for both chat and work. It can capture
separate model rosters for `chat`, `planning`, `execution`, `vision`, `triage`,
`draft`, `review`, `specialist`, `research` and `reflection`. Each roster has one to three explicit candidates.
Changing models never changes the host's rules, scoped tools or approval checks.

```text
/routes
/route planning --model jira-monkey-worker:latest
/route execution --model jira-monkey-worker:latest
/route vision --model jira-monkey-worker:latest
/setup --routing-policy measured
```

Use `--append` to add a distinct, explicitly selected fallback. Ollama names are
matched exactly against installed models and pinned to their digest; this command
does not download weights. Optional `--provider claude|openai|deepseek` selects a
cloud provider for a non-chat/non-vision role. Supply its exact available model ID
and the corresponding API-key environment variable separately. Monkey does not
invent model IDs, credentials or cloud authority. Cloud work is only allowed when
that destination was captured in the job's recipe. Chat and vision stay local.

A project/mission planner proposes work. Execution-role agents assess already
reviewed literal steps; models do not directly execute shell commands. A vision
agent consumes one bounded PNG/JPEG from an earlier approved MCP call and proposes
whether the next exact step can proceed. It cannot invent a screenshot, new click
coordinates or a permission grant. Capture/click tools need their own configured
MCP adapter and approved steps. No native GUI integration was certified in 0.6.

## Switching and limits

Network failure/timeout and HTTP 429, 500, 502, 503 or 504 may select the next
captured candidate. Other errors stop. Refusals, invalid schemas, incomplete output,
missing model pins, permission errors and missing credentials do not trigger a
different model. OpenAI's explicit refusal output is checked separately from valid
structured output, as described in its [structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).
There is no refusal-circumvention route or unrestricted payload runner.

Every fallback charges the original job's model-call and infrastructure-retry
budgets (defaults 12 and 2). A roster cannot reset these limits. Role selection,
fallback reason and response hashes go into the signed trace. Exact commands stay
available while inference is pending. Local inference remains serialized.

## Measured routing and DGM boundaries

`ordered` is the default policy. `measured` can reorder an explicitly configured
roster when capturing future work. A candidate needs participation in at least
three jobs with recorded outcome checks and operator sign-off. Among qualifying
candidates, observed transport reliability ranks first, then mean response latency.
Unmeasured candidates keep their configured relative order. Existing jobs retain
their captured roster. Refusals receive no ranking penalty.

These are observational participation counts, not causal proof that a particular
model produced a correct task outcome. Sign-off relies on the approved predicates;
different models are not automatically independent reviewers. Fixture ranking tests
are not a real model-performance benchmark. No improvement claim is made without
actual evaluation data. Recalled text cannot rewrite metrics or routing authority.

This bounded adaptation does not turn on native Kist DGM self-deployment. Its
readiness checks still apply; `/evolve-status` reports the actual blockers.
`/improve` retains proposals with evidence. No model can lower those gates,
auto-approve a deployment, or bypass a refusal by swapping itself out.
