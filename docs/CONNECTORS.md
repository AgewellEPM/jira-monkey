# Connect a service to Monkey

Use **🍌 Connections** or `/connect` for Monkey-owned setup. Built-in Teams / Planner,
Asana, monday.com, Salesforce and QuickBooks adapters generate their configuration;
another MCP or HTTP endpoint can also be entered in the prompt. Hidden credential
entry does not go into chat or history. No user-authored configuration file is needed.
See [owned adapters and Monkey's own MCP/API server](OWNED_MCP_API.md).

The file formats below remain available for advanced imports. Configuration is
stored with mode 0600. Reconnecting creates a new catalog and makes unsubmitted
plans stale. An adapter being configured does not prove live account access or
certify every operation supported by that vendor.

## MCP configuration

Configure an installed server, with an absolute executable and script path:

```json
{
  "transport": "stdio",
  "command": "/absolute/installed/python",
  "args": ["/absolute/installed/task_server.py"],
  "env": {},
  "sandbox": {"read": [], "write": [], "network": []}
}
```

No server package is downloaded. `npx`, `uvx`, shell launchers and prohibited browser
runtimes are refused. Executable/script hashes are checked before calls; imports
and dependencies are not transitively pinned. In 0.6, local servers additionally
require Monkey's verified macOS subprocess sandbox. Older local connections must
be reconnected to capture confinement. HTTP MCP remains available on other systems;
unconfined stdio is refused.

Read grants name exact files or directory trees; nested credential/guidance paths
remain denied. Write grants name at most 16 exact files, never directory-wide
output. Monkey state, installed runtimes, whole-home access and startup environment
overrides are refused. Each child gets an empty, read-only private home, sanitized
environment and no process forks or unapproved executables. CPU time (30 seconds),
open files (128) and each written file (8 MiB) have kernel limits. A 384 MiB RSS
watchdog polls every 50 ms; it is not an aggregate or instantaneous kernel quota.
Sessions end within 45 seconds, with bounded protocol output and cleanup.

`network` may explicitly grant a loopback TCP port, such as `127.0.0.1:12345`.
The macOS filter allows loopback addresses at that port. Arbitrary remote-IP grants
are refused because this sandbox does not provide that filter. DNS and other ports
remain denied. Use the HTTP MCP/API adapter for remote services. Programs needing
arbitrary temporary files, child processes or other permissions are unsupported.

For an already provisioned remote server:

```json
{
  "transport": "streamable-http",
  "url": "https://your-configured-mcp.example/mcp",
  "headers": {"Authorization": "Bearer YOUR_PRIVATE_TOKEN"}
}
```

The URL and token above are placeholders. `sse` is also supported. Only HTTPS is
allowed remotely; loopback HTTP supports local services. URLs cannot contain
embedded credentials, query credentials or fragments. Redirects are refused.
`mcpServers` configuration maps are accepted when `/connect NAME` selects an exact
entry; `type: http` is an alias for Streamable HTTP.

The official MCP SDK handles initialization, negotiation, paginated tool discovery,
tool invocation and transport cleanup. Tool arguments are validated again against
the discovered JSON Schema. Catalog limits: 20 pages, 1,000 tools, 1.5 MB. Input
arguments: 45 KB. Retained results: 1 MB. Unknown schema references fail rather
than fetch more URLs. Depth, expansion and node counts are bounded. Patterns use
a conservative subset: simple anchored atom repetition and bounded fixed sequences;
nested repetition, branches, lookarounds and backreferences are refused. A peer's
complex schema may therefore need a simpler explicit adapter. Full server content
is retained, including unrendered image
or audio fields; the terminal displays evidence text and JSON, not arbitrary media.

No sampling or elicitation callback grants server authority. OAuth acquisition,
interactive server input, deferred-task polling and the full resources/prompts
surface are not implemented. Thus this is a tested MCP tool client, **not 100% of
all MCP extension behavior**. Unknown or unsupported capabilities fail visibly.

## Explicit HTTP APIs

A connection can describe exact operations, using the same preview/approval path:

```json
{
  "transport": "api",
  "base_url": "https://your-api.example/v1",
  "headers": {"Authorization": "Bearer YOUR_PRIVATE_TOKEN"},
  "operations": [{
    "name": "create_task",
    "description": "Create a task in the explicitly selected project",
    "method": "POST",
    "path": "/projects/{project}/tasks",
    "inputSchema": {
      "type": "object",
      "properties": {
        "path": {"type":"object","properties":{"project":{"type":"string"}},"required":["project"],"additionalProperties":false},
        "body": {"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}
      },
      "required": ["path", "body"],
      "additionalProperties": false
    }
  }]
}
```

This example is a fixture contract, not any named vendor's API shape. Use the
real service's documented method, path, schema and credentials. Arguments live in
an operator-selected JSON file, for example:

```json
{"path":{"project":"EXACT_PROJECT_ID"},"body":{"title":"Reviewed task title"}}
```

Supported top-level arguments: `path`, scalar `query`, JSON `body`, UTF-8
`raw_body`, or explicitly allowed conditional `headers`. An operation's
`request_headers` may allow only `If-Match` and `If-None-Match`; tool arguments
cannot supply authentication or routing headers. JSON and raw bodies are mutually exclusive. Operation `content_type`
defaults to `application/json`; raw requests can explicitly specify another media
type. A GraphQL API can use an exact POST operation with a schema for query and
variables. Path values are encoded; arguments cannot replace the base origin or
credential headers. There is one HTTP request, no automatic retry and no redirect.
Authentication tokens are configured out of band; automatic refresh/login is not
implemented. gRPC, WebSockets and custom protocols need an appropriate MCP server
or a future explicit adapter.

## Import selected OpenAPI operations

```json
{
  "transport": "api",
  "base_url": "https://your-api.example/v1",
  "headers": {},
  "openapi": {
    "file": "/absolute/service-openapi.json",
    "operations": ["getTask", "createTask"]
  }
}
```

The importer accepts a local OpenAPI 3 JSON file and explicitly selected operation
IDs. It uses the operator's base URL, never inferred `servers` entries. Local,
non-recursive references, scalar path/query parameters and JSON request bodies
are supported. Recursive/remote refs, reference siblings, nullable syntax, complex
parameter encodings and header/cookie parameters need an explicit operation schema.
It does not silently approximate unsupported shapes. Compiled operations are
snapshotted; editing the source document requires reconnecting to affect new work.

## Use and inspect the same prompt

```text
/connect work --file /absolute/private-connection.json
/tools --server work
/tool-request JOB "Look up exact task APP-123"
/tool JOB --server work --name getTask --args-file /absolute/arguments.json
/tool-run JOB --hash EXACT_REQUEST_HASH --note "Checked this service and exact request"
/tool-result JOB
```

`tool-request` uses the captured planning model to propose a request or ask questions. It
cannot approve. `/tool` does not call a model. Both expose the exact arguments and
request hash. The Tools tab previews the request; Enter collects your review note.
Before invoking, Monkey checks current catalog/schema/config/program hashes,
ticket/schedule, original budget and the captured admission rules. Monkey's own
runtime persists an exclusive, sealed claim before invoking the exact callback.
A server's read-only or idempotent hint is not an authority grant.
The exact request pins Monkey's admission source hashes. An explicitly selected
legacy Kist route instead pins its executable and Captain source hashes.
Changing global runtime settings cannot change an already-reviewed request's recipe.
Older requests without this pin must be proposed and reviewed again.

`RETURNED_UNVERIFIED` means an observed response, not verified business success.
Approve a separate read/verification operation, inspect both calls and then:

```text
/tool-signoff JOB --call ACTION_CALL_ID --verification VERIFICATION_CALL_ID --pointer /structuredContent/body/id --equals '"EXACT_ID"' --note "Inspected the action and the persisted task"
```

The latest verification call must belong to the same ticket. Both operation artifacts
and exact response hashes must remain intact. `/pointer` addresses recorded JSON.
For MCP tools that return JSON inside text, `/parsedContent/0/id` addresses a
strict JSON decode of the first text block; it does not invoke a model. A mismatched
expected value blocks sign-off. The signature is an **operator attestation against
a chosen recorded predicate**, not independent proof of every service side effect.
Choose identifiers and checks that establish your intended outcome. Completed
history retains that distinction and invalidates on new work or changed dates.

## Bounded missions with tool agents

`/mission JOB TEXT` asks the captured planning model for a proposal only. `/mission JOB --file PATH`
accepts an operator-written JSON proposal with the same validation. Its shape is:

```json
{
  "understanding": "Inspect the exact configured task",
  "questions": [],
  "steps": [{
    "agent": "researcher",
    "purpose": "Read the exact task specified by the operator",
    "server": "work",
    "tool": "getTask",
    "arguments_json": "{\"path\":{\"task\":\"EXACT_ID\"}}"
  }],
  "checks": [{
    "step": 1,
    "pointer": "/structuredContent/body/id",
    "equals_json": "\"EXACT_ID\""
  }]
}
```

This is an illustrative contract; use your actual discovered tool schema and
documented response shape. Inspect `/mission-review`, then use
`/mission-authorize JOB --hash HASH --note TEXT` and `/mission-run JOB`.
The Mission tab exposes the same controls. `/agents JOB` shows assignments,
assessments, consumed calls, remaining budgets and any clarification request.

Each used role has a foreground worker task. Roles are researcher, worker,
reviewer and vision, at most three agents per original ticket (including advisory delegates).
A mission has at most six steps and twelve exact JSON outcome checks. Steps run
in order. Original provider and tool budgets apply across planning, clarification,
agents and retries. No step receives authority to change its literal arguments.
Agent assessments are advisory, retain source references, and can stop execution;
the host validates the sealed parent approval again before each effect.
The mission and agent identities appear in its command receipts and operation IDs.

For service changes, include a separately observed read-back with checks that
establish your intended outcome. `/mission-signoff JOB --hash RESULT_HASH --note TEXT`
requires all calls, response hashes and approved checks to match. It is an operator
attestation of those recorded predicates, not independent proof of every side effect.
Changing dates invalidates approval and completion. Lost responses stop the mission
with `UNKNOWN`; reconciliation remains explicit and never resends a consumed step.

Unanswered planning questions block all tools. `/mission-answer JOB TEXT` retains
the question and your answer under the original objective, then requests a revised
proposal and fresh approval. After any mission run has started, inspect prior effects
and create an explicit plan for remaining work. There is no automatic replay,
unrestricted delegation or substitution of unknown IDs from earlier results.

## Lost responses and reconciliation

A response loss, interrupted call or uncertain transport result becomes `UNKNOWN`.
The durable call ID, approval and available runtime files remain inspectable.
Monkey does not resend that call. Open a separate inspection ticket, approve an
appropriate read operation and retain its actual response. Then resolve explicitly:

```text
/tool-resolve ORIGINAL_JOB --call UNCERTAIN_CALL_ID --verification INSPECTION_CALL_ID --pointer /parsedContent/0/tasks/0/id --equals '"EXACT_ID"' --note "Inspected this specific operation in the service"
```

This records `RESOLVED_WITH_EVIDENCE`; it does not replay the old request, assert
that a missing record proves failure, or automatically mark work complete. New
work requires a new exact proposal and approval. Call budgets survive resolution.
Jira's existing `/reconcile` comment-specific behavior remains separate.
