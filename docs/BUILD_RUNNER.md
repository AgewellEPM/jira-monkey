# Offline multiprocess builds

The dev13 candidate adds a real Linux container runner for general build work.
It runs Python, Node, C/C++, Make and Git from an exact local image. This is
development functionality; the default installed dashboard refresh remains on
its separate 0.6.0 worker.

`builder use --file PATH` selects a generated recipe after checking its engine,
image, supervisor and resource controls. `/builder status` reports the selected
backend. `/builder native` selects the existing native verifier for new jobs.
Existing jobs retain the backend captured when they started. Selection neither
downloads an image nor starts a VM. The dev14 candidate adds separate
`/builder setup`, `start` and `stop` commands, along with captured-environment
startup for jobs; see [MANAGED_BUILDER.md](MANAGED_BUILDER.md). Its guardian
owns the foreground environment and records verified shutdown. External recipe
selection retains its original explicit-engine contract.

The worker transfers a bounded source snapshot through a temporary named volume;
it mounts no host project, home, credentials, Docker socket or repository metadata.
Source links and special files are refused. Known secret paths and dependency
directories are excluded with recorded reasons. Arbitrary secret content cannot
be identified from a filename alone; select an appropriate project scope.

Commands run in an isolated writable copy, as UID 1000 with zero capabilities,
no network, and no new privileges. The image root and input are read-only. The
worker uses private PID, IPC and cgroup namespaces, 768 MiB memory with no extra
swap, 1.5 CPU quota, 128 processes, a 128 MiB workspace and 32 MiB scratch area.
The workspace permits execution of the programs it builds. No browser runtime
is installed or invoked by this backend. These controls use Docker's documented
[container runtime options](https://docs.docker.com/engine/containers/run/).

A trusted PID 1 supervisor and tracer retain command output, file manifests,
source changes and file/process/network/credential syscall observations. The
command cannot write their evidence directory. All descendants must be gone
before output files are captured. The trace caps strings at 4096 bytes and the
trace file at 8 MB; it is not an exhaustive host-wide or remote-internal trace.
The image ID, package inventory, command arguments, source hashes and resulting
evidence belong to the retained operation. A signature binds observed records;
it does not establish that a model's code or conclusion is correct.

The host validates returned paths, types, hashes, preimages and completion
evidence. Complete commands can import ordinary UTF-8 source changes with the
existing per-file journal, preimages and read-back. Host source drift or changes
to project guidance block import. Binary outputs remain inspectable artifacts
in the build report. Multi-file imports are not atomic: a later failure does not
erase earlier recorded effects. Exit zero with incomplete evidence is not a
passing verification. A successful check still requires outcome review.

Every operation gets a durable claim before engine effects. Interrupted workers
retain available fixed evidence paths before owned containers and volumes are
removed. `builder recover JOB --operation BUILD_ID` inspects and stops that exact
job-bound operation; it never restarts the command. Resolve the corresponding
general-agent interruption separately after inspecting the actual effects.

The current source snapshot limits are 4096 files, 2 MB per file, 32 MB total,
and 32 directory levels. Source import accepts text up to 250 KB per file. Output
is capped at 256 KB. Commands have a default 60-second deadline (bounded to 120
seconds in the low-level runner). Dependencies are not fetched during commands.
Auditable dependency acquisition is still a separate release requirement.

`scripts/qualify_build_runner.py` builds the image from a resolved official Debian
digest using its signed package repositories, then tests actual C and subprocess
builds, isolation, output/time/process/memory bounds and a killed host driver.
Model decisions in its full worker case are explicitly scripted. Native model
task quality and Windows/native Linux CLI qualification remain separate gates.
See VALIDATION.md for dated results and failures.
