# Enterprise deployment prerequisites — 2026-09-15

The full enterprise goal is not complete. Source work continues on 0.7.0.dev11;
the default production command remains 0.6.0. Its required gates are in
RELEASE_READINESS.md. Before the latest startup changes, the separately installed
dev8 candidate passed 261 source tests and an actual installed CLI/HTTP-MCP coordinator
fixture with 16 calls and a verified 513-entry signed export. The Windows replies
were synthetic. These results do not qualify another OS or enroll native authority.

The September 15 recovery run subsequently added five passing native Windows 10
file/state checks and passing r7 macOS and Linux ARM64 installation/CLI/audit
fixtures. Linux's public signature was also verified on the host after transfer.
Full Windows 10 console qualification failed on a timeout, and two Windows 11
launch attempts did not start a guest. All test VMs and temporary helpers are now
stopped; Ollama's original local-only service is restored. Details and the signed
evidence report are under
`~/.local/share/jira-monkey-validation/0.7.0-dev8-native-recovery-20260915`.

## Revalidated external prerequisites

| Prerequisite | Authoritative observation | Required change |
| --- | --- | --- |
| Windows UI authority | `ghostbridge-vcards.vcard_status` reports `allowExecution: false`, no UI provider, no workflow-approver key, no valid independent cleanup supervisor and no observation witness. There are zero approved workflows and zero dry runs. | Owner-approved provider, approver, cleanup and witness enrollment, followed by the required signed workflow admission. Monkey cannot create this authority by treating its own proposal as approval. |
| Exact Windows sessions | Windows 10 build 19045 recovered, installed Python and Monkey, and passed five native file/state checks. Its `caps` fixture exceeded 90 seconds. Windows 11's enrolled portal returned `portal-status-uncertain` twice, then CLOSED without starting a guest. | Resolve Windows 10 console performance and the Windows 11 launch interface, then complete native qualification. No alternate Windows generation is a substitute. |
| Native test capacity / cleanup | Windows 10 completed in-guest shutdown after recovery; Perslis finalized QMP shutdown and GhostBridge released its slot. Both Windows 11 reservations are CLOSED. The Linux container was removed and its VM stopped. Final checks found no VM or temporary helper process. | Continue to enforce one guest at a time. The earlier consent denial is historical, not a current request to repeat approval. |
| Local inference | The earlier installed ticket worker completed three calls and a reviewed pause. During native testing Luke authorized temporary Ollama shutdown. Repeated automatic restarts required a bounded localhost maintenance response; it has stopped. The original local-only service is healthy and both Monkey model tags are present. | General coding and broader model quality remain unqualified; metadata availability and the successful ticket fixture do not establish them. |

The fixed trust directory `/Library/Application Support/GhostBridge/Trust` is
absent. Its parent is root-owned, and `sudo -n` requires administrator
authentication. Owner-side setup cannot replace this with a writable trust path.
No native-provider signing keys, human approvals or live policy entries were generated.

Inspection of the native Windows provider also identifies a software gap:
`Platform/Windows/RemoteSupportBroker/NativeVCardProvider/README.md` in the
GhostBridge controller project explicitly states that the separate asymmetric
catalog/admission/post-observation/recovery verifier is not implemented or
qualified. The existing Mac bridge is a translator, not a Windows actuator.
Generating keys or turning on `allowExecution` cannot supply those missing proofs.

The Windows 11 target is ARM64. Native ARM64 packaging remains blocked by the
pinned cryptography wheel's availability, rechecked on PyPI on September 15.
A separately named `windows11-arm64-x64-py311` compatibility bundle now resolves
the same pinned x64 packages. It requires kernel/process architecture evidence
and Windows 11's x64 emulation. This candidate is prepared for the exact same
registered Windows 11 guest; native guest execution remains pending. It does
not qualify native ARM64 Python or silently substitute a different OS.

A Windows 10 launch-argument defect now has a tested candidate fix. The shared
USB data disk carried a device boot priority alongside `-boot order=c`, a
combination QEMU defines as undefined. Removing that conflicting priority passed
eight focused checks and a release build. Selecting the ATA OS disk in the live
BIOS menu reached Windows, confirming the practical boot-order cause. The
candidate retains the authenticated GhostBridge lease boundary and is not
activated. Automatic boot with that installed candidate remains unqualified.
See VALIDATION.md and
`~/.local/share/jira-monkey-validation/0.7.0-dev7-win10-boot-order/result.json`.
This configuration fix does not supply the missing native provider or enrollment.

The current read-only evidence is retained privately in
`~/.local/share/jira-monkey-validation/0.7.0-dev7-enterprise-blockers.json`.
It records the earlier provider readiness, exact target profiles, guest ownership
and local-provider failures. The resumed Windows 10 lifecycle/boot observations
and denied close are separately retained in `0.7.0-dev7-win10-native-session.json`.
No workflow approval, provider enrollment, production installation or
business-service configuration was changed.

## Work that remains after the environment is ready

Implement and exercise the general build/container runner, connect the new
Windows coordinator through a supported enrolled provider transport, and qualify
the independent native proof issuer. Finish both exact Windows targets and extend
the passing Linux CLI/container checks, including process cleanup, PowerShell interaction,
model outage, API/MCP effects and uncertain-result recovery. Live model quality,
production-service compatibility and self-improvement evaluation/promotion remain
separate unfinished gates. A transport fixture or more macOS-only checks cannot
substitute for these requirements.

The earlier goal was paused after these prerequisites persisted across three
turns. Luke explicitly resumed the work, starting a fresh audit of blockers.
The resumed turns added checked startup bundles, an installed local-model trial,
a tested Windows boot-order candidate and the installed Windows coordinator
fixture. Their evidence and remaining limits are in VALIDATION.md. The same owner
enrollment and native consent prerequisites remained across those earlier three
resumed turns. Luke subsequently authorized retry and recovery; the current turn
reached real Windows PowerShell, fixed measured setup issues, passed native Linux
CLI checks and completed guest cleanup.
Owner enrollment and native-provider proof remain separate unfinished gates.
No missing approval or proof was replaced with a local fixture or a flag.
