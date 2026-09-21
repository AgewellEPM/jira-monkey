from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile

from .app import App
from .common import Refused, clone, require
from .conversation import fast, proposal

TICKET = {"source": "jira", "instance": "https://example.invalid", "key": "DEMO-123", "revision": "fixture-1",
          "title": "Explain the login report", "body": "The login sometimes fails. Draft a useful response requesting reproduction steps."}


class DemoModels:
    fixture = True

    def __init__(self, scenario="pass", delay=0.08):
        self.scenario, self.delay = scenario, delay
        self.calls = []
        self.started = asyncio.Event()

    def usage(self):
        return {"provider": "offline fixture", "requested_model": "fixture", "returned_model": "fixture",
                "input_tokens": None, "output_tokens": None, "cost": "unknown", "fixture": True}

    async def triage(self, c, data):
        self.calls.append("triage")
        await asyncio.sleep(self.delay)
        return {"goal": "Draft a useful response", "criteria": ["Ask for reproduction steps"], "unknowns": ["Reproduction steps"],
                "needs_human": False, "reason": "Fixture: useful clarification request can be drafted"}, self.usage()

    async def draft(self, c, data):
        self.calls.append("draft")
        self.started.set()
        await asyncio.sleep(self.delay * 3)
        return "Thanks for reporting the login problem. Please share reproduction steps, the exact error, and when it occurred. No fix has been verified yet.", self.usage()

    async def review(self, c, data):
        self.calls.append("review")
        await asyncio.sleep(self.delay)
        revise = self.scenario == "revise" or self.scenario == "revise-once" and self.calls.count("review") == 1
        return {"verdict": "REVISE" if revise else "PASS", "findings": [{"code": "MORE_DETAIL", "severity": "warning", "claim_or_excerpt": "Fixture requests a more precise question", "evidence_refs": [data["source_ref"]], "suggested_revision": "Ask for exact steps"}] if revise else [], "unresolved_questions": []}, self.usage()

    async def local(self, c, model, pin, instruction, data, shape=None, **kwargs):
        text = data["operator_input"]
        result = fast(text)
        if not result and "DEMO-123" in text and "draft" in text:
            result = proposal("command", "fetch_run", "DEMO-123")
        result = result or proposal("clarify", "clarify", arguments={"question": "Offline demo has scripted language only; use /help"})
        return {k: result[k] for k in ("operation", "target")} | {
            "after": result["arguments"].get("after", "")}, self.usage()


class DemoJira:
    fixture = True

    def __init__(self, c=None, scenario="pass"):
        self.c = c or {"site": TICKET["instance"]}
        self.scenario = scenario
        self.ticket = clone(TICKET)
        self.rows = []
        self.posts = 0
        self.reads = 0

    async def fetch(self, key):
        require(key == self.ticket["key"], "Offline demo only contains " + self.ticket["key"])
        return clone(self.ticket), "42"

    async def author(self):
        return "fixture-operator"

    async def post(self, key, payload):
        self.posts += 1
        row = {"id": str(10000+self.posts), "author": {"accountId": "fixture-operator"}, **clone(payload)}
        self.rows.append(row)
        if self.scenario in {"response-loss", "no-match"}:
            if self.scenario == "no-match":
                self.rows.clear()
            raise Refused("Fixture: Jira stored the comment but the acknowledgement was lost")
        return row, 201

    async def read(self, key, comment_id):
        self.reads += 1
        if self.scenario == "readback-error":
            raise Refused("Fixture: read-back unavailable")
        return clone(next(r for r in self.rows if r["id"] == comment_id))

    async def comments(self, key):
        self.reads += 1
        return clone(self.rows)


def demo_app(root, scenario="pass", delay=0.08):
    model = DemoModels(scenario, delay)
    jira = DemoJira(scenario=scenario)
    app = App(root, models=model, jira_factory=lambda c: jira, offline=True)
    app.db.configure({"provider": "ollama", "model": "fixture", "ollama_model": "fixture", "chat_model": "fixture", "site": TICKET["instance"]})
    return app, jira


async def demonstration(scenario="response-loss"):
    with tempfile.TemporaryDirectory(prefix="jira-monkey-demo-", dir=Path(tempfile.gettempdir()).resolve()) as folder:
        app, jira = demo_app(folder, scenario)
        try:
            result = await app.dispatch("fetch", key=TICKET["key"])
            jid = result["job_id"]
            await app.dispatch("run", target=jid)
            await app.models.started.wait()
            status_during = app.status()
            if scenario == "pause":
                await app.dispatch("pause", target=jid, after="review")
            await app.worker.task
            job = app.db.job(jid)
            states = [job["work_state"]]
            if job["work_state"] == "DRAFT_READY" and scenario not in {"revise", "revise-once"}:
                await app.dispatch("approve", target=jid, note="Explicit offline fixture approval")
                await app.dispatch("publish", target=jid, confirm=TICKET["key"])
                states.append(app.db.job(jid)["delivery_state"])
                if scenario in {"response-loss", "no-match", "readback-error"}:
                    await app.dispatch("reconcile", target=jid)
                    states.append(app.db.job(jid)["delivery_state"])
            return {"fixture": True, "network_calls": 0, "scenario": scenario, "status_during_draft": status_during,
                    "states": states, "post_count": jira.posts, "read_count": jira.reads,
                    "job": app.summary(app.db.job(jid)), "events": app.db.events(job_id=jid),
                    "note": "Offline fake adapters exercising the real core. This is not live Jira/model validation"}
        finally:
            await app.close()
