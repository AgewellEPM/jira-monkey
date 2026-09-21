# Development deployment candidate

The installed command selects the local 0.6.0+dashboard.1 terminal/web refresh on
the existing worker. The source candidate is 0.7.0.dev13 and is not qualified
for enterprise rollout. The table records
specific earlier native evidence; none is inferred from packaging. See
[current release gates](RELEASE_READINESS.md) and [dated validation](VALIDATION.md).

`Monkey dashboard` opens a local web endpoint and keeps the terminal prompt
active. This uses a separate runtime payload with an atomic launcher update;
existing open sessions and their state were preserved. Native visual validation
was blocked when IsolatedTester could not capture a NativeBrowser window.
See [WEB_DASHBOARD.md](WEB_DASHBOARD.md) for operation and access boundaries.

| Surface | Implemented | Native evidence |
| --- | --- | --- |
| macOS ARM64 / Python 3.11 wheel | Console commands, state, local models, HTTP MCP/API, scoped project tools | Offline wheel installation and actual `Monkey` commands checked |
| Linux ARM64 / Python 3.11 wheel | Portable console/state and HTTP integration code | Dev8 r7 installed CLI/state/audit fixture passed in a Linux VM/container; interactive, HTTP integration and current-candidate checks remain |
| Linux x64 / Python 3.11 wheel | Portable console/state and HTTP integration code | Complete pinned wheels resolve; native execution pending |
| Windows x64 / Python 3.11 wheel | Console entry points and native SID/ACL/locking code | Five Windows 10 dev8 file/state checks passed; full installation and console qualification timed out |
| Windows ARM64 / Python 3.13 wheel | Packaging target | Blocked: cryptography 50.0.1 has no native Windows ARM64 wheel on PyPI at the checked date |
| Windows 11 ARM64 / x64 Python 3.11 compatibility | Explicit Windows x64 emulation target | Pinned x64 wheels resolve; guest execution is pending |
| General coding commands | Existing macOS verifier plus an explicitly selected offline Linux container backend for `/build` | Dev13 installed Mac ARM64 client/Linux ARM64 image passed ten real build, source-import, isolation and recovery cases. Foreground VM provisioning, dependency acquisition and native model outcomes remain open. |
| Windows accessibility | Exact-session/workflow coordinator over approved MCP requests | Installed synthetic HTTP protocol fixture passed; native actions and independent provider proofs remain unqualified. Prior test sessions are closed. |

These are specific targets. Python dependencies, native APIs and command parsing
must be tested on each actual OS/architecture before advertising support.
The Windows package gap must be resolved with a qualified build or a separately
qualified interpreter architecture. Monkey does not downgrade cryptography,
replace a Windows generation or launch another guest to evade consent.

## Offline bundles

With the development build tools already installed:

```sh
python3.11 scripts/package_bundle.py --target macos-arm64-py311 --output /absolute/new-bundle
```

Other target names are `linux-x64-py311`, `linux-arm64-py311`,
`windows-x64-py311` and `windows-arm64-py313`. The builder snapshots its source,
selects exact dependency pins using target markers, and downloads only binary
wheels from PyPI. It checks interpreter/ABI/platform tags, Python requirements,
transitive metadata and extras. A missing wheel stops packaging.

`windows11-arm64-x64-py311` is a separate compatibility target for the registered
Windows 11 ARM64 guest. It uses Windows' x64 application emulation and preserves
the same dependency pins. The launcher checks `IsWow64Process2`, the Python
executable's PE architecture and a Windows 11 build number; its installation
receipt records the process and native architectures. Native x64 bundles refuse
an ARM64 host. Selection never falls back to emulation automatically.
See Microsoft's documentation on [Windows on Arm emulation](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation).

The bundle contains `start_monkey.py`, `qualify_cli.py`, `INSTALL.txt`, wheels,
hash-pinned requirements, package inventory, source hashes and a build log.
From a Windows x64 bundle directory, start it in PowerShell with:

```powershell
py -3.11 -I .\start_monkey.py
```

If the optional `py` launcher is absent, invoke the installed Python 3.11
executable by its absolute path with the same `-I` argument. The bundle does
not install Python or silently select another interpreter.

On either packaged Linux architecture or macOS ARM64:

```sh
python3.11 -I ./start_monkey.py
```

The launcher checks the actual interpreter and native architecture, the complete
package inventory, exact requirement hashes, source and helper bytes. It creates
a copied private virtual environment, installs only bundled wheels with
`--no-index` and `--require-hashes`, checks dependencies and the real console
entry point, then opens the foreground prompt. Repeating the command reuses only
an unchanged runtime. `--check` is read-only; `--install-only` skips the prompt;
`--qualify` runs the real console/state/signed-audit fixture and retains its report.
Append `-- status` for an exact command. Use `--manifest-sha256 HASH` when the
expected bundle hash was supplied separately.

Each setup step has a 600-second bound and prints its actual stage. A timeout
attempts cleanup of that installer's process tree; any cleanup failure is
reported and requires inspection before retrying. Installation logs record the
command and bound. Use a fresh bundle directory after a failed partial install.

The runtime, install log, receipt, qualification reports and separate candidate
state live in `.monkey-install` beside the bundle. The receipt records source,
binaries, metadata and the base Python hash. Default `__pycache__` files are not
part of that inventory: checked launches use a fresh empty private cache prefix
and disable cache writes, so they load verified source instead. Direct console
use can create ordinary bytecode without breaking a later checked launch.
A partial or changed installation is
preserved and refused, not overwritten. Keep the bundle at its installed path
because virtual-environment launchers contain absolute paths. This path does not
replace the production command or edit global PATH. Python itself must already
be installed. Artifact hashes detect changes; publisher signing, external native
libraries and native OS qualification remain separate gates.

When evaluating a candidate alongside an existing installation, use a separate
state directory: `Monkey --state /absolute/private/monkey-candidate-state`.
Candidate validation must not migrate or change the production state implicitly.

This supplies neither Python itself nor model weights. `/models` lists installed
local Ollama models; `/route ROLE --model EXACT_TAG` selects one without downloading
it. `/setup --request-timeout SECONDS` applies a bounded timeout to future jobs.
Captured jobs retain their original configuration and total budgets.

## Local state and operations

Dev8's Windows binding commits a version-4 database floor. An older executable
refuses that state. Its coordinator preserves the exact owner target/session,
workflow/CAS state and returned reports; it does not supply missing native
enrollment or verify Windows postconditions by itself. See WINDOWS_COORDINATOR.md.

Each process owns one state directory; a second owner is refused. POSIX state uses
owner-only modes and descriptor-based path access. Windows code uses current-user
SIDs, ACLs and held directory handles, rejects reparse points and network drives,
and has five native Windows 10 dev8 file/state checks; broader Windows
qualification remains incomplete. Back up the entire state after `/quit`.

New project and connected operations use Monkey's own admission by default.
Every exact operation gets a consumed durable claim before its effect and a
sealed receipt after an observed return. Receipt loss does not cause replay.
Existing Kist recipes require their captured optional runtime; changing global
configuration does not reroute them silently.

An update invalidates old approvals for new execution under changed code. It does
not erase completed effects or require replay for review. Reviewing historical
results verifies retained bindings and evidence and records both the execution
and review runtime hashes. Uncertain delivery still needs a separately recorded
service read and explicit operator resolution.

General jobs created by dev7 also capture the application Python source hashes.
The worker checks them before inference and tool dispatch; changing or replacing
Monkey stops new actions under the old scope. Start a new job in the same workspace
after inspecting its retained effects. This does not reset the old job's limits.
Already completed results can be accepted after an upgrade with both runtime
hashes retained. Jobs captured by older releases remain inspectable; their missing
execution-runtime provenance is reported explicitly and they cannot gain a new
runtime implicitly through `agent-continue`.

The first captured general job with runtime pins raises SQLite `user_version` to
3 in the same transaction as that job. Older executables refuse this state instead
of ignoring the runtime field. Opening legacy state for inspection does not itself
raise this compatibility floor. Back up the entire closed state before upgrading;
rolling back an executable does not undo later work or convert newer state. Use a
matching newer executable for current evidence, or restore the complete prior
state separately when deliberately inspecting a pre-upgrade backup.

Use `/caps` or `/doctor` to inspect implemented platform boundaries. Unsupported
coding containment is reported before a model starts building. Exact commands,
drafting and HTTP integrations do not depend on a running coding subprocess.

## Remaining release gates

Native Linux and Windows installs, Win32 ACL/reparse/lock tests, real PowerShell
and terminal use, Windows 10/11 accessibility control and cleanup, a general build
runner, production service validation and successful local-model acceptance are
unfinished. Managed enterprise deployment, dependency updates, signed distribution
and self-improvement promotion/rollback also require qualification.

Package hashes and Monkey's audit signatures provide evidence integrity. They do
not establish enterprise fitness, model correctness or coverage of hidden actions
inside a remote service or child process.
