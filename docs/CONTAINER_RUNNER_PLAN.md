# General build backend — active implementation work

Dev13 now implements the bounded runner described in BUILD_RUNNER.md. The first
nine real Linux-container cases passed, including C and Python/Node subprocess
builds, isolation, output/time/process/memory limits and killed-driver recovery.
The separately installed candidate then passed all ten cases, including the REPL
worker, recorded source import and preserving a captured backend after a settings
change. Model decisions in that worker test were scripted, not local-model output.
Foreground provisioning/ownership of the build environment and auditable public
dependency acquisition remain implementation work; this plan is not complete.

The current native verifier denies forks and writes to source. It cannot be
called a full build backend. Colima 0.10.3 and Lima 2.2.0 were installed from
Homebrew's checked bottles on 2026-09-14. The September 15 validation run used
one owned Linux VM and an unprivileged, offline Python container for CLI/state
checks, then removed the container and stopped the VM. Those results do not
implement or qualify the general build backend below. Logs are in the private
validation directory and linked from VALIDATION.md.

Selected implementation direction:

- One dedicated `monkey` Colima Linux profile, 2 CPUs / 2 GiB memory, bounded
  disk. No automatic Docker-context activation, SSH-agent forwarding, home
  mounts, SSH-config edits, published application ports, or background service.
  Use `--mount none --activate=false --ssh-config=false --ssh-agent=false
  --port-forwarder none`. Check actual version help before use.
- Check active isolated sessions and guest processes before qualification.
  There were none at the last read-only check. Always stop the owned profile
  in a guaranteed cleanup step and verify guest/helper cleanup. Windows guests
  remain exclusively under Perslis/GhostBridge; do not substitute one.
- An explicit private Docker client configuration and the profile's own Unix
  socket. Do not reuse the operator's Docker credentials or active context.
- A pinned local builder image with Python, Node/npm, C/C++ tools, Git and
  syscall tracing. Build from a resolved official Debian image digest; use
  signed repositories, disable recommendations, retain installed package
  versions and the resulting image ID. No browser packages or runtimes.
- Transfer a bounded, descriptor-checked source snapshot, excluding secret
  files and Git/host state. No host bind mounts. Use a temporary named input
  volume filled through a never-started helper container; mount it read-only
  in the worker. A separate root-owned evidence volume retains results even
  if a worker dies. Delete only the exact owned containers/volumes afterward.
- Worker: own PID namespace, no network, read-only root, no privilege gain,
  hard cgroup memory/CPU/PID limits and bounded tmpfs working directories.
  A pinned trusted supervisor runs as root; the actual untrusted command
  drops all capabilities and UID/GID to a non-root identity. Parent-only
  capabilities may be needed for that drop, tracing, and child cleanup;
  verify they do not survive into the command.
- Run the command against a writable isolated copy. Kill/verify all command
  descendants before capturing output. Retain syscall logs and before/after
  file hashes. The trusted supervisor's evidence directory must be inaccessible
  to the command. Validate every returned path/hash/size/type again in Python
  before applying ordinary source changes against their captured preimages.
- A durable container operation ID is saved before create/start. Recovery
  inspects and stops that exact container, never blindly starts a replacement.
  Read-back, cancellation, OOM, output caps, detached children, forged receipts,
  symlink escapes and source drift require real tests before activation.
- Keep the native backend available for previously captured jobs. New jobs
  capture a qualified image/backend recipe. The foreground Monkey application
  owns startup and shutdown; the user does not manage another terminal.

Public dependency acquisition is a separate remaining piece: it must be
auditable, bounded, without ambient credentials or installation scripts that
download prohibited browser runtimes. Ordinary execution stays offline.

Native Kist DGM readiness remains unchanged. A qualified container runner does
not itself satisfy independent review, rollback-witness, fitness, budget or
promotion gates. General self-improvement still needs implemented, tested
candidate evaluation and reviewed promotion/rollback.
