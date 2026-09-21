# Monkey in your terminal and browser

Run `Monkey dashboard` from your shell. Monkey prints a private local web link
and opens its regular terminal prompt. Open that link in your browser. From an
existing, updated Monkey prompt, `/dashboard` starts the same dashboard.

The browser and terminal share one application, job queue, worker, model setup,
approval system and signed journal. `/dashboard status` shows the current link;
`/dashboard stop` closes the web listener. `/quit` ends foreground execution and
closes the dashboard too. Closing a browser tab does not stop the terminal worker.
An older, already open Monkey prompt keeps its current code; quit that prompt
and start `Monkey dashboard` to use the refresh.

## The workspace

- **Overview** shows recorded totals and recent tickets and activity.
- **Current tickets** includes open work. **Needs your review** includes missing
  information, approvals and unresolved delivery.
- **Past & completed** shows signed-off or closed work by date. Its planned view
  uses the saved sprint schedule. Closed work is not counted as a published comment.
- **Activity** replays committed events and provides earlier history pages.
- **Connections** shows Monkey's actual service configuration. Guided credential
  entry remains in the terminal's hidden `/connect` form.
- **Ask Monkey** accepts ordinary requests and exact commands through the same
  command dispatcher as the terminal. Each request retains a receipt. Accepted
  work and completed replies are displayed separately. The selected ticket is
  pinned for an asynchronous conversation even when terminal focus changes.

Open a ticket to inspect its source, exact draft, events, dates, tool results,
plan and signed trace. Review dialogs bind approval to the displayed job version
and candidate hash. Publishing presents the exact destination, comment, visibility
and confirmation before sending. Uncertain delivery remains subject to the
existing outbox and reconciliation rules. A lost HTTP response does not silently
resubmit the same dashboard request; its retained request ID identifies it.

## Local access

The listener binds only to `127.0.0.1` and chooses a free port by default. Use
`Monkey dashboard --port 8765` for a specific available local port. The dashboard
has a separate random operator key from the external MCP/API. Its private link
passes that key in a URL fragment; the page removes the fragment and retains the
key in tab-scoped session storage. The key expires when this listener closes.

The server validates the exact host, same-origin command requests and bearer key.
It emits no permissive CORS headers. UI assets are local, ticket text is inserted
as text rather than HTML, and the page uses a restrictive
[Content Security Policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy).
External MCP/API clients retain their restricted authority. They do not receive
the browser operator key or gain approval operations from this UI.

This is a local, personal dashboard. Remote team hosting, shared accounts,
multi-user authorization, public TLS deployment and Internet exposure need their
own implementation and qualification. The foreground application creates no
web daemon or separate worker, and changing interfaces does not add tool or
model capabilities.

## Current installation and evidence

The local `0.6.0+dashboard.1` refresh adds this interface to the installed 0.6.0
worker. Its runtime is staged separately so existing sessions and saved state
are preserved. The source development candidate is `0.7.0.dev12`.
`scripts/stage_dashboard.py` records this limited backport; it does not select a
launcher. The retained installation receipt records the selected entrypoint and
rollback files. Qualification results and the native visual-test blocker are in
VALIDATION.md. This local refresh is not a worldwide release qualification.
