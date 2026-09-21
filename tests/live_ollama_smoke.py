"""Bounded local-model smoke only: no cloud credential access, no Jira writes."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jira_monkey as m

c = {"provider": "openai", "model": "not-called", "ollama_model": "qwen2.5-coder:7b",
     "site": "", "email": "", "review_policy": "human", "max_attempts": 3}
ticket = {"source": "jira", "instance": "https://example.invalid", "key": "DEMO-1",
          "revision": "synthetic-1", "title": "Write a release note for a keyboard shortcut",
          "body": "Draft one sentence stating that Command-Shift-J opens Jira Monkey. This is a writing task only; no code or execution is requested."}
models = m.Models(env={})
started = time.monotonic()
report = {"synthetic_ticket": True, "live_ollama": True, "cloud_calls": 0, "jira_calls": 0,
          "ollama_model": c["ollama_model"], "accepted": False}
try:
    report["triage"] = models.triage(c, ticket)
    report["review"] = models.review(c, ticket, {"triage": report["triage"], "result": {
        "text": "Press Command-Shift-J to open Jira Monkey.", "verified_execution": False,
        "provider": "synthetic-author"}})
    report["protocol_pass"] = True
except Exception as exc:
    report["protocol_pass"] = False
    report["error"] = str(exc) if isinstance(exc, m.Refused) else type(exc).__name__
report["seconds"] = round(time.monotonic() - started, 2)
Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
sys.exit(0 if report["protocol_pass"] else 1)
