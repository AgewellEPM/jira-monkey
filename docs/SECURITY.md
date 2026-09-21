# Security review — Monkey 0.6.0

This release hardens untrusted ticket/model content, local MCP servers, connected
HTTP APIs, the owned gateway and stored evidence. Tests use private fixtures and
synthetic secrets. They do not establish that software is impossible to exploit.

## Findings and changes

| Finding or attack | Implemented boundary | Evidence |
| --- | --- | --- |
| A supplied `(a+)+$` schema stalled validation | Bounded schema/input expansion and conservative regular expressions | Original subprocess exceeded 2 seconds; hostile schema now refused |
| A linked credential directory redirected a private write | Descriptor-relative private directory traversal; no followed links | Synthetic token escape reproduced before fix and blocked after it |
| Installed MCP programs inherited broad host access | macOS sandbox, clean environment, pinned launch paths, exact file grants | Real child denied synthetic host reads/writes, fork, exec and ungranted network |
| Peer output or allocation exhausts resources | Bounded protocol framing/output; CPU, file-size and descriptor limits; RSS/deadline watchdog | Actual flood and bounded allocation fixtures stopped and reaped |
| SDK cancellation skipped scratch cleanup | Cleanup shielded from AnyIO cancellation, with guaranteed directory removal | Negotiation-failure and active-cancellation regression tests |
| Foreign-origin requests leak credentials | Every HTTP MCP request is checked against the configured origin; redirects refused | Mock transport receives no foreign-origin request |
| Path/header/JSON ambiguity changes an operation | Strict relative paths, header rules, duplicate-key/nonfinite rejection | Traversal, conflicting auth and ambiguous verification fixtures |
| API client impersonates the operator | Independent core allowlist, provenance and exact targets | Forged approvals/configuration and focus takeover refused |
| Model refusal is routed around | Only classified transport failures can fall back | Refusal, schema, incomplete output and permission fixtures stop at the first model |
| Stored history is changed or shortened | Ed25519-signed SHA-256 chain, store hashes, run manifests and saved checkpoint | Modification, deletion, reorder, rehash, signer replacement and rollback tests |
| Crash leaves a durable manifest before its index commit | Verify and adopt the existing signature, without replay | Fault injection at the index commit; original manifest preserved |

The owned gateway also enforces loopback authentication, Host/Origin checks,
bounded concurrency/rate/body/time, no WebSocket and no proxy authority. Service
writes retain consumed operation IDs and explicit approval. Lost responses remain
uncertain; reconciliation never silently resends them.

## Supply chain and compatibility

Runtime dependencies are pinned in `requirements-lock.txt`; Ed25519 is a direct
dependency. A version-specific OSV query covered all 35 installed distributions.
It found advisories for the old packaging utilities; the dedicated Monkey virtual
environment was upgraded to pip 26.2.1 and setuptools 84.0.0. The subsequent query
returned no advisories, and `pip check` passed. This means no matches in that
database at the recorded time, not absence of undiscovered vulnerabilities.
The metadata source is the [OSV query API](https://google.github.io/osv.dev/api/).

The official MCP SDK remains responsible for protocol negotiation. Its installed
SSE implementation already enforces same-origin endpoint handling; no SSE origin
flaw was claimed as reproduced. Monkey adds its own HTTP route checks and bounded
stdio transport. See [MCP security guidance](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/docs/2026-07-28/tutorials/security/security_best_practices.mdx)
and [connection limits](CONNECTORS.md).

Local subprocess confinement was tested on Apple M4 / macOS 26.0. Other platforms
fail closed for local MCP execution; HTTP MCP/API still has the application-level
controls. No browser runtime, VM, third-party business account or cloud model was
used for these security tests. Native Terminal keyboard testing remains blocked
by the configured TinkyVision policy; PromptSession tests are identified separately.

## Limits that remain visible

Application tracing is not an OS-wide audit or observation of remote internals.
The local signing key is not an external witness: full account/root compromise
can replace all local trust material. Retain fingerprints and signed exports
independently. See [exact coverage](SIGNED_TRACE.md).

MCP imports/dependencies are not transitively pinned. Read access includes runtime
libraries and explicitly granted project trees, with protected-path exclusions.
The RSS watchdog polls; it cannot enforce an instantaneous hard memory ceiling.
Loopback-port grants cannot express arbitrary remote-IP policy, so unsupported
grants are refused. Complex schemas, subprocess test harnesses and other unsupported
protocol features require explicit integration work rather than weakened controls.

Operator-approved predicates may be incomplete or wrong. A signature does not
prove business correctness. Real vendor account integration, cloud route performance,
native vision/click behavior, held-out model improvements and native DGM deployment
remain unverified. The prior intent score remains 94.5%; native DGM gates remain
closed. [Validation](VALIDATION.md) distinguishes old measurements from this release.
