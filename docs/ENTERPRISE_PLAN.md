# Deployment qualification — active work

Target: deploy Monkey into Linux terminals and Windows PowerShell, with explicit
model selection, MCP/API integrations and observed Windows 10/11 accessibility
actions. Preserve the standalone foreground interface and signed trace.

This is an implementation checklist, not a certification or a readiness claim.

Work resumed after the earlier pause. ENTERPRISE_BLOCKERS.md records the
owner-enrollment and native-session prerequisites. Dev8 checked startup bundles
installed and reopened on macOS ARM64 and Linux ARM64. Windows 10 passed five
file/state checks, while its full console run timed out. Windows 11 did not start.
All of those test sessions are now closed. Dev9 startup work has begun; use
RELEASE_READINESS.md and the newest dated VALIDATION.md entry for current results.
The full implementation and qualification scope below remains unfinished.

1. Portable packaging and upgrades: pinned dependencies, native command entry
   points, preserved private state, reviewable rollback, an offline wheelhouse.
2. Portable state: Windows user SIDs, native protected ACLs, reparse-point rejection,
   exclusive process ownership, crash recovery and publicly verifiable signatures.
   POSIX keeps descriptor-confined files and owner-only permissions.
3. Model selection: installed local defaults, explicit per-role provider/model
   rosters, immutable job recipes, bounded transport fallback and measured future
   selection. Model changes retain permissions and cannot bypass a refusal.
4. Execution: qualify process containment and cleanup on every advertised platform.
   The current macOS single-process verifier is insufficient for general builds.
   Container execution and dependency acquisition remain implementation work.
5. Enterprise configuration: managed connection setup and secret references,
   explicit API/MCP schemas, access policy, diagnostics and audit export. Validate
   actual integrations; do not advertise universal API/schema compatibility.
6. Windows UI: resolve the exact licensed Windows 10 and Windows 11 profiles
   through Perslis/GhostBridge. Use fresh accessibility observations and exact
   element identities, retain before/after evidence, respect native consent and
   close the owned session. Run only one guest or isolated session at a time.
   The inspected provider contract, new coordinator and missing transport are in
   WINDOWS_ACCESSIBILITY_PLAN.md; generic MCP connectivity alone does not satisfy it.
7. Acceptance: install and exercise the real CLI/REPL on Linux and both Windows
   generations. Include model outage, signed restart recovery, MCP/API reads and
   bounded actions, response loss, stale UI targets and cleanup. Host simulations
   or mocked platform flags do not prove native platform support.

September 14 evidence: dev8 passed 261 source tests (81.572 seconds), 16 focused
coordinator checks, an offline macOS install and the real CLI/HTTP-MCP fixture.
That fixture retained 16 synthetic Windows replies and a verified 513-entry
checkpoint, and confirmed that dev7 refuses version-4 Windows-bound state.
It did not execute Windows. The then-active Windows 10 guest was subsequently
recovered and shut down in the September 15 run; its owner released the session.
The source-tested boot-order candidate is not activated.
Owner enrollment, a supported provider transport and the independent native proof
issuer remain prerequisites. Exact reports are in VALIDATION.md.

Prior evidence: the dev7 development source suite passed 238 tests on macOS
(56.461 seconds, 0.7.0.dev7). General jobs now capture application Python source
hashes and stop new actions after a code change. Historical effects remain
reviewable. A version-3 state barrier prevents an older executable from ignoring
those runtime pins. Eight focused checks and 29 installed general-work/MCP/API
checks passed. Actual dev6/dev7 console qualification preserved legacy results,
rejected continuation without rewriting original limits, recorded unknown legacy
runtime provenance, verified signed exports and rejected a dev6 downgrade against
new pinned-job state. Dependencies, native toolchains and OS containment still
need their separate qualification. A preceding dev5 run failed during native startup; the
failures, diagnostic samples and successful rerun are retained. New checks cover
unchanged-operation stops, grounded local action history, bounded transport
retries, inner-timeout reporting, partial verifier evidence, native cleanup,
descriptor traversal, encoded research parameters and active-installation leases.
The earlier 0.7.0.dev2 suite passed 209 tests, including
real loopback HTTP create/read-back/sign-off and response-loss restart checks
through Monkey's own admission runtime. The tests assert that Kist and Captain
are not invoked on that path or the new default scoped project path. Earlier
pinned Kist integration tests also pass. Three additional package compatibility
checks passed, including macOS older-minimum-OS wheels and wrong-architecture rejection.
An earlier native Windows 10 open request was denied by GhostBridge consent, and
no guest remained at that checkpoint. A later open succeeded. The September 15
recovery run then exercised actual Windows ACL/file/state checks and closed that
guest. The default installed Monkey remains 0.6.0. A separate 0.7.0.dev6
installation from a fresh 35-package offline wheelhouse passed actual console
commands through project import, exact approval, source edit, pinned verifier, restart, sign-off,
signed export and tamper rejection. Its 24 installed MCP/API, native admission,
routing and verifier checks also passed. The preceding dev5 installed CLI failure
exposed copied-interpreter support that source-only tests had missed. Dev6 pins
the exact interpreter, framework launcher and required Python configuration file;
unrelated executables remain outside its grants. The successful installed fixture
is JM-622c2d2f3074, with 231 entries in its publicly verified signed export.
This candidate has not replaced the default installation.

Packaging now builds standard wheels and resolves complete, hash-pinned offline
bundles against each target's dependency markers. Wheel availability and metadata
closure are distinct from native OS qualification. Fresh 0.7.0.dev8 bundles for
macOS ARM64 and Linux x64/ARM64 Python 3.11 contain 35 packages each; Windows x64
Python 3.11 contains 36. All 45 application source files match the tested macOS
installation. Windows ARM64 Python 3.13 remains blocked by the previously checked missing
cryptography 50.0.1 native wheel. No downgrade or substitute guest was used.

The event feed now wakes after committed database changes and keeps replay as
a fallback. The latest fixture benchmark measured status p95 0.422 ms over
1,000 requests and event-to-UI-emission p95 182.44 ms over 13 events. Typed input
survived concurrent work; pause took effect after review. The earlier measurement
missed the event target at 265.635 ms and remains in the validation logs. These
measurements cover the application/PromptSession path, not physical screen paint.

Real local coding trials have produced source and tests, but have not yet
completed the autonomous work loop successfully. Recorded failures include
provider timeouts, repeated planning, zero-test discovery and requests for an
unavailable test dependency. The plan-loop and zero-test checks are fixed and
tested; no model performance or self-improvement success is claimed yet. The
separate installed-wheel coding trial JM-789facc053cd stopped NEEDS_INPUT after
six local-model calls and four tools, with a repeated write and model timeout.
The Granite ranges and Qwen whitespace trials also stalled and were stopped with
their records retained. Ollama metadata reads responded again in the resumed
session. A new installed Qwen trial retained a plan and directory listing but
stopped after timeouts, with no program produced. No successful general coding
qualification is claimed. See VALIDATION.md for exact trial IDs and boundaries.

See DEPLOYMENT.md for installation instructions and explicit platform limits.
WINDOWS_ACCESSIBILITY_PLAN.md records the inspected provider contracts and
transport gaps. Read live owner status before any future VM action; historical
session descriptions do not establish that a guest is still running.

The active general-agent and container implementation requirements remain in
GENERAL_AGENT_PLAN.md and CONTAINER_RUNNER_PLAN.md. Kist DGM readiness gates are
unchanged; no automatic self-deployment is qualified.
