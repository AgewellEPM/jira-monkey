# Windows accessibility integration — unfinished

The required targets are distinct enrolled Windows 10 and Windows 11 systems.
The latest read-only GhostBridge catalog exposes `legacy.win10` and `legacy.win11`
with their generation-specific application identities. The catalog does not grant
session or action authority. Perslis/GhostBridge must continue to own the guest
lifecycle, and a licensed installed disk is required for the exact target.

## Observed provider contract

The configured `ghostbridge-legacy` and `ghostbridge-vcards` MCP tools expose this
division of responsibility:

- Legacy catalog/status and session operations own the target and lifecycle.
- `legacy_accessibility_tree` returns frame-bound labels, roles, bounds and
  provenance. These are untrusted observations, not action addresses or proof of
  native Windows UI Automation coverage.
- Direct `legacy_click` does not execute arbitrary labels or coordinates. Its
  current contract requires the signed V-Card workflow provider for mutation.
- V-Card compilation proposes a fixed workflow; external human Ed25519 approval
  binds the exact draft and card digests before admission.
- `vcard_catalog_observe` obtains a signed provider catalog for an approved
  workflow. Session, provider, helpers and signing custody come from separate
  owner enrollment, not caller-supplied replacements.
- A dry run binds signed catalogs and sealed input. Starting a run performs no
  first action. `vcard_run_step` accepts only the run ID and current state digest;
  the provider resolves one fixed action, verifies its transition and receipts it.
  Cleanup uses its separately enrolled authority.

These statements come from the configured MCP tool descriptions and local provider
sources inspected on 2026-09-14. They describe an integration contract, not an
executed Windows acceptance test.

## Monkey implementation still required

The 0.7.0.dev8 candidate adds a dedicated coordinator over the existing exact MCP
approval path. It retains the target generation, owner session, workflow and state
digests, observation provenance, submitted requests, replies and cleanup reports
in the signed journal. Wrong targets, changed profiles/connections, stale state,
generic-tool redirection and replay are refused. Read-only result inspection is
also exposed through Monkey's own MCP/API. See WINDOWS_COORDINATOR.md.

This application work does not supply the missing native asymmetric verifier or
owner enrollment. The coordinator requires the workflow's signed-provider session
namespace to match the lifecycle owner's exact session; an authenticated bridge
between those components still needs native qualification.

Both configured GhostBridge MCP providers currently launch local Node programs
that use additional native helpers. Monkey's generic local MCP sandbox currently
denies child process creation, so importing their launch entries is insufficient.
Establish a supported provider transport and explicit ownership boundary before
claiming a connected implementation; do not silently remove confinement or start
an unrestricted replacement after a denial. Remote Linux/Windows clients also need
an authenticated, enrolled route to the provider that owns the Windows session.

Qualification must use actual Windows 10 and Windows 11 independently: install and
use Monkey from PowerShell, obtain fresh tree evidence, inspect a concrete workflow,
perform its authorized action, verify the visible result and signed receipt, and
stop the owned guest. Include stale target, changed session, denied consent,
response loss, restart, cancellation and independently verified cleanup cases.

## Current external state

The subsequent `vcard_status` check reports execution disabled, no enrolled UI
provider, no valid workflow-approver key, no independent cleanup supervisor and
no observation witness. There are no approved workflows or dry runs. This is
missing owner trust configuration in GhostBridge, not a Monkey model-selection
option. See ENTERPRISE_BLOCKERS.md for the exact current prerequisites.

The resumed session progressed after Windows 95 closed independently. GhostBridge
launched the exact Windows 10 guest; an uncertain open acknowledgement reconciled
to HOT without another launch. Fresh OCR still showed its boot screen. No desktop,
PowerShell or Monkey execution was reached. GhostBridge then denied native consent
to close the test guest; the latest status remains HOT with its VM slot held.
See ENTERPRISE_BLOCKERS.md for the exact session and required cleanup. No new guest
or alternate shutdown/control route may bypass that denial.
