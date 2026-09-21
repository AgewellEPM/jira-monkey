# Measured validation — Monkey development and installed releases

## Dev14 foreground environment ownership and responsive sealing — 2026-09-15

The source candidate is `0.7.0.dev14`. It adds asynchronous managed environment
setup, a shared ownership lease, inherited-pipe shutdown, signed guardian
evidence, captured-environment job startup and bounded recovery commands. The
default `Monkey` launcher still selects `0.6.0+dashboard.1`; these development
changes were installed separately. Production state and Ollama were not changed.

The final full source suite passed **311 tests in 69.486 seconds**. The installed
candidate passed **89 focused tests in 9.415 seconds**, covering the environment,
audit, container runner, response and general-agent runtimes, startup and web
dashboard. The two initial environment test failures were incomplete fixture
recipes (tool catalog, then supervisor hash); their failed logs are retained.

Real macOS qualification used an owned Colima profile, two virtual CPUs, 2 GiB
memory and a private configuration root. Fresh IsolatedTester session checks and
host process checks preceded each launch; no sessions overlapped. The initial
setup downloaded the pinned base, installed and retained a package inventory,
and passed an actual Python/Node probe. Its first qualification script failed
after setup because it incorrectly searched the terminal event feed for a VM
record stored in the audit journal. Cleanup succeeded. The corrected check
compares observed guest processes and ownership across repeated setup calls.

| Final native case | Observed result |
| --- | --- |
| Installed setup and reuse | Reused the same live guest and owner; native/backend reselection kept the exact image; `/quit` stopped the guest and released the lease. |
| Killed foreground | The installed CLI exited from `SIGKILL`; its independent guardian stopped the guest and left a valid signed cleanup receipt. |
| Restart after kill | The installed CLI reopened the saved state, started the captured environment, then shut down cleanly. |
| Installed general worker | A build request started its own captured environment, ran the real multiprocess generator/checker, imported and verified generated source, and preserved the captured backend after a global setting change. |

The worker's model decisions were **scripted fixtures**. Job `JM-cc3042773bb7`
finished at `AWAITING_REVIEW`, with result hash
`55a49573e87c6352e28a60d3c91feed5fe8e68b07369147d210078f9735f83d1`.
It was not signed off or published. This does not qualify actual local-model
task quality. Its 185 in-process dispatcher samples had a maximum of 22.033 ms;
these exclude input scheduling and rendering.

The longer setup exposed a real responsiveness defect: each `/builder status`
read verified and resealed the full history. The corrected setup run initially
measured 413.453–500.626 ms over 31 samples. Status reads now retain individually
signed receipts, and work completion verifies a consistent captured journal
prefix off the input loop. Existing tamper checks remain; new tests cover
concurrent signed appends and rejection of external writes during verification.

On the same 16 GiB macOS host and accumulated test journal, the final installed
CLI/JSON setup run measured **6.878–79.393 ms over 27 status samples**. The killed
foreground case measured 7.171–61.060 ms over 20 samples; restart measured
7.072–61.521 ms over 21 samples. These are newline-to-reply measurements in the
automated CLI, not native terminal rendering, browser visuals or a general
performance guarantee.

The installed lifecycle journal export contains 2,043 entries with SHA-256
`280f30651ff4ab0a74f7e1cacf7295af09baf8b0608e78011806c6cdb78a5fea`
and signer fingerprint
`fbd6e07148f7f5a2b9be44c119e3926ca9c35fb5d6ba6189d0b4ae163e0888f9`.
The worker export contains 408 entries with SHA-256
`9bf8f0ba719b7b339a331e362652ece9a90d76af38e705d2750329f4c1f5901c`
and fingerprint
`dd0d3974ce822e23262a1dd7f5064e36278c1f645887f53eb401553261d92668`.
Public verification receipts are retained with the evidence.

Evidence is under
`~/.local/share/jira-monkey-validation/0.7.0-dev14-managed-builder-20260915`.
The retained build image is
`sha256:9cf2af02263bf841bc78507f47bfff7d32c07a1c0c0691537d2c5c63547c2b8a`.
All native cases ended with no guest or virtualization helper remaining. See
[MANAGED_BUILDER.md](MANAGED_BUILDER.md) for the trusted bootstrap boundary.
Automatic host dependency installation, general dependency acquisition,
Linux/Windows management, live integrations, native UI, standalone evolution
and model outcome qualification remain open; public rollout is not qualified.

## Dev13 multiprocess builds and retained effects — 2026-09-15

The source candidate is `0.7.0.dev13`. It adds a pinned, offline Linux container
runner for general build work, with bounded descriptor-based source transfer,
trusted process/file/network tracing, resource controls, validated source imports,
and no-replay recovery. The normal `Monkey` launcher still selects the separate
`0.6.0+dashboard.1` runtime; no production state, model service or user app was
changed during this qualification. The development candidate was installed under
a private validation home and exercised through its actual `Monkey` entrypoint.

The full source suite passed **298 tests in 84.416 seconds**. The installed
candidate passed **83 selected tests in 13.978 seconds**, covering the new build
boundaries, general-agent runtime, exact startup controls, original response
workflow and dashboard. All **169 installed payload files and both launchers**
matched their manifest. An earlier targeted invocation had one loader error
because `test_agent_runtime` imports a sibling test by its bare module name;
the normal discovery invocation and installed tests directory resolved it. Its
failed log is retained, not counted as a passing run.

Actual qualification used one owned Colima/Linux ARM64 VM at a time, with no host
mounts, SSH-agent forwarding, public ports, context activation or browser runtime.
The builder image resolved an official Debian base digest and installed tools
through signed Debian repositories. The final image ID is
`sha256:c266d25e1c5536d692237665b930883cc9e409544d686baee3c5e5cfecc2732e`;
the exact package inventory and build log are retained. The first attempt stopped
before command execution because Docker reports added capabilities using `CAP_`
prefixes. Equivalent names are now normalized while retaining the exact allowed
capability set. The second attempt compiled C but could not execute its output:
Docker's tmpfs default denied execution. The private work mount now explicitly
permits execution; host sharing and all other limits remain unchanged.

The third run passed nine actual container cases. The fourth, using the frozen
installed payload, passed **ten**: C compilation/execution; Python-to-Node
subprocess generation; UID/capability/evidence/root/input/network boundaries;
detached-child timeout; output cap; symlink rejection; process cap; memory OOM;
a host driver killed after its container started; and the full REPL/agent build
and import path. Recovery inspected and removed the claimed objects without
restarting the command. All test containers/volumes were removed, the VM was
stopped, and guest/helper and IsolatedTester-session cleanup was checked.

The worker fixture scripted only model decisions. Real commands generated and
checked `generated.py`, which the host imported with its preimage and read-back.
The job kept its captured container recipe after `/builder native` changed the
global setting. It ended `AWAITING_REVIEW`, with no operator sign-off or external
publication. Twenty-eight in-process REPL status-dispatch samples had a maximum
17.491 ms during that work. This excludes input-arrival/scheduling delay and
physical terminal rendering; it is not the full responsiveness release gate.

The installed public audit verifier accepted the **1,047-entry** export. Its
SHA-256 is `209992882046c0bc56830955dcf63237af93ad5e307492c9a36785f7b1e72e7b`
and signer fingerprint is
`ed9e7a0563bf111640d31f3d82191d3cf97aa0367cc791b81988113ce504ea1e`.
Trace strings and sizes remain bounded; signatures establish retained-record
integrity, not exhaustive host attestation or model correctness.

Evidence is under
`~/.local/share/jira-monkey-validation/0.7.0-dev13-build-runner-20260915`.
The runner does not yet own automatic foreground VM/image provisioning or public
dependency acquisition. Real model task quality, independent improvement gates,
live business integrations, native UI and remaining platform qualification are
still open. See [BUILD_RUNNER.md](BUILD_RUNNER.md) for the current contract.

## Shared web and terminal workspace — 2026-09-15

The installed `Monkey` command now selects `0.6.0+dashboard.1`: the modern banana
terminal presentation plus a local web face for its existing 0.6.0 worker. Its
separate runtime is under `~/.local/share/jira-monkey-terminal/0.6.0+dashboard.1`.
The source development candidate is `0.7.0.dev12`. The launcher switch preserved
the original runtime and open sessions and did not open production state.
The activation receipt retains before/after hashes and rollback files; all 102
original payload files and all 109 selected payload files were checked.

The dashboard has a ticket workspace, actual counts, searchable/paged jobs,
completion and planned-date calendars, earlier journal pages, conversation,
evidence tabs and exact review/publication dialogs. Browser and terminal use
the same App instance. Browser requests have retained IDs and explicit operator
provenance. A request pins its conversational target across asynchronous calls.
External MCP clients keep their original restricted authority. Request-body,
host, origin and authentication checks were exercised over real loopback HTTP.

Ten new dashboard integration tests cover shared state, asynchronous controls,
target changes, stale/wrong-draft approvals, local calendar sign-off, uncertain
publication without duplicate writes, history, dates and listener/key lifecycle.
The first run exposed Uvicorn's bound-method ASGI auto-detection mismatch; it was
stopped and fixed with an explicit ASGI3 interface. The second run exposed a
reply formatter that treated action receipts as complete job records and a
socket reuse issue. Those were fixed; a test's assumed initial `/task` state
was also corrected. Failed run logs were retained. The source dashboard
run passed 10 tests in 3.041 seconds. The full source suite passed 289 tests in
105.048 seconds before the final destination-display, asset-trace, CSS and JSON
launcher refinements; the final dashboard tests cover the affected backend.
The final installed payload passed 69 dashboard, terminal navigation, runtime,
schedule, request and MCP/API checks in 11.335 seconds.

A final correction excludes cancelled/rejected work from planned-date lists and
normalizes the casing of terminal-only lifecycle commands. The affected suite
was rerun against both source and the installed payload: 10 tests passed in
3.226 seconds and 2.883 seconds respectively. Its targeted before/after hashes
are retained in `final-corrections.json`. No new browser appearance claim follows
from these backend checks.

The actual `/Users/lukekist/bin/Monkey --version` returned `0.6.0+dashboard.1`.
The installed command was then started as `Monkey --state <fresh-private-state>
--json dashboard`. An authenticated web request captured `JM-fb8c64a3dcd2`; the
same foreground terminal's `/status` reported that exact job and title. Replaying
the web request ID created no second job. Ten loopback status samples ranged
from 1.921 to 4.561 ms; this small empty-workspace sample did not include model
generation and is not a general performance claim. `/quit` returned exit code 0
and the listener was confirmed closed. Its 44-entry signed export has SHA-256
`c141fd008ca81691e36b11cd06d6431c0ca06056fc3915bc3d4463124e77b854`
and signer fingerprint
`a8fd6bf2605e3daa0b6e02423e2f80088be1a028585fa7d87567ffd4a48bdb04`;
the installed public verifier accepted it.

Native visual inspection was attempted through IsolatedTester with the already
installed NativeBrowser, which links the system WebKit framework and bundles
no browser runtime. Session `695bfa31` launched PID 5493 on a 1600×1100 isolated
display but placed no window. `session_frame` returned: “Capture failed: No
capturable window found for PID 5493 yet.” No native keystrokes were attempted.
The session was stopped, the owned fixture dashboard was shut down, and no test
app, VM or isolated session remained. No prohibited browser was used. Native
appearance and keyboard usability remain unverified. The earlier TinkyVision
Terminal deny-list blocker also remains.

Evidence is under
`~/.local/share/jira-monkey-validation/0.6.0-dashboard.1-20260915`; earlier staging
and failed test logs are retained under `0.6.0-terminal.1-20260915`. This is a
local interface refresh, not a public or multi-user deployment certificate.
See [WEB_DASHBOARD.md](WEB_DASHBOARD.md) for commands and access boundaries.

## Dev11 local-model response evidence — 2026-09-15

An actual installed dev10 coding trial, `JM-391fb639ab9b`, stopped NEEDS_INPUT
after five model calls, one directory listing and two bounded transport retries.
It produced no verified program in 298.203 seconds. Its 1,143 recorded status
requests had p95 21.681 ms on the shared Mac. The 1,233-entry signed export
verified; SHA-256 is
`2719df345a68c781804daec032f3c1e18e3711a1269863e09a3b08fa95aeeb94`.
The 60-second request limit left no partial generation evidence for the timeouts.

Dev11 receives bounded Ollama chat frames and records signed progress and
settlement receipts. The status rail distinguishes waiting from provisional
output. Only a fully settled, completed and schema-valid response reaches the
worker. Cancellation, malformed output, late frames and absent completion cannot
create tool actions. Original call/retry/time limits remain enforced. Progress
records contain hashes/counts, not raw provisional content or intermediate thinking.

All 279 source tests passed in 123.490 seconds. Nine new asynchronous stream
tests passed separately in 0.460 seconds and against the installed package in
0.681 seconds. They include actual socket framing, arbitrary byte splits/Unicode,
signed progress before status, cancellation before a proposed file write,
disconnects, absolute deadlines during continued output, limits and rejected
fallback. The first focused run failed because the fixture placed thinking text
outside `message`; the corrected fixture uses Ollama's documented shape. The
failed log is retained. No runtime permission or model refusal policy was relaxed.

The separately built 35-package macOS ARM64 bundle passed checked installation
and actual CLI/signed-audit qualification. The initial launcher invocation used
two mutually exclusive flags and correctly refused before installation; the
corrected `--qualify` invocation includes installation and passed.

The installed coding qualifier now runs the authored unittest module and fixed
arithmetic cases withheld from model context before declaring its fixture passed.
Three actual native checker fixtures proved this gate: addition passed; subtraction
with a misleading passing self-test failed; addition with an empty self-test module
also failed. Their 48-entry signed export verified, SHA-256
`e4cd69bccd3a792369b7993ee4d7038b8ac2ac399d8bacd69ce936c297820b34`.
This is an outcome-check fixture, not adversarial or independent attestation.

The installed foreground REPL, authenticated loopback MCP/API, 15-tool discovery,
capture/recall, approval-authority rejection, hidden credential fixture and
shutdown cleanup passed. The second gateway run used the bundle's own interpreter
and dependencies; the first used the development interpreter with the installed
payload and is retained separately. All 49 application files matched the manifest.

The actual dev11 local-worker trial `JM-eb38cb694040` used the pinned installed
`jira-monkey-worker:latest` model and the configured 120-second request deadline,
within an original 600-second job budget. It created `calc.py`, but stopped
NEEDS_INPUT after five calls, one file operation and two retries. Three later
streams received 1,034, 1,020 and 1,151 provisional content bytes respectively
without a completion frame before their deadlines. No test file, executed check,
finished result or operator acceptance was recorded. The first planning response
started arriving at 41.840 seconds and completed at 102.654 seconds. This proves
output was arriving during the recorded requests; it is not a speed improvement
comparison with the dev10 trial's different deadline.

The run took 547.400 seconds. Its 2,094 status samples had p95 23.582 ms. A host
sample reported 9% free memory and other active workloads; model performance here
is measured on that shared host. The 2,314-entry exported checkpoint verified in
the fixture and again through the installed CLI, SHA-256
`c76e39369a18d81fa2f1579b8960cabd8b8c094aa7f6edfa0cc38936602d1bd7`.
Reopening the state through the installed CLI preserved NEEDS_INPUT, five consumed
calls and idle execution. The general coding quality gate remains unmet.

Luke subsequently confirmed that the styling request concerns Monkey's terminal
interface. Dev11 retains that banana-themed terminal layout.

Reports and failed attempts are retained under
`~/.local/share/jira-monkey-validation/0.7.0-dev11-stream-observability-20260915`.
The full release gates remain open. Native visual verification is still blocked;
the default `Monkey` command remains 0.6.0.

## Dev10 modern terminal preview — 2026-09-15

The separately installed 0.7.0.dev10 preview adds banana-gold accents, charcoal
panels, clearer headings and selection, a two-column workspace at wider sizes,
compact navigation at narrower sizes, and responsive evidence tabs and calendar
cells. Existing commands, ticket navigation and sign-off rules remain in place.
The request was interpreted as styling Monkey's terminal home screen; an optional
question about a separate web page had not received a response.

Six browser tests passed in 0.451 seconds and the original 36 runtime acceptance
tests passed in 3.879 seconds. A separate formatted-text check covered 72 layouts
across widths of 24, 42, 78 and 110 cells, all seven workspace sections and all
eleven evidence tabs. It checked cell bounds, style resolution and absence of raw
escape sequences. These checks do not establish physical font or display quality.
The complete 270-test suite and startup benchmark in the next section belong to dev9;
they were not repeated or represented as dev10 measurements.

A fresh 35-package macOS ARM64 bundle passed checked installation, actual CLI
startup, signed export verification, altered-export rejection and reopening.
Its fixture job `JM-2f8e46f4ae5d` retained 21 signed entries, export SHA-256
`8a372acae6b287be3c7e64681701aa1be276a81dbf7c070c50ae3816d7b5e82c`.
The installed foreground REPL passed authenticated loopback HTTP/MCP checks,
discovery of 15 tools, capture/recall, forbidden approval rejection and cleanup.
An installed PromptSession fixture passed hidden credential entry and exclusion
from history/events/audit using a synthetic token. All 48 application files
matched the selected bundle manifest. No live business-service operation, model
generation or Windows/Linux validation was performed for this styling change.

`Monkey Preview.command` starts this candidate with separate state and verifies
its installed files. The normal `Monkey` command remains 0.6.0. Native Terminal
visual/input verification is still blocked by TinkyVision's sensitive-app
deny-list; no alternative automation path was used. This is an inspectable
development preview, not a qualified worldwide release.

Evidence and the preview launcher are under
`~/.local/share/jira-monkey-validation/0.7.0-dev10-modern-terminal-20260915`.

## Dev9 startup and installed release checks — 2026-09-15

The unchanged dev8 source baseline passed 266 tests in 107.309 seconds. Dev9
then passed 270 tests in 78.384 seconds after one failed broad run was investigated.
The failed run lacked a memory-limit event. An isolated diagnostic observed the
watchdog stop a child at 235,290,624 resident bytes; it did not establish the exact
cause of the earlier failure. The fixture now commits its allocated pages and
starts its enforcement deadline after connection negotiation. Runtime receipts
record the exact limit, elapsed time and distinct stop reason. Twenty security
checks passed in 6.190 seconds before the final broad run. The failed log remains.

Exact CLI commands no longer load the MCP SDK, server or terminal UI libraries.
SDK initialization for an actual connection runs off the prompt event loop.
Four fresh-process tests passed against source (2.030 seconds) and the installed
candidate (1.596 seconds), including unavailable optional libraries, denied network
attempts, pause/cancel, signed history, public verification and status during a
delayed SDK import. Completion rules remain shared with the ticket browser.

Five samples per command measured complete fresh-process startup on this Apple
M4 / 16 GiB / macOS 26.0 / Python 3.11.14 host. Each used private empty state and
an empty Python bytecode-cache prefix with writes disabled; OS file caches were
not flushed. Median milliseconds, dev8 to installed dev9: version 1001.989 to
235.182; help 1040.273 to 248.729; capabilities 1286.769 to 547.986; status
1295.913 to 481.696. These are not Windows measurements or physical paint latency.

A fresh 35-package macOS ARM64 r2 bundle passed checked installation, actual CLI
commands, signed audit export, tamper rejection and reopening. Its basic fixture
retained 21 signed entries. The actual installed scoped-project fixture changed
the addition function, passed its pinned verifier, restarted, signed off and
verified a 233-entry export. The installed REPL also passed real authenticated
HTTP/MCP startup, discovery of all 15 exposed tools, capture/recall, forbidden
approval rejection and cleanup. An installed PromptSession fixture checked hidden
Asana credential entry and history/event/audit exclusion using a synthetic token.
All 48 packaged application files matched the selected bundle manifest. No real
business-service operation, model generation or Windows/Linux run occurred here.

Native Terminal keyboard testing was attempted through TinkyVision and denied:
`com.apple.Terminal` is on its sensitive-app deny-list. No new window was opened
and no alternate automation path was used. Physical terminal verification remains
open. The default installed command remains 0.6.0; dev9 is a separate candidate.

Evidence is in
`~/.local/share/jira-monkey-validation/0.7.0-dev9-release-startup-20260915`.
RELEASE_READINESS.md preserves the full worldwide objective and outstanding gates.

## Native recovery closeout — 2026-09-15

The r7 Linux ARM64 bundle passed a fresh checked installation, installed console
commands, 19-entry signed export, altered-export rejection and checked reopening.
The environment was Linux 6.8.0-117-generic / glibc 2.41 / Python 3.11.16, inside
an unprivileged container in the single owned Colima VM. The container had no
network or host directory mounts and a read-only root. Its private installation
workspace explicitly allowed execution. Installation plus audit checks took
13.013 seconds; checked reopening took 1.609 seconds. These are CLI/container
results, not physical terminal UI or general code-runner qualification.

The Linux receipt recorded 3,049 runtime files. Its received public export was
verified again by the installed macOS candidate using fingerprint
`a000d6144389fd3fca8f792aeae4d545dbf8dfc301b3571fdd6bdb96cca727cf`;
a changed transferred copy was rejected. Earlier transfer and setup fixture
failures remain recorded. Their cleanup and the successful run's cleanup all
stopped the container and VM; no overlapping guest was launched.

Windows 10 ultimately completed in-guest shutdown after another recovery reboot
and Windows updates. The earlier hard reset is not claimed as a clean shutdown.
Perslis observed QMP `shutdown`, finalized power-off and GhostBridge reported
CLOSED with no slot held. Two attempts to open the registered Windows 11 target
returned `portal-status-uncertain`, then reconciled to CLOSED without a QEMU
guest starting. Windows 11 native execution remains unqualified.

Ollama repeatedly restarted through another launch agent while testing. A
temporary explicit HTTP 503 response held its localhost port during the final
Linux run, with no model process present. That helper and the artifact-transfer
server are stopped. The original `com.kist.ollama-local` wrapper and unchanged
launch file are restored; the API reports 0.32.3 and both Monkey model tags.
Final checks found no VM, isolated test session or owned temporary helper.

The report, failed attempts, public exports, cleanup receipts and SHA-256/Ed25519
evidence seal are under
`~/.local/share/jira-monkey-validation/0.7.0-dev8-native-recovery-20260915`.
The seal establishes local artifact integrity, not independent native-provider
authority or an exhaustive host syscall trace. No business-service write,
model generation or provider enrollment was performed in these native checks.

## Windows 10 recovery and native setup — 2026-09-15

Luke authorized the close retry and subsequent recovery. The close adapter
required in-guest shutdown. The existing owned Windows 10 session recovered
through the configured Perslis tools: its BIOS menu showed the shared USB disk
first; selecting the 64 GiB ATA OS disk reached the desktop and PowerShell. No
second guest launched. This confirms the practical boot-order cause; the
separately built Perslis configuration fix is not activated.

PowerShell reported Windows 10 build 19045. A private Python 3.11.9 x64 setup
completed with exit code 0. Before execution, Windows reported a valid Python
Software Foundation Authenticode signature and the expected installer SHA-256
`5ee42c4eee1e6b4464bb23722f90b45303f79442df63083f05322f1785f5fdde`.
This is a test prerequisite, not enterprise qualification of that older patch.

Native testing found two launcher issues: filtered child environments omitted
Windows home variables, and the integrity reader compared CPython's synthetic
path-based executable bits with handle metadata. The latter rejected the
unchanged `python.exe`. A native diagnostic retained all 89 reads and showed
identical identity, size and timestamps, with modes 33279 and 33206. The fix
normalizes only those Windows filename-derived execute bits; hashes, readonly
and type bits, file identity, timestamps and link counts remain checked. This
distinction is documented in CPython v3.11.9's `Modules/posixmodule.c` and
`Python/fileutils.c`.

The next attempt passed those checks but reached the 180-second `venv` timeout.
Its incomplete directory and log remain retained. A native process query found
no matching Python installer processes afterward. Setup now allows 600 seconds
per step, records that bound, prints the actual stage and addresses the installer
process tree before killing a timed-out parent. Ten focused tests passed in
1.952 seconds, including a real POSIX child that could not write after timeout.
Windows process-tree cleanup still requires native qualification.

The Windows r4 attempt installed all 36 pinned packages, passed `pip check` and
printed Monkey `0.7.0.dev8`, but exceeded the outer 1,800-second limit before an
installation receipt existed. It remains an incomplete installation. After
Luke authorized temporarily stopping Ollama, the installed executable returned
its version with exit 0 in 88.391 seconds. The console fixture subsequently
timed out on `caps` at 90 seconds. It did not reach a signed audit export.

The initial Ollama stop was verified at 04:22 UTC, but a later process check
found that the service had restarted at 04:24. The native follow-up timings are
therefore **not** measurements with Ollama off. At 04:49 its launchd entry was
temporarily disabled and the service stopped again, with the original enabled
setting retained for restoration after VM testing.

Five native Windows checks passed in 43.913 seconds against that installed
package: owner-only ACL enforcement, hard-link and parent-escape rejection,
NTFS junction rejection, exclusion of a second state owner, and signed state
reopening with the same identity. These are actual Windows results, separate
from the failed whole-console qualification. Long PowerShell input also stalled
and dropped characters. The live Monkey REPL is not qualified on this guest.

That timeout exposed a fixture-cleanup gap: killing a Windows console launcher
alone can leave its Python child. The qualification helper now retains the
parent while addressing its process tree, and records timeout output explicitly.
Twelve focused launcher tests passed in 4.748 seconds, including real POSIX
installer and console child cleanup. Native Windows timeout cleanup is still
unqualified. Windows `USERNAME` now survives the filtered fixture environment.

Fresh `bundles-0.7.0-dev8-native-r7` artifacts contain these helper changes. The
macOS checked installation and console/audit fixture passed. An explicit Windows
11 ARM64/x64-Python compatibility target checks the Windows kernel architecture,
PE executable architecture and build; it does not claim native ARM64 Python.
Application payloads and the production 0.6.0 command remain unchanged.

Receipts, failed attempts, diagnostics, source-test logs, archive hashes and
transfer records are retained under
`~/.local/share/jira-monkey-validation/0.7.0-dev8-native-recovery-20260915`.
Guest timestamps have a clock offset; host receipt timestamps are separate.
These reports are observed execution evidence, not independent native-provider
attestation. No model generation, business write or provider enrollment occurred
in these Windows setup checks.

## 0.7.0.dev8 Windows coordinator — 2026-09-14 late evening

The complete source suite passed **261 tests in 81.572 seconds**. The 16 focused
Windows coordinator cases passed in 6.176 seconds. They exercise exact target and
workflow session binding, dropped acknowledgements, denied closes, retained error
responses, stale state, connection changes, no replay on restart, API authority
limits, independent status linking, cleanup after policy disablement and the
atomic version-4 compatibility floor. Two initial test-harness errors (an incorrect
helper name and recursive mocked clock) were fixed; that failed log is retained.

The new macOS ARM64 bundle installed offline and passed the checked startup
console/state/audit fixture. A concurrent checked version invocation was refused
while qualification held its launch lock. After qualification completed, the
sequential checked command returned 0.7.0.dev8. No launch lock was bypassed.

`scripts/qualify_windows_coordinator.py` used the actual installed `Monkey`
command and a real loopback streamable-HTTP MCP fixture. Windows replies were
explicitly synthetic. Job `JM-8eefcceaf4f7` recorded 16 calls across fresh CLI
processes: catalog, one open, observation/status, readiness, workflow start,
steps, provider/cleanup ticks, terminal receipt and one close. The reports
reconstructed after each command. The installed dev7 executable refused this
version-4 state. The 513-entry checkpoint verified with public-key fingerprint
`c11176dbfab315ec0ba86baa8802a1fc014b8fc3186372adcf03895cb8685e32`;
its exported file SHA-256 is
`420942e2b7940796818b276279107acbec4b6bf6a3461a4cffb1cbb3f9dd1e8c`.
The fixture helper was terminated and its exit confirmed. This does not claim
Windows boot, PowerShell interaction, native provider verification or cleanup of
the separately running real guest.

Four fresh bundles contain 35 packages each for macOS ARM64 and Linux x64/ARM64,
and 36 for Windows x64, all Python 3.11. All **45 application files** match source,
each bundle and the installed macOS copy. Default production remains 0.6.0.

Aggregate report:
`~/.local/share/jira-monkey-validation/0.7.0-dev8-qualification.json`.
The exact installed fixture commands, ledger, signed export and cleanup result
are in `0.7.0-dev8-installed-windows-coordinator/result.json` under the same root.

Read-only final checks still found the real Windows 10 session
`c76738cd-6bc0-4d15-b692-616b4290b610` HOT, QEMU PID 63707. Its denied native close
was not retried. Root trust enrollment remains absent, and no other guest or
isolated test was launched. Supported GhostBridge transport, the independent
native proof issuer, owner enrollment and actual Windows 10/11/Linux execution
remain unfinished requirements; the coordinator fixture does not replace them.

## Windows boot-order candidate — 2026-09-14 late evening

Read-only inspection of the owned Windows 10 guest found the shared USB data
disk had `bootindex=20`, while the OS disk had no device boot priority and the
command also supplied `-boot order=c`. QEMU documents mixing `bootindex` with
`-boot order/once` as undefined behavior:
https://www.qemu.org/docs/master/system/bootindex.html.
This is a confirmed argument defect; it is not yet a proven explanation of the
observed boot stall.

The installed Perslis backend's SHA-256 matched the release build in
`~/perslis-dos-snake`. A narrow source change removes the USB priority from its
Windows 7/8/10 BIOS profiles, preserving the OS disk at IDE index 0 and existing
disk-first or one-time installer boot selection. Other local edits were retained;
the exact incremental patch and before/after snapshots are saved separately.

The new regression tests first failed with 24 assertions across the 12 affected
shared-disk cases. After the fix, eight focused tests passed in 0.013 seconds,
including the 24-case boot/media matrix and the existing Windows 11 policy check.
The release candidate built in 24.17 seconds. Its actual CLI passed executable
identity verification and the quarantine diagnostic: an authenticated GhostBridge
lease is still required. Its SHA-256 is
`bbeb042fe7ff66285352015d525ddce9cc8e468f7cac97180510e9f96d8dbfc7`.

This candidate was not activated. The immutable installed Perslis release and
Monkey's default 0.6.0 command remain unchanged. Fresh GhostBridge OCR at
2026-09-15 01:03:01 UTC still showed SeaBIOS / "Booting from Hard Disk...".
The owned Windows 10 session remained HOT, PID 63707, after its earlier denied
close. No close was retried and no other guest was launched. A permitted cleanup,
candidate activation and native restart are needed before claiming the stall is
fixed; Windows PowerShell, Windows 11 and Linux remain unqualified.

Evidence and artifact hashes:
`~/.local/share/jira-monkey-validation/0.7.0-dev7-win10-boot-order/result.json`.

## Checked bundle startup — 2026-09-14 evening

The dev7 application payload is unchanged. Seven startup-integrity tests passed
in 0.096 seconds, covering target/interpreter mismatch, changed artifacts,
unlisted packages, requirement directives, escaping paths, retained failed
installation and rejection of poisoned cached code. The three existing
package-metadata checks also passed.

Fresh `bundles-0.7.0-dev7-startup-r3` bundles contain 35 packages for macOS ARM64
and Linux x64/ARM64 and 36 for Windows x64. Each includes a checked one-command
launcher and console qualification helper. Only macOS was installed: the actual
launcher created its copied environment, installed offline, passed `pip check`,
ran Monkey 0.7.0.dev7, passed the console/state/audit fixture, and reopened for
`status` successfully. The Windows bundle was refused on macOS before creating
an installation. This is neither a PowerShell nor a Linux execution result.

The earlier startup bundles are retained. The first qualification helper created
633 bytecode files, and the next launch refused the changed runtime. Revision 2
disabled qualification bytecode writes and passed a subsequent launch. Direct
console use still created ordinary cache files, so revision 3 excludes default
cache files from its source/binary inventory and uses a fresh empty private Python
cache prefix on every checked launch. Cache writes remain disabled. A regression
case proved that Python normally consumed a timestamp-matching poisoned cache,
while the checked launch used the correct source. The actual installed bundle
also returned the correct version with an injected cache marker, then restored
that test cache. Qualification, direct console use and checked reopening all
passed; no temporary cache directory remained.

Evidence: `~/.local/share/jira-monkey-validation/0.7.0-dev7-startup-r3-qualification.json`.
It includes bundle hashes, the installation receipt, actual console results and
the signed-export fingerprint. No production installation, native UI session,
provider enrollment or business service was changed.

Ollama's metadata endpoints responded again during this session. A new installed
`qwen2.5-coder:7b` coding trial, `JM-03f159b1a461`, recorded a plan and a directory
listing, then stopped `NEEDS_INPUT` after request timeouts. It consumed five
model attempts and one tool call in 262.096 seconds, retaining its original
16-call/32-tool/600-second limits. It produced no program and was not signed off.
During the run, 1,032 recorded status requests had p95 3.855 ms on the shared
macOS M4 host. This measures application command responsiveness, not screen paint
or model quality. Its 1,122-entry signed checkpoint verified; exact evidence is
in `0.7.0-dev7-live-installed-qwen-startup/result.json` under the validation root.
The test's Qwen model was unloaded afterward; the unrelated Mistral model and
the preexisting Ollama service were left running.

The separate installed ticket worker then completed triage, drafting and review
using the captured `jira-monkey-worker:latest` digest
`ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b`.
Job `JM-1ed5de854d91` used three provider calls and paused after review exactly as
requested. Its draft remained unpublished and unapproved; review returned PASS.
The run took 123.422 seconds. Its 474 status samples had p95 18.076 ms on the
shared Mac. Its 531-entry exported checkpoint verified with fingerprint
`db079901387ccb62beb32864c81a9a34b91b9d958d0f6ef8cd99b23d7c55c6c5`.
The complete report is `0.7.0-dev7-live-installed-worker-startup-r2/result.json`.
The first harness attempt correctly failed before any provider call because it
marked a live-adapter job as a scripted demo; that failed job and signed export
remain retained. The corrected harness creates a new local job with live adapters
and explicitly records that its input is synthetic.

After the unrelated Windows 95 guest closed independently, GhostBridge launched
the exact Windows 10 guest. The open response was UNCERTAIN with an adapter
exception; a status read reconciled it to HOT without a second launch. Two fresh
OCR observations still showed SeaBIOS / "Booting from Hard Disk...". No Windows
desktop, PowerShell or Monkey execution was reached. The session is
`c76738cd-6bc0-4d15-b692-616b4290b610`; its QEMU PID was 63707. The native close
request was denied, and a subsequent status read still reported HOT with the VM
slot held. Cleanup is pending native consent, and no further guest may launch.
The exact MCP receipts are retained in `0.7.0-dev7-win10-native-session.json`.

## 0.7.0.dev7 development candidate — 2026-09-14

The complete source suite passed **238 tests in 56.461 seconds**
(`0.7.0-dev7-full-tests-r2.log`). Eight focused general-runtime checks passed in
0.614 seconds. The prior 237-test run also passed, before the additional atomic
compatibility-floor check was added. **29 tests passed in 3.838 seconds** against
the separately installed dev7 package: general work, runtime upgrades and MCP/API
fixtures. Its offline installation and `pip check` passed. Production remains 0.6.0.

New general jobs capture Monkey's application Python source hashes. Those hashes
are checked before inference and tool dispatch, and the result binds the original
scope and runtime. An upgrade or source change stops new actions under the old
scope. Delayed model proposals remain recorded without dispatch; already observed
effects remain retained when code changes before their return. Historical sign-off
and interrupted-operation resolution record execution/review provenance without
authorizing replay. Older unpinned jobs stay inspectable, with unknown execution
runtime reported explicitly; continuation does not give them a new runtime.

Capturing the first pinned general job atomically raises SQLite `user_version` to
3. A failed capture commits neither the job nor that compatibility floor. This
prevents older executables from ignoring the new runtime field; it is not an
automatic state rollback. See DEPLOYMENT.md for whole-state backup guidance.

`scripts/qualify_agent_upgrade.py` used actual installed dev6 and dev7 interpreters
and `Monkey` console entry points. It created source, ran fixed arithmetic checks,
retained a completed and a stopped job, reopened them in dev7, rejected continuation
without resetting original limits, and accepted the inspected legacy result with
its runtime explicitly unknown. It then created pinned dev7 work and demonstrated
that the dev6 executable refused its version-3 state. Both exported traces verified
with their recorded public-key fingerprints. The model decisions were scripted
fixtures; no live inference, Windows action or business-service effect is claimed.

The retained result is
`~/.local/share/jira-monkey-validation/0.7.0-dev7-agent-upgrade/result.json`.
The legacy completed job `JM-9c9e50372337` has a 113-entry exported trace, SHA-256
`39d3165c8792cf3b2cb67902dea6901d536c8f2217fd43815d340804322296c7`.
The fresh completed job `JM-8fbeedeb11fb` has a 107-entry exported trace, SHA-256
`e57152a4c1e9a2b6a021bf400cb303dc3db1eb03dd7686e760de32ffe5b2dfa1`.
Exact fingerprints and original export paths are in that report.

Fresh dev7 offline bundles contain 35 packages for macOS ARM64 and Linux x64/ARM64,
and 36 for Windows x64. All 44 application files match across the tested source,
each bundle and the macOS installation. Only macOS was installed and exercised.
The aggregate report is
`~/.local/share/jira-monkey-validation/0.7.0-dev7-qualification.json`.

Read-only GhostBridge checks confirmed distinct Windows 10/11 enrollment and an
independently running Windows 95 session with no fresh desktop observation. No
Monkey guest or isolated test session was launched. The prior Windows 10 consent
denial remains in force. The provider's signed-workflow contract and Monkey's
missing coordinator/transport are documented in WINDOWS_ACCESSIBILITY_PLAN.md.
Linux/Windows native execution, general build containment, live local-model quality,
production integrations and self-improvement promotion remain unfinished.

## 0.7.0.dev6 development candidate — 2026-09-14

The complete source suite passed **230 tests in 55.582 seconds** on the reference
Mac (`0.7.0-dev6-full-tests-r2.log`). A fresh 35-package offline wheelhouse installed
into a separate virtual environment with a copied Python interpreter. Its actual
`Monkey` executable reports `0.7.0.dev6`; offline installation and `pip check`
passed. The default `~/bin/Monkey` still reports `0.6.0`.

The preceding dev5 installed CLI check failed when project attachment rejected
the copied virtual-environment interpreter. Accepting the exact current
interpreter then exposed a denied read of its `pyvenv.cfg` during native startup.
Dev6 captures hashes for that interpreter, its required framework launcher and
the exact configuration file. It grants the configuration file read access only;
unrelated executables beside Python remain unavailable. Runtime changes block
dispatch, while historical review checks the originally recorded runtime files.
The eight project-admission tests also passed using the actual copied interpreter
(`0.7.0-dev6-copied-interpreter-tests-r2.log`, 1.752 seconds). Failed qualification
and first-run logs remain available rather than being overwritten.

The fresh installed CLI passed import, scoped project attachment, planning from
an inspected fixture proposal, exact authorization, source edit, pinned sandboxed
verification, restart, sign-off, audit export, public verification and tamper
rejection. This is application execution evidence; no model generated the plan.
The fixture job is `JM-622c2d2f3074`, and its result is retained at
`~/.local/share/jira-monkey-validation/0.7.0-dev6-installed-cli/result.json`.
Its exported journal contains **231 entries**, with SHA-256
`edb0587f764022721771ffb1a744e65ca39b190e6785b71ca4f41c1596b76b22`
and signer fingerprint
`f695761d53af0071ee8fdc75edefad1d3e00eb4754701cbc80d537ebd7acdd71`.

An additional **24 tests passed in 6.314 seconds** against the installed package:
five MCP/API cases, six native admission cases, eleven routing cases and two
native verifier cases. These include traced transport fallback within original
budgets, refusal/permission boundaries, signed measured-routing fixtures, retained
timeout output and process cleanup. They do not establish real-model performance,
production service compatibility or physical Terminal rendering.

All 44 application payload files matched between the tested source, bundle and
installed package. The combined report, with test-log hashes and export references,
is `~/.local/share/jira-monkey-validation/0.7.0-dev6-qualification.json`. A final
read-only Ollama probe timed out on `/api/version`, `/api/ps` and `/api/tags` with
a four-second timeout per request. The provider was not restarted again.

Real-model coding, repeated-task learning, DGM promotion, native Linux/Windows
execution and Windows accessibility remain unqualified. The existing Ollama
catalog problem and Windows native-consent denial remain recorded below. No
production installation, model roster or business account was changed by this
qualification.

## 0.7.0.dev5 development source — 2026-09-14

The complete source suite passed **229 tests in 58.487 seconds** on the reference
Mac. The earlier run failed 16 cases and remains in
`~/.local/share/jira-monkey-validation/0.7.0-dev5-full-tests.log`; the passing run
is `0.7.0-dev5-full-tests-r2.log`. No default installation was upgraded.

This revision adds bounded infrastructure retries inside the original general
work budget, committed action/result turns for local JSON inference, detection of
unchanged directory operations, and a stop after three unchanged source operations.
Only actual bound decisions become assistant history. Structured output remains
subject to the application’s existing validation; no native tool authority or
permission change is granted to a model. Other provider adapters retain their
existing structured evidence packets. There is no measured coding-performance
improvement claim yet.

POSIX project traversal now keeps directory descriptors, checks root identity,
rejects linked ancestors and bounds directory-only scans. Research rejects encoded
credential parameter names before network access and records failed/cancelled
reads. The installer holds the state ownership lease before replacing a payload,
so an active foreground application blocks an upgrade.

Native startup failures exposed macOS account and timezone notification waits
before Python user code. The sanitized child environment now supplies a UID-bound
CoreFoundation encoding and `TZ=UTC0`. Apple's libc distinguishes a POSIX timezone
rule from a zoneinfo filename; the former avoids the associated file notification.
See [Apple's CoreFoundation launch example](https://github.com/apple-oss-distributions/webdavfs/blob/main/WebDAVPlugin/WebDAV_Mount.c)
and [libc timezone implementation](https://github.com/apple-oss-distributions/Libc/blob/main/stdtime/FreeBSD/localtime.c).
No sandbox permission was widened. Actual native tests retain the host-read,
project-write, network and fork restrictions, and verify timeout cleanup. Timed-out
checkers retain partial output and its hash; an inner tool timeout no longer
masquerades as exhaustion of the original job deadline.

Two additional real-model trials were unsuccessful and are retained privately:
`0.7.0-dev3-granite-ranges` (`JM-91ee52131a71`, nine model calls/four tools) repeated
unchanged source writes; `0.7.0-dev4-qwen-text` (`JM-5ec415d5da64`, twenty model
calls/eighteen tools) repeated directory creation without source or verification.
Both were interrupted after the observed stalls. Their original budgets and
evidence were preserved. Neither demonstrates a completed autonomous program.

Ollama's installed-model catalog subsequently timed out. Its existing idle local
launcher was restarted once without configuration changes; the catalog remained
unresponsive. The sampled provider was waiting in a file-open syscall. The
manifest inspected for `qwen2.5-coder:7b` remained readable and matched its captured
digest. The provider problem is unresolved; no weights were downloaded, no model
refusal was bypassed, and no successful inference benchmark is claimed for dev5.

Linux/Windows execution, Windows accessibility and self-improvement remain
unqualified. The Windows 10 native-consent denial is still in force; no alternate
guest or automation tool was used to bypass it.

## 0.7.0.dev2 development candidate — 2026-09-14

The complete source suite passed **209 tests in 67.122 seconds** on the reference
Mac. Three additional offline-package compatibility checks passed. New native
admission tests exercise real project writes and sandboxed verification without
Kist, preserved exact approval, changed-source/receipt rejection, zero-test
detection, pause boundaries, interrupted effects and review after a runtime update.
The prior Kist integration tests still exercise their explicitly selected adapter.

An offline wheelhouse installed into a separate environment without package-index
access. Its real `Monkey` executable passed import, scoped project attachment,
planning from an inspected fixture proposal, authorization, source edit, fixed
verifier execution, restart, sign-off, audit export and rejection of a tampered
export. No model generated this proposal; this demonstrates application execution.
The installed MCP/API suite passed five tests and its native admission suite
passed six, including a lost-response restart and resolution after an upgrade.

The retained installed CLI result is
`~/.local/share/jira-monkey-validation/0.7.0-dev2-installed-cli-r2/result.json`.
Its fixture job is `JM-74d0f9978998`. The exported journal contains 205 entries;
SHA-256 `76de724a7aa05b7c4f97d8ad492eb1d782cfab5a07ea3448107a4f21dfe7c06c`,
signer fingerprint
`0551d5720b9051cc4e1d7c2ee1cabb7aff32d259b3d69f1411a4123c53981508`.

The updated event-feed benchmark measured status p95 **0.422 ms** over 1,000
requests and event-to-UI-emission p95 **182.44 ms** over 13 events, with typed input
preserved and pause after review. The earlier failed benchmark (265.635 ms event
p95) is retained. The benchmark uses real persistence and PromptSession with fake
models and in-memory input/output; it does not measure physical display paint.

Fresh bundles resolve for macOS ARM64, Linux x64/ARM64 and Windows x64. Only the
macOS candidate was installed and exercised. Native Windows ARM64 packaging is
blocked by the pinned cryptography release's missing wheel; Windows 10 native
session consent was denied by GhostBridge. No substitute guest was launched.

The installed local-model coding trial `JM-789facc053cd` used actual Ollama and
actual file tools. It stopped NEEDS_INPUT after six calls/four tools, repeating a
source write before a model timeout. It did not produce a verified completed
program. General model performance, Linux/Windows behavior, general build
containment and self-improvement remain unqualified. The default `~/bin/Monkey`
still reports 0.6.0; this candidate has not replaced it.

## Earlier installed release — 0.6.0

Reference host: Apple M4, 16 GiB RAM, macOS 26.0 (25A354), Python 3.11.14,
Ollama 0.32.3, prompt_toolkit 3.0.52 and httpx 0.28.1. Measurements were made
on the shared host with other applications running, not an otherwise idle lab host.

The runtime is installed for use and inspection. It has **not passed every release
gate**: held-out intent/target accuracy was **189/200 (94.5%)**, below 95%.
Live Jira, optional cloud routes and other platforms remain untested. Passing fake
adapter tests is evidence about the core, not certification of a production Jira site.

## 0.6.0 security, tracing and model roles

**174 tests passed in 72.802 seconds** on the shared reference host.

Twenty adversarial tests cover schema denial of service, private-directory links,
ambiguous JSON, path/header injection, API authority, credential routing, actual
local MCP confinement, output flooding, memory watchdog and cancellation cleanup.
Nine audit tests cover signed file-effect evidence, worker endings, tampering,
rollback, missing receipts, interrupted recovery and crash between manifest and
index commit. Ten routing tests cover explicit roles, original budgets, transport
fallback, refusal/validation stop, image boundaries and synthetic measured ordering.
A further mission test proves vision cannot invent missing screen evidence. The
existing PromptSession navigation test now opens Trace without authorizing work.

The [responsiveness measurement](runtime-benchmark.json) with signed tracing enabled
recorded **0.442 ms status p95** over 1,000 checks and **178.082 ms event-to-UI-emit
p95** over 13 events. A held fake provider request keeps the worker pending while
the actual input loop handles status and pause. Typed input survived concurrent
events and the worker paused after review. This measures handler/emission paths,
not native display paint, long-term retention scalability or real provider latency.

The dedicated virtual environment's 35 installed packages returned **zero advisory
matches** in the final OSV version query; `pip check` passed. Packaging utilities
pip and setuptools were upgraded following the initial findings. Exact scan and
installed-surface reports are retained outside the payload under
`~/.local/share/jira-monkey-validation/`.

Installed verification uses `check_installed_gateway.py` and
`check_installed_mission.py`: the actual `Monkey` command from another directory,
real authenticated MCP/API, private fixture effects under actual SML, installed
Ollama agent assessments, signed trace export, public-key verification without the
private state, and process/manifest cleanup. These scripts report their actual
pass/fail results and retain a synthetic signed trace for inspection. No production
service, cloud inference or native screen/click test is claimed.

Model quality and DGM release gates remain separate. Automatic ordering is tested
against synthetic signed measurements; it is not proof of faster or more accurate
real models. See [security findings](SECURITY.md), [trace coverage](SIGNED_TRACE.md)
and [routing limits](MODEL_ROUTING.md).

## 0.5.0 owned MCP/API and connection setup

**134 tests passed in 42.997 seconds.** Five additional tests cover Monkey-owned
adapter creation with no prewritten file or business-network access, private token
storage, credential exclusion from real PromptSession output/history/events,
native operation schemas and HTTP requests, and actual loopback MCP/API behavior.
All 32 built-in operation schemas are validated. Request checks use fixture
transports; they are not production account certification.

The real MCP/API checks exercise discovery, local task capture, advisory memory,
rejected API approval escalation, required authentication, Host/Origin validation,
bounded requests, token rotation and shutdown. A further end-to-end check starts
an exact operator-authorized mission through the actual MCP endpoint, creates one
Asana-shaped private fixture record through real Kist/SML, and separately reads it
back. An unapproved start and consumed-mission replay are refused. Agent assessments
in this fault-test fixture are deterministic; the real runtime and HTTP effects
are not mocked.

`scripts/check_installed_gateway.py` verifies the installed `Monkey --json repl`
from another directory, generated client settings, authenticated HTTP and actual
MCP calls, foreground cleanup, manifest hashes, and the installed hidden-credential
setup form. The report is written outside the installed payload. Native Terminal
keyboard automation remains blocked by the configured TinkyVision policy; actual
PromptSession tests are reported separately.

Monkey now owns its MCP/API implementation and connection configuration. Vendor
account credentials, optional OAuth registration/refresh, additional API operations
and production integration validation remain separate. No business account was
contacted during these tests. Earlier universal-compatibility and DGM limitations
remain; the old requirement for a user-authored setup file is superseded.

## 0.4.1 bounded mission checks

`python -m unittest discover -s tests -v`: **129 tests passed**, 41.666 seconds.
The 118 prior tests remain, with ten mission checks and a connector regression
proving global runtime configuration changes cannot replace a reviewed request's
pinned Kist binary or Captain sources. Mission tests use deterministic model
responses for fault injection and actual Kist/SML plus the official MCP SDK for
effects. They verify exact assignment/approval, separate ledger read-back, sealed
sign-off, restart without replay, lost response, failed predicates, pause during
inference, stale dates, original budgets, cancellation and retained clarification.
Database invariants reject resetting agent, tool, execution-run and provider counters.
Real PromptSession input exercises Mission navigation, review-note refusal,
authorization, execution and final sign-off. It is separate from native macOS input.

The [live local mission](live-mission-smoke.json) passed on
2026-09-14T05:49:00.594311+00:00. Installed Ollama proposed the two-step fixture plan,
then worker and reviewer agents each assessed their exact step. Actual Kist SML
created one private ledger task and separately read it back. The approved JSON
predicate matched before the harness signed the exact result. All three actual
model calls and the mission records are retained. There were 6,074 deterministic
status samples during planning/execution, p95 **0.302 ms**; this is status-function
latency on the shared host, not terminal paint or a production service benchmark.

The [initial attempt](live-mission-initial-clarification.json) is also retained: it
proposed the correct calls but asked for already-supplied information, so no tool
ran. The planning prompt was clarified to use supplied literal arguments and
documented response shapes. This is development feedback, not a held-out accuracy
result. `/mission-answer` separately preserves questions, the original objective
and budget, and requires fresh approval for the revised proposal.

The installed-command verification script is `scripts/check_installed_mission.py`.
It runs the actual `~/bin/Monkey` from `/private/tmp`, in temporary private state,
with an operator fixture plan and real local agent calls. It checks persisted plan
status across CLI invocations, exact tool actions, sign-off, replay refusal, recall,
idle shutdown and installed manifest hashes. Its report belongs outside the
installed payload so producing evidence does not change its manifest.

Mission agents have authority only for an exact inspected sequence. There is no
automatic variable substitution, unrestricted agent scope, production-service
certification or DGM promotion. The existing held-out score and native DGM blockers
below remain unchanged.

## 0.4.0 execution and connector checks

`python -m unittest discover -s tests -q`: **118 tests passed**, 25.577 seconds.
The suite retains the 93 prior tests and adds 6 scheduling checks, 9 actual
project execution checks, 9 MCP/API integration checks and a real parent-crash
lifecycle test. Dependencies are pinned in requirements-lock.txt; `pip check`
reports no broken requirements. MCP SDK is 2.2.0; JSON Schema validation is 4.26.0.

These new checks use the installed Kist binary and a helper compiled against four
unchanged actual Captain source files. They cover durable approval-before-effect
receipts, scoped writes and read-back, pinned verifier success and failure,
missing input, changed rules, forged records, retained budgets, accepted/applied
pause, altered artifacts and dates invalidating sign-off. Four actual verifier
attempts to read host state, write project files, access network and fork are
denied by Seatbelt. This is not DGM-grade aggregate resource containment.

Actual local SDK servers verify stdio, Streamable HTTP and SSE discovery/calls.
A loopback HTTP API checks OpenAPI local-reference compilation, scalar path
encoding, schema errors, exact request routing, lost write response and redirects
without resend. A separate ledger read-back must match an operator-selected exact
predicate before service sign-off. Uncertain calls can be resolved against a
separately approved inspection; the consumed call is never automatically replayed.
Connection credentials remain outside catalogs and prompts.

The parent-crash test kills an owning process while Kist has claimed an actual
SML capability. The supervisor and Kist host exit, no completion receipt is emitted,
and no matching child process remains. This does not certify cleanup of arbitrary
third-party MCP server descendants or every hardware/power-loss boundary.

The live model check passed on 2026-09-14T05:09:07.305159+00:00. During actual project planning,
2079 deterministic status samples had p95 0.247 ms
on the shared host; this measures the status function, not terminal paint.

The live installed-Ollama fixture in [live-symbolic-smoke.json](live-symbolic-smoke.json)
records an actual local plan, arithmetic source edit, fixed verifier results,
Kist/SML receipts, exact fixture sign-off, a local specialist response and an MCP
request proposal. Fixture approvals are performed by explicit harness assertions;
they are not an independent reviewer or a production operator sign-off. Private
fixture state is removed after capture. This small integration sample is not a new
held-out accuracy score and does not replace the 94.5% control result below.

Native Kist DGM reports **REFUSED (0/6 gates live)**. Hard process containment,
external rollback witness and qualifying private held-out fitness block even manual
evolution. Unattended evolution additionally requires distinct reviewer authority,
witnessed budget reservation and sealed activation authority. Monkey exposes this
readiness and records improvement proposals; it does not execute/publish an
improvement or claim a measured capability gain. All four existing Kist global
autonomy switches were checked and remain false.

Teams, Asana, Monday.com, Salesforce, QuickBooks, live Jira and cloud providers
were not contacted for mutations. Account-specific permissions, authentication,
pagination and business behavior require separately configured integration checks.
The supported surface is described in CONNECTORS.md; automatic OAuth, MCP sampling/
elicitation, resources/prompts and deferred-task polling are not implemented.
The `/delegate` specialists remain advisory. In 0.4.1, mission agents can perform
only exact operator-approved tool steps within the original shared limits.

Native Terminal keystrokes remain blocked by TinkyVision's sensitive-app policy.
Actual PromptSession tests exercise navigation, schedule forms and tool approval
with an empty review-note refusal while input stays usable. These are not native
keyboard automation or physical display-latency measurements.

## Historical drafting-runtime checks

The 0.3.1 baseline was **93 tests passed**, 4.089 seconds.
This includes the original 30 preview tests, the 36 named acceptance tests,
13 additional recovery/control checks, 2 installer upgrade/rollback checks,
6 general-request tests and 6 ticket browser/calendar tests.
The preview baseline was 30 passing tests in 0.243 seconds under Python 3.9.6.

The test names in [test_runtime.py](../tests/test_runtime.py) retain T01–T36:

| Cases | Checked behavior |
| --- | --- |
| T01–T08 | Six-field import; exact command bypass; one schema repair; strict fields and authority; ambiguity; prose typos; exact identifiers; hostile ticket text |
| T09–T15 | Status during a blocked draft; Ollama unavailable; original attempt/call bounds; manual exhaustion; accepted/applied pause; cancelled incomplete output; stale late response retention |
| T16–T19 | Modified approval payload; material source change; wrong origin; duplicate local publication |
| T20–T25 | Lost POST response; failed read-back; no visible reconciliation match; pagination; multiple matches; restart from durable POSTING |
| T26–T29 | Safe-read Retry-After; optimistic concurrent changes; commit failure blocks publication; inert terminal payloads |
| T30–T36 | Socket-free demo; no checkout; unsupported code execution; no provider substitution; shutdown retains counters; narrow rail; factual status |

Additional tests cover lifetime ownership, resume within the same candidate,
feedback during work, pause stops queue draining, natural `show teh draft`,
ambiguous status references, hash-bound conversational confirmation, genuinely
overlapping publish tasks, shared retry budgets, timestamp-only source updates,
import identity verification, operator priority at local request boundaries,
preview migration, and rollback after a final bundle installation failure.

T33 implements the stricter V1 policy of refusing all automatic provider
substitution; there is no hidden fallback. T28 injects commit failure; a physical
disk-full experiment was not performed. Crash-state tests reconstruct persisted
failure points; they do not claim exhaustive power-loss testing of every instruction.

## Real HTTP failure experiments

`python scripts/test_jira_http.py` passed both scenarios using the real asynchronous
HTTP/Jira adapter and a temporary loopback server. The server and connections were
closed afterward. [Raw report](jira-http-integration.json).

- The server committed a comment then closed the connection without a response.
  Monkey entered POST_UNKNOWN. Reconciliation scanned two pages, found the exact
  operation and entered POSTED_VERIFIED. The server observed exactly **one POST**.
- The server returned a 201 receipt then failed read-back. Monkey retained
  POSTED_UNVERIFIED and did not repost.

This checks actual socket failure behavior and wire parsing against a controlled
server. No live Atlassian request or comment was made. The comment/property wire
shape follows the [Jira comment API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/)
and [comment property API](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comment-properties/).
Site permissions, group/role visibility and real tenant behavior still require
an authorized integration issue. The property is a correlation marker, not an
idempotency guarantee; reconciliation never contains a resend.

## Local model selection and language evaluation

No model download occurred. Monkey's `jira-monkey-chat:latest` and
`jira-monkey-worker:latest` tags copy already installed `qwen3-vl:4b-instruct`
weights. Both tags are pinned to:

```text
ee4b975b58c17ce268cd19d40db35d5edc64603035d2ffc1fee1968eb0947f7b
```

Context was 4,096 tokens and intent output was limited to 256 tokens. The model
does not advertise a thinking capability. The generated intent is independently
validated in Python, resolved against host records and checked against the operator's
own input. No model field grants publication authority.

Ollama's grammar compiler rejected the initial large schema repetition limits.
The generation schema now omits only size bounds; the host validator still enforces
them. Object property order is retained on the Ollama wire. These are observations
from this installed backend, not a general promise about all Ollama versions.
See [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs).

| Evaluation | Schema valid | Correct intent/target | Meaning |
| --- | ---: | ---: | --- |
| Raw local model, development fixture | 200/200 | 164/200 (82%) | Model classifier alone; fails accuracy gate |
| Routed interface, development fixture | 200/200 | 195/200 (97.5%) | Used during development; not held out |
| Frozen routed interface, new held-out fixture | 200/200 | 189/200 (94.5%) | Final generalization check; fails 95% accuracy gate |

The held-out fixture has 80 ordinary, 40 misspelled, 40 ambiguous, 20 untrusted and
20 unsupported requests. Expected labels were authored by the coding agent before
evaluation; they were neither generated nor graded by the tested local model.
They have not had independent human label review. No NLP tuning was performed
after inspecting held-out failures. A safe abstention is counted wrong when the
fixed expected label calls for a specific action.

| Held-out group | Correct |
| --- | ---: |
| Ordinary | 72/80 |
| Misspelled | 38/40 |
| Ambiguous | 40/40 |
| Untrusted content | 20/20 |
| Unsupported operations | 19/20 |

The evaluator performed interpretation/validation only, with command dispatch
disabled. It made **zero external mutations**, but that alone does not demonstrate
that every interpreted command is safe to dispatch. Separate core tests exercise
approval, confirmation and outbox enforcement. All designated ambiguity cases
clarified correctly on this finite fixture.

Representative failures: “Display the proposed reply” abstained; “Show me the
comment you drafted” selected show instead of draft; “which tickets need human
inpt” selected the job list instead of needs-you; “Change this ticket's status to
resolved” returned recorded status instead of explaining the unsupported operation.
It did not change Jira. All 11 failures and their exact proposals are retained in
[heldout-evaluation.json](heldout-evaluation.json). Other final reports are
[intent-evaluation.json](intent-evaluation.json) and
[interface-evaluation.json](interface-evaluation.json). Earlier Gemma/Qwen
development reports are retained and explicitly superseded by these results.

Reproduce with `python scripts/evaluate_intents.py --model jira-monkey-chat:latest
--hybrid --heldout --output PATH`. Repeating this known fixture measures regression,
not a fresh held-out result.

## Responsiveness and actual drafting

[runtime-benchmark.json](runtime-benchmark.json) uses the real PromptSession loop,
pipe input, in-memory output, SQLite and a delayed fake provider. A partial typed
line survived interleaved events and completed as a draft request.

| Boundary measured | Samples | p95 |
| --- | ---: | ---: |
| Deterministic status function | 1,000 | 0.015042 ms |
| Committed event to UI emit/flush callback | 13 | 44.451 ms |
| Status while real local drafting ran | 200 | 0.046708 ms |

The first two meet the stated 100/250 ms engineering targets **at those measured
boundaries**. The event result is not a physical screen-paint latency measurement;
the small sample and in-memory output limit what it proves.

The held-out interface used 109 deterministic paths and 91 local requests.
Overall subsequent-input p95 was 4,132.69 ms, mixing both routes. For an explicit
warm proxy of Ollama-reported load time below one second, 75 local calls had p50
1,257.09 ms and p95 **1,448.99 ms**, meeting the three-second warm target. Sixteen
calls had load time at least one second; their wall-time p95 was **100,347.39 ms**.
The first local request took 9,451.31 ms. Cold/reloaded requests can be slow on this
shared host; the warm result is not a blanket conversation-latency guarantee.
Exact controls stay available while those calls are pending.

`python scripts/live_smoke.py` used real local Ollama for triage, draft and review
of a synthetic six-field ticket. It saved one completed candidate, received PASS,
applied the requested pause after review, and retained delivery DRAFT with **zero
Jira POSTs**. Three measured worker calls and their actual output/usage are in
[live-local-smoke.json](live-local-smoke.json). A reviewer PASS is a model result,
not proof of correctness or a code fix. Token/timing fields come from the
[Ollama chat response](https://docs.ollama.com/api/chat); unprovided cost is unknown.

## Deployment and remaining limits

Version 0.3.1 adds the installed `Monkey` command, the banana ticket navigator,
current/finished ticket trees, saved draft and event readers, exact local sign-off,
and a completion calendar. Keyboard tests use a real PromptSession and pipe input:
arrow sequences open the chosen ticket and draft without approving or publishing;
typing still submits normally. Other tests cover sign-off moving local work into
the calendar, Jira approval staying current until verification, stale sign-off
refusal, date filtering, leap-month navigation and rejected work retaining its
actual outcome.

The installed CLI was exercised from `/private/tmp` with the exact plain request
“Draft a sprint ticket for password reset”. Real Ollama returned DRAFT_READY after
one candidate and three calls, with no Jira access. An earlier triage prompt
incorrectly demanded implementation proof before drafting proposed criteria; that
failure is retained alongside the successful corrected runs in
[local-request-validation.json](local-request-validation.json). The control-intent
evaluation above predates the new direct-request path, which is tested separately;
it is not a fresh accuracy benchmark of every 0.3.1 input path.

Inspecting the plain-request draft also exposed unsupplied assignment, estimate
and priority fields that the model reviewer had missed. The final core now forces
revision of those ungrounded planning commitments, with a regression test proving
they cannot be approved even after repeated model PASS results. The raw earlier
draft and review remain in the report; they are not claimed to have passed that
subsequently added guard. This finite guard is not a general correctness proof.

Native Terminal launch and the actual 80×24 banana navigator were inspected through
TinkyVision: [screen capture](native-banana-menu.png). TinkyVision's sensitive-app
deny-list rejects Terminal click/type/key operations. Native keyboard automation
was stopped at that boundary; the permission setting was not changed and no other
UI automation stack was substituted. Keyboard behavior is supported by the
PromptSession tests, not a claim of native macOS keypress testing.

The installer stages the payload, validates the recognized installation, retains
the previous app/CLI/payload in a backup, and preserves state. A failed final
bundle installation was fault-tested to restore all prior entrypoints byte for
byte. The standalone runtime owns no Kist service or repository worker.

Native Terminal verification and final installation receipts are recorded in
[BUILD_NOTES.md](BUILD_NOTES.md). The existing unrelated Windows guest was left
alone; no additional guest or isolated UI session was needed. Native UI inspection
uses TinkyVision, with no browser runtime.

There is no code execution, hidden daemon, automatic publishing, automatic
provider fallback, automatic uncertain-write retry or resend-unlock mechanism.
No Windows/Linux terminal test, live cloud test, live Jira tenant test, independent
security audit or exhaustive crash/disk-failure matrix was performed. Exact commands,
offline demonstration and durable recovery remain usable when local language
interpretation fails.
