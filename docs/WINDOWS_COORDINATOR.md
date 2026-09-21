# Windows coordination in 0.7.0.dev8

Monkey can bind one local task to an exact `legacy.win10` or `legacy.win11`
target and retain its lifecycle, workflow, observation and cleanup reports.
GhostBridge owns the VM, native consent, workflow approval, actuation and
independent cleanup. Monkey does not create those authorities.

The coordinator uses existing configured MCP connections. Its supported request
path is covered by a local streamable-HTTP protocol fixture; this does not certify
an enrolled GhostBridge connection or native Windows execution. The current
installed GhostBridge Node servers still need a supported transport compatible
with Monkey's confinement. Their child-process requirements are not silently
exempted from that confinement.

## Commands

Start with a local task and two actual MCP connections, one for the lifecycle
tools and one for V-Cards. Bind the exact workflow profile from owner enrollment:

```text
/windows-bind JOB --server OWNER --workflow-server VCARDS --target-id legacy.win10 --profile-id EXACT_PROFILE --note "Purpose of this session"
/windows JOB
/windows-plan JOB catalog
/tool-run JOB --hash EXACT_PLAN_HASH --note "Inspected the exact request"
/windows-plan JOB open
```

Every plan is a proposal. Inspect its actual tool and arguments, then use the
existing `tool-run` command to approve that exact hash. Native consent still
applies. Model requests, recalled notes, API callers and tool descriptions cannot
grant approval or redirect a bound Windows task through a generic tool request.

Supported actions are `catalog`, `open`, `status`, `observe`, `close`, `readiness`,
`workflow-start`, `workflow-status`, `workflow-step`, `provider-tick`,
`cleanup-tick`, `workflow-pause` and `workflow-receipt`. All target/session/run
identifiers and CAS digests come from the binding and retained reports.

Only `workflow-start` accepts an argument file. It contains exactly the approved
`workflowDigest`, `dryRunDigest`, `inputEnvelopePath` and `inputExpectedSHA256`.
The path belongs to the separately enrolled owner-side sealed-input vault.
No command, raw text, coordinate, key name or authority override is accepted.

The workflow's returned session must name the same owner session ID, Windows
generation and exact profile. A provider that uses a different session namespace
needs an authenticated ownership integration before it can satisfy this contract;
Monkey does not invent a crosswalk from matching labels.

## Freshness, uncertainty and cleanup

An action proposal requires applicable status/readiness receipts received within
30 seconds. Execution checks again before dispatch. Changed connections or newer
receipts invalidate the proposal. Provider and cleanup ticks require a subsequent
workflow status read; a tick acknowledgement does not establish an advanced state.
Frame observations remain untrusted and do not prove the owner session identity.
The observation path is limited to at most one successful capture per second;
the provider must also enforce its global 1 FPS / 300-frame policy.

Once an open is submitted, this binding cannot issue another open. Dropped,
malformed or denied replies remain inspectable, and uncertainty blocks further
requests through the existing MCP delivery state. Restart performs no replay.

To inspect uncertainty, use a separately approved status read and link its exact
call ID with `windows-sync JOB --call CALL_ID`. The read must come from the pinned
connection and match the exact session/run. Linking preserves its original age,
rejects reused or older evidence, and does not resolve, resend or sign off the
original call. The existing `tool-resolve` command separately records the
operator's inspected outcome predicate and note.

`workflow_completed_reported`, `session_closed_reported`, and the retained
terminal receipt are distinct. The terminal receipt requires a matching run,
workflow and cleanup object; it remains a provider report. Monkey's signature
proves integrity of its recorded evidence, not independent verification of the
native provider's signing keys or the Windows postcondition. Native qualification
must separately exercise GhostBridge's verifier and cleanup on each target.

Execution readiness must be true before workflow actuation. Closing the session,
requesting cleanup and advancing a cleanup-required workflow remain available
when later actuation policy is disabled; their native authority is still enforced
by GhostBridge. Another tracked session must report COLD with its slot released
before a new open can be proposed. The owner remains responsible for the global
one-VM lease, including guests opened outside Monkey.

## Persistence and external inspection

Bindings, original MCP requests/responses, typed projections, errors and linked
inspections are committed in Monkey's existing signed journal. `/windows` reads
that durable evidence while a request is pending. Monkey's own MCP/API exposes
`monkey_windows_result` / `windows_result` for inspection only.

The first binding atomically raises SQLite `user_version` to 4 with its record,
event and command receipt. Older executables refuse that state. Capturing later
general work never lowers this floor. Back up the complete closed state directory
before an upgrade; an executable rollback is not a state rollback.

Binding a task does not launch a guest. Closing Monkey also does not imply that
GhostBridge has closed a session: inspect and approve the exact cleanup/close
sequence, and retain its actual result before ending a native test.
