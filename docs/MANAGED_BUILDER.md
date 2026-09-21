# A build environment owned by the foreground Monkey session

The `0.7.0.dev14` development candidate adds asynchronous environment ownership
to the captured container build recipe. It is separate from the installed
`0.6.0+dashboard.1` interface refresh and is not a worldwide release declaration.

## Commands and lifetime

On macOS, with Colima and the Docker CLI already installed:

```text
Monkey builder setup
/builder status
/builder stop
/builder start
```

Setup opens the regular prompt, acquires one global Monkey environment lease,
starts a private Colima profile, downloads a digest-pinned Debian base, installs
the build tools, and runs an actual Python/Node probe. It retains the image ID,
engine identity, tool catalog, package inventory and configuration hash before
selecting the recipe for new jobs. The first command acknowledges a request;
only a later `READY` event records successful preparation.

The default folder is `~/.monkey-build`. An alternate `--root` must be an empty,
private directory whose name starts with `.monkey-build` and whose Unix socket
paths fit the platform limit. Project tools exclude these directories, including
the shared `.monkey-build-lock` lease. The managed profile uses its own Colima
and Docker client state and disables home mounts, agent forwarding, template
inheritance, automatic Docker context selection and guest port forwarding.

The prompt and dashboard continue to show recorded state during setup. `/builder
stop` can cancel setup. Stop requires active work to settle first. `/quit` stops
the foreground workers and releases the environment. Stopping retains its disk,
image, package inventory and recipe; it does not remove user projects.

New general build jobs capture both the recipe and its environment descriptor.
They start that captured environment before calling their model or tools, inside
the original job deadline. Changing the global recipe cannot change an existing
job. `/builder native` selects the native backend for new work while retaining
the managed environment descriptor. `/builder start` explicitly reselects the
retained managed recipe. `/builder use --file RECIPE` selects an external recipe.

`/builder recover JOB --operation BUILD_ID` runs as a foreground background task.
It starts a job's captured managed environment when required, then reconciles
only the objects bound to that operation. It never replays the command.

## Cleanup evidence

A small guardian process holds the shared environment lease and an inherited
pipe. Closing that pipe, including when the Monkey process is killed, requests
bounded shutdown of the exact captured profile. The guardian does not start a
VM. It retains bounded command output and signs a hash-linked journal and final
cleanup receipt with an ephemeral Ed25519 key. Monkey pins the public fingerprint
when ownership is acquired and verifies the final receipt and output hashes.

An interrupted setup command retains its partial stdout and stderr with hashes,
process identity and outcome. A configuration change blocks reuse. A missing or
invalid cleanup receipt is an error; neither an accepted stop request nor a
mutable owner file proves that the guest stopped. Normal shutdown attempts all
cleanup stages even if an earlier stage fails.

Status reads append individually signed records without creating a new whole-run
seal. Work completion copies a consistent verification snapshot on the database
thread, then checks every captured signature, store hash and retained run file
off the input loop. The closing seal names the exact verified prefix. Concurrent
Monkey appends remain signed and are included in subsequent checkpoints. An
external database commit during verification causes refusal. Explicit full
verification and export still validate the entire retained journal.

This is a trusted bootstrap process. Its journal accounts for invoked setup and
lifecycle commands, captured configuration, output, image and package evidence;
it is not a kernel trace of every file or network access made by Colima or its
dependencies. Individual build commands use the separately documented traced
container runner in [BUILD_RUNNER.md](BUILD_RUNNER.md).

## Qualification scope

`tests/test_build_environment.py` covers command interruption/output bounds,
global lease contention, inherited-pipe cleanup, evidence tampering, configuration
drift, owned-environment reuse, project-path exclusion and cleanup after an
unrelated shutdown failure. Its Colima adapter is a fixture and starts no VM.

`scripts/qualify_managed_builder.py` exercises the installed CLI with one real
environment per invocation. It requires a fresh IsolatedTester session check
and checks for active guests before launching. Its setup, restart and killed
foreground cases retain separate results and verify the guardian's evidence and
lease release. A failed case is retained as a failure, without an automatic
replacement launch. Current measured results belong in VALIDATION.md.

Automatic installation of Colima/Docker, Linux/Windows environment management,
general dependency acquisition, native UI behavior and real local-model task
quality remain separate release gates. Managed lifecycle qualification does not
establish those capabilities.
