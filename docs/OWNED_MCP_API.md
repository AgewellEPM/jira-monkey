# Monkey owns its MCP/API

Monkey includes its own service adapters, connection setup, MCP server and
HTTP API. You do not need to write a configuration file or supply somebody else's
MCP server to use the built-in services.

## Connect from Monkey

Open `Monkey`, choose **🍌 Connections**, choose a service, and press Enter.
The same setup is available through:

```text
/connect
/connect asana
/connect teams
/connect monday
/connect salesforce
/connect quickbooks
/services
```

Monkey generates the operation schemas, routes and private configuration. Enter
your service access token in the hidden credential field. That field is excluded
from terminal replies, in-memory history, disk history, the event journal and model
prompts. Credentials are stored in owner-private files with mode 0600 under the
Monkey state directory. Existing configuration files can still be imported through
`/connect NAME --file PATH`.

Salesforce setup also asks for the exact organization URL and REST version;
QuickBooks asks for the company/realm ID and sandbox or production. Asana, Teams
and monday use the documented default public API routes. These are access-token
connections: Monkey does not create vendor accounts or invent account authority.
OAuth application registration, consent and automatic token refresh are not yet
implemented. An expired token must be replaced through setup.

Saving a built-in adapter lists its available operations locally. It **does not
send a service request** or claim account access has been verified. Use an explicitly
reviewed read operation to check the real account. `/disconnect NAME` prevents new
calls through that connection and retains prior work receipts and configuration
history; it does not revoke the token at the vendor.

## Included service operations

| Adapter | Included operations | Source |
| --- | --- | --- |
| Asana | Workspaces, user, projects, project tasks, get/create/update task, task comment | [Asana task API](https://developers.asana.com/reference/createtask) |
| Teams / Planner | Teams, channels, plans, plan tasks, get/create/update task, get/send channel message | [Planner](https://learn.microsoft.com/en-us/graph/api/planner-post-tasks?view=graph-rest-1.0), [channel messages](https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0) |
| monday.com | User, boards, board metadata, item, create item, update columns, add update | [Items](https://developer.monday.com/api-reference/reference/items), [versioning](https://developer.monday.com/api-reference/docs/api-versioning) |
| Salesforce | Task metadata, get/create/update Task | [REST record creation](https://developer.salesforce.com/docs/platform/api-rest/guide/dome-sobject-create.html) |
| QuickBooks Online | Company information, customer, invoice, create invoice | [Intuit setup](https://intuitdeveloper.github.io/getstarted/), [invoice reference](https://developer.intuit.com/app/developer/qbo/docs/api/accounting/all-entities/invoice) |

These 32 operations are a documented starting catalog, not every vendor endpoint.
List responses retain returned pagination information; the built-in listing tools
do not silently claim they retrieved every page. Account permissions and business
validation still apply. No production vendor account was contacted during testing.

Planner updates require an exact `If-Match` value from observed evidence. Conditional
headers cannot replace credentials, routing or framing headers. monday GraphQL
documents are fixed in the operation schema; variables carry the reviewed data.
GraphQL `errors` and QuickBooks `Fault` envelopes count as reported errors even if
HTTP returns 200. A service response remains unverified until the recorded outcome
checks and operator sign-off succeed.

Choose **Another MCP server** to enter an endpoint and optional token. Monkey
discovers its actual tool catalog. Choose **Another HTTP API** to enter a base URL,
operation name, method, relative path and optional JSON body schema. Monkey builds
the configuration. Advanced multi-operation and OpenAPI imports are still available
through the existing configuration import path. See [CONNECTORS.md](CONNECTORS.md)
for its supported protocol and schema boundaries.

## Start Monkey's own MCP/API endpoint

While staying in the same Monkey prompt:

```text
/api start
/api status
/api stop
```

The server binds only to `127.0.0.1`. A free port is selected unless you use
`/api start --port NUMBER`. It shares the live application's state, worker and
approval journal. `/quit` shuts it down. It does not launch a daemon or require
another terminal. `Monkey api start` also supports a dedicated foreground server;
Ctrl-C stops that process.

Monkey generates a private `client-*.json` file under `~/.jira-monkey/gateway/` and
shows its path. It contains an MCP `mcpServers.monkey` entry and an `api` entry with
the endpoint and authentication header. Use those generated settings in your MCP
client. The token is not printed. Clients must authenticate on both MCP and HTTP;
browser-origin requests and unexpected Host headers are refused. There is no public
listener or OAuth login exposed by this local server.

Version 0.6 also refuses duplicate authority/framing headers, WebSockets, oversized
or slow requests, duplicate JSON keys and unknown command arguments. Authenticated
traffic is bounded to eight active requests with a burst of 60 and refill of two
per second. Reverse-proxy headers cannot change the peer authority. API clients
cannot change operator focus or attach implicit memory to the selected ticket.

Normal shutdown removes the current generated client file. Restart creates a new
token, so clients must reload the new settings. After a crash, an old private file
may remain, but its stopped listener and token confer no authority on a new server.

## MCP tools and HTTP operations

| MCP tool | HTTP operation | Result |
| --- | --- | --- |
| `monkey_status` | `status` | Recorded worker and delivery state |
| `monkey_jobs` | `jobs` | Captured tasks |
| `monkey_services` | `services` | Available service adapters |
| `monkey_tools` | `tools` | Configured tool schemas |
| `monkey_task` | `task` | Capture local work; no execution |
| `monkey_events` | `events` | Committed task journal |
| `monkey_recall` | `recall` | Evidence and advisory memory |
| `monkey_remember` | `remember` | Bounded advisory note with client provenance |
| `monkey_propose_tool` | `propose_tool` | Exact arguments for operator review |
| `monkey_mission` | `mission` | Request a bounded mission proposal |
| `monkey_mission_result` | `mission_result` | Inspect proposals and actual outcomes |
| `monkey_run_mission` | `run_mission` | Start a previously authorized mission |

The MCP endpoint is `/mcp`. HTTP supports authenticated `GET /v1/status` and
`POST /v1/rpc`, with this shape:

```json
{"operation":"task","arguments":{"text":"Prepare the specified sprint request"}}
```

For example, Python can read the generated private file without placing its token
in a shell argument or chat:

```python
import json
from pathlib import Path
import httpx

settings = json.loads(Path("THE_PATH_SHOWN_BY_MONKEY").read_text())["api"]
response = httpx.get(settings["url"] + "/status", headers=settings["headers"])
print(response.json())
```

There are no API operations for operator approval, final sign-off, changing
credentials, widening project scope, shell execution or enabling evolution. API
requests carry `monkey_api_client` provenance. A mission can run only after its
exact plan is authorized in Monkey, and consumed missions cannot replay. All
service effects pass the captured admission checks and original budgets. New work
defaults to Monkey's own runtime; explicitly captured Kist requests retain theirs.
This server does not bypass native Kist DGM readiness.

## Verification

Tests cover setup without a prewritten file or business-network access, native
request shapes, credential privacy through real PromptSession input, actual MCP
discovery and HTTP requests, authentication, rejected approval escalation, token
rotation, shutdown and no replay. A separate test exercises the owned MCP endpoint
through real Kist/SML against an Asana-shaped private HTTP fixture, with an exact
creation and separate read-back. These tests do not certify live vendor accounts.
Native macOS Terminal keyboard automation remains blocked by TinkyVision policy;
PromptSession input tests are reported separately.
