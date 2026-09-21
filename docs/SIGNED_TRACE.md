# Signed work traces

Monkey 0.6 records what its application actually observes. Every worker,
conversation, execution, specialist, publication and reviewed mutation run gets a
start, retained activity and a signed ending. Failures and interruption remain
failures and interruption. Signing is not completion or operator approval.

```text
/trace JOB
/audit JOB
/audit-export JOB
Monkey audit-verify /absolute/export.json --fingerprint TRUSTED_SHA256_FINGERPRINT
```

Open the ticket's **Trace** tab with the arrow keys. The terminal view is bounded
to recent records; the exported bundle contains the journal through its signed
checkpoint. Exports include shared application activity, even when requested from
one ticket. They are private files and can contain task metadata and event text.

## What is recorded

- Commands and provenance; atomic hashes of inserted, updated and deleted job,
  evidence, approval, outbox, usage, artifact, session and event rows.
- Model role, selected provider/model/digest, captured roster hash, latency,
  validated response hash, transport fallback or blocked interpretation.
- Dev11 local Ollama streams add first-output timing, bounded progress
  checkpoints, received byte/frame counts and separate content/thinking hashes.
  A settlement distinguishes a validated complete response from interruption or
  rejection. Provisional text and intermediate thinking are not copied into the
  journal or exposed as completed work. Token usage stays unknown when the final
  provider frame is missing. Status publishes progress after its journal commit.
- Scoped project reads/writes with exact paths and before/after hashes.
- Provider HTTP and MCP/API destinations, request/result hashes, attempts and
  errors. Configured authentication values are excluded from these trace fields.
- Native admission or captured SML requests/receipts, verifier output hashes, connector child PIDs,
  confinement, observed resource limits, exit and cleanup.

Runtime library imports, a child program's individual system calls, remote service
internals and provider internals are not observed. A returned receipt does not
prove that every remote side effect was visible. This is application tracing,
not an endpoint-security recorder. Losing a response retains uncertainty.

The local stream parser follows Ollama's documented
[newline-delimited responses](https://docs.ollama.com/api/streaming) and
[chat completion/usage fields](https://docs.ollama.com/api/chat). It bounds wire
data to 2 MiB, each frame to 128 KiB, generated content to 30,000 characters,
intermediate thinking to 128 KiB and total frames to 16,384. The original absolute
request deadline still applies during ongoing output. A final `done` frame,
`done_reason: stop`, settled response body and Python schema validation are all
required before output is usable. Missing completion, late extra frames, malformed
JSON and native tool calls cannot grant action authority or trigger a model swap.
Transport retry budgets remain separate and unchanged.

## Verification format and trust

Each entry contains sequence, identity, UTC time, kind, job/run references, data
and the previous entry hash. Canonical JSON uses sorted keys, ASCII escaping and
compact separators. SHA-256 covers those fields; Ed25519 signs its 32-byte digest.
The final manifest binds the journal length/head, outcome and signing-key
fingerprint and has a separate Ed25519 signature. The implementation uses
[cryptography's Ed25519 signing and verification API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

The private signing seed stays in `STATE/audit/signing-key.json`, mode 0600, under
owner-private directories. It is never exported. Bundles include only the public
key, entries and ending seal. The required trusted fingerprint detects a different
signer; verification does not need Monkey's database or private key. Keep the
fingerprint and an export outside the writable Monkey state for comparison.

Store changes and their signed records commit in one SQLite transaction. A
separate durable checkpoint detects a journal shortened behind its last saved
head. Startup verifies the journal, stored records, run seals and checkpoint;
tampering stops new work. Interrupted runs are closed as interrupted, never replayed
as a recovery shortcut. Missing keys are not silently regenerated for an existing
journal. Preserve the whole state directory when investigating an error.

This local key is not a hardware-backed or third-party witness. An attacker who
controls the account/root and can replace the key, database and checkpoints can
forge a different local history. Independent retention is needed to detect that
replacement. Hashes establish integrity of recorded evidence, not correctness of
a model's claims or business outcome.

On first upgrade, existing stored rows receive a baseline hash. Earlier I/O is not
reconstructed. An older executable cannot safely continue this signed store: use
the matching application and preserve/restore a consistent whole-state backup.
