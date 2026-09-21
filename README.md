<div align="center">

# 🍌 Monkey

### A foreground terminal worker with local models and signed work evidence.

Point it at a ticket, a coding task, or a research question. It plans, works in a
real terminal, and hands back **signed evidence** of what it actually did — with your
own local model, on your own machine.

![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![Local models](https://img.shields.io/badge/models-local%20by%20default-79c0a0)
![Status](https://img.shields.io/badge/status-0.7.0.dev%20%C2%B7%20pilot-e0a800)
![Platform](https://img.shields.io/badge/platform-macOS%20%C2%B7%20Linux-555)

</div>

---

## Install — straight from GitHub

**One line** (recommended — installs the `Monkey` command onto your machine):

```bash
curl -fsSL https://raw.githubusercontent.com/AgewellEPM/jira-monkey/main/install.sh | bash
```

**With [pipx](https://pipx.pypa.io)** (clean, isolated):

```bash
pipx install git+https://github.com/AgewellEPM/jira-monkey.git
```

**With pip:**

```bash
pip install git+https://github.com/AgewellEPM/jira-monkey.git
```

Then just run it:

```bash
Monkey            # start the worker
Monkey --help     # every command
```

> Requires **Python 3.11+**. The installer finds a suitable interpreter, prefers `pipx`,
> and falls back to `pip --user`.

---

## What it does

Monkey is not a chatbot. It's a **worker** that runs in the foreground of a real
terminal, does the job, and proves it.

- 🧠 **Local by default** — reasoning runs on your own model (Ollama and friends); nothing
  is sent to a cloud model unless you opt in.
- 🧾 **Signed work evidence** — every run produces a tamper-evident record of what it did:
  the plan, the steps, the outputs. Claimed work you can actually check.
- 🖥️ **Real terminal, real jobs** — coding, research, tickets and requests, executed as
  bounded steps — not a wall of suggestions.
- 📦 **Offline build runner** — a captured, offline container environment for real
  multiprocess builds, with retained syscall evidence.
- 🍌 **Terminal + web dashboard** — a banana-themed console, plus an optional local web
  workspace that shares the same foreground session.
- 🔌 **Connectors, gated** — connect the services you use through an explicit, auditable
  routing layer.

---

## Quick tour

```bash
Monkey repl                 # interactive worker
Monkey mission "…"          # give it a mission
Monkey dashboard            # open the local web workspace for this session
Monkey services             # list connected services
Monkey audit-verify         # verify the signed evidence of past runs
Monkey builder              # pick a captured offline build environment
Monkey tools                # list available tools
```

Full command reference: [`docs/COMMANDS.md`](docs/COMMANDS.md).

---

## Honest status

Monkey is a **development candidate (0.7.0.dev)** — the installed command tracks
`0.6.0+dashboard.1`. It has passed specific macOS and Linux ARM64 CLI/state checks and a
set of Windows file/state checks; **Windows console qualification and worldwide-deployment
readiness are not claimed.** See the real gates and test posture in
[`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md), the build-runner contract in
[`docs/BUILD_RUNNER.md`](docs/BUILD_RUNNER.md), and the full development notes in
[`docs/DEVELOPMENT_NOTES.md`](docs/DEVELOPMENT_NOTES.md).

---

<div align="center">

**Perslis** · a one-human company; the rest of the team is software.
[perslis.com](https://www.perslis.com) · [Task Monkey on the site](https://www.perslis.com/jira-monkey.html)

</div>
