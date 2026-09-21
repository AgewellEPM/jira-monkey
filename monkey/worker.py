from __future__ import annotations

import asyncio
import re
import time

from .adapters import RequestError
from .ollama_stream import IncompleteStream
from .common import REVIEW, Refused, clone, digest, identity, now, require, validate


def comment_payload(text, operation_id, visibility):
    body = {"type": "doc", "version": 1, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": text}]}]}
    payload = {"body": body, "properties": [{"key": "jira-monkey.operation", "value": {"id": operation_id, "schema_version": 1}}]}
    if visibility:
        payload["visibility"] = clone(visibility)
    return payload


class StaleResult(Refused):
    pass


class Worker:
    def __init__(self, db, models):
        self.db, self.models = db, models
        self.active = None
        self.continuous = False
        self.closed = False
        self.task = None
        self.blocked = lambda: False

    def eligible(self):
        return [j for j in self.db.jobs() if not j.get('work_type') and not j.get('windows_binding_id') and not j.get("contract_id") and j["work_state"] == "QUEUED" and j.get("schedule", {}).get("status") != "RETURNED" and j["delivery_state"] not in {"POST_UNKNOWN", "POSTING", "POSTED_UNVERIFIED", "POSTED_VERIFIED", "POSTED_MISMATCH"}]

    def start(self, job_id=None, continuous=False):
        require(not self.closed, "Worker is shutting down")
        require(not self.task or self.task.done(), "One worker is already active; use /pause or /cancel")
        jobs = [self.db.job(job_id)] if job_id else self.eligible()
        require(jobs or continuous, "No eligible queued work")
        if jobs:
            require(not jobs[0].get('work_type'),'General work continues through /agent-continue')
            require(not jobs[0].get('windows_binding_id'), 'Windows work uses /windows-plan and exact tool approval')
            require(jobs[0]["work_state"] == "QUEUED", "Only queued work can run; use /resume, /retry or /recover")
        self.continuous = continuous
        self.task = asyncio.create_task(self.loop(jobs[0]["id"] if jobs else None), name="monkey-worker")
        return {"message": "Queue work enabled; no publishing" if continuous else "One candidate attempt requested", "job_id": jobs[0]["id"] if jobs else None}

    async def loop(self, first):
        candidate = first
        try:
            while not self.closed:
                if self.blocked():
                    await asyncio.sleep(.1)
                    continue
                if not candidate:
                    jobs = self.eligible()
                    if not jobs:
                        if not self.continuous:
                            return
                        await asyncio.sleep(0.1)
                        continue
                    candidate = jobs[0]["id"]
                self.active = candidate
                await self.run(candidate)
                candidate = None
                self.active = None
                if not self.continuous:
                    return
        finally:
            self.active = None

    def current(self, job_id, token, snapshot_id):
        j = self.db.job(job_id)
        if j["operation_token"] != token or j["snapshot_id"] != snapshot_id or j["work_state"] != "RUNNING":
            raise StaleResult("Late result belongs to superseded work")
        return j

    def stage(self, job, name):
        token = identity("call_")
        job, _ = self.db.change(job["id"], job["version"], name.lower() + ".started",
            {"stage": name, "work_state": "RUNNING", "operation_token": token, "stage_started_at": now()},
            payload={"stage": name, "attempt": job["attempt_count"], "message": name.title() + " started; waiting for provider"})
        return job, token

    async def call(self, job, token, stage, packet):
        snapshot_id = job["snapshot_id"]
        fn = getattr(self.models, stage.lower())
        while True:
            job = self.current(job["id"], token, snapshot_id)
            require(job["call_count"] < job["limits"]["calls"], "Total provider-call budget exhausted")
            cid = identity("provider_")
            row = {"id": cid, "scope": stage.lower(), "attempt": job["attempt_count"], "started_at": now(), "state": "RUNNING", "cost": "unknown"}
            job, _ = self.db.change(job["id"], job["version"], "provider.reserved",
                {"call_count": job["call_count"] + 1}, records=[("provider_calls", cid, row)],
                payload={"scope": stage.lower(), "call_id": cid})
            started = time.monotonic()
            try:
                result, usage = await fn(job["recipe"], packet)
                row.update(usage, state="COMPLETED", finished_at=now())
                try:
                    current = self.current(job["id"], token, snapshot_id)
                except StaleResult:
                    current = self.db.job(job["id"])
                    self.db.change(current["id"], current["version"], "provider.stale_result", {},
                        records=[("provider_calls", cid, row), ("artifacts", identity("stale_"), {"kind": "stale_result", "value": result, "snapshot_id": snapshot_id})],
                        payload={"message": "Late result retained as stale; current candidate unchanged"})
                    raise
                self.db.change(current["id"], current["version"], "provider.completed", {}, records=[("provider_calls", cid, row)], payload={"scope": stage.lower(), "call_id": cid})
                return result
            except asyncio.CancelledError:
                row.update(state="INTERRUPTED", finished_at=now(), latency_ms=(time.monotonic()-started)*1000)
                current = self.db.job(job["id"])
                self.db.change(current["id"], current["version"], "provider.interrupted", {}, records=[("provider_calls", cid, row)], payload={"message": "Request cancelled locally; no completed artifact"})
                raise
            except StaleResult:
                raise
            except Exception as exc:
                row.update(state="FAILED", error=str(exc) if isinstance(exc, Refused) else type(exc).__name__, finished_at=now(), latency_ms=(time.monotonic()-started)*1000)
                current = self.current(job["id"], token, snapshot_id)
                self.db.change(current["id"], current["version"], "provider.failed", {}, records=[("provider_calls", cid, row)], payload={"message": row["error"], "call_id": cid})
                job = self.current(job["id"], token, snapshot_id)
                # Only classified infrastructure failures retry. Invalid semantic/schema
                # output stops this attempt; operator retry still shares the job cap.
                can_retry = isinstance(exc, (RequestError, IncompleteStream)) and getattr(exc, 'status', None) in {None, 429, 500, 502, 503, 504}
                if not can_retry or job["retry_count"] >= job["limits"]["retries"] or job["call_count"] >= job["limits"]["calls"]:
                    raise
                self.db.change(job["id"], job["version"], "provider.retry_requested", {"retry_count": job["retry_count"] + 1}, payload={"message": "Bounded infrastructure retry reserved"})
                await asyncio.sleep(0.1)

    def checkpoint(self, job_id, finished, next_stage=None, final_state=None, reason=""):
        j = self.db.job(job_id)
        pause = j["pause_after"]
        order = {"TRIAGE": 0, "DRAFT": 1, "REVIEW": 2}
        apply = pause and (pause == "now" or order[finished] >= order[pause.upper()] or final_state)
        retry = j.get("retry_requested") and (finished == "REVIEW" or final_state)
        if retry:
            final_state = "QUEUED" if j["attempt_count"] < j["limits"]["attempts"] and j["call_count"] < j["limits"]["calls"] else "WAITING_USER"
            reason = "Operator feedback retained; bounded retry queued" if final_state == "QUEUED" else "Original budget exhausted"
        state = "PAUSED" if apply else final_state or "RUNNING"
        update = {"work_state": state, "stage": next_stage, "operation_token": None, "reason": reason}
        if retry:
            update.update(retry_requested=False, approval_id=None)
        if apply:
            update.update(pause_after=None, resume_stage=next_stage, resume_state=final_state, resume_continuous=self.continuous)
            self.continuous = False
        j, _ = self.db.change(j["id"], j["version"], "pause.applied" if apply else "stage.boundary", update,
            payload={"message": finished.title() + " completed; job is now paused" if apply else reason or finished.title() + " completed"})
        return j, bool(apply or final_state)

    async def run(self, job_id):
        async with self.db.audit.run(job_id,'response_worker'):
            return await self._run(job_id)

    async def _run(self, job_id):
        try:
            j = self.db.job(job_id)
            require(j["work_state"] == "QUEUED", "Job is not queued")
            require(j.get("schedule", {}).get("status") != "RETURNED", "Ticket was returned for new dates; schedule it before working")
            require(j["fixture"] == self.models.fixture, "Fixture work and live adapters cannot be mixed")
            resume = j["resume_stage"]
            if not resume:
                require(j["attempt_count"] < j["limits"]["attempts"], "Candidate attempt budget exhausted")
                require(j["call_count"] < j["limits"]["calls"], "Total provider-call budget exhausted")
                number = j["attempt_count"] + 1
                attempt = {"id": f"{job_id}:{number}", "number": number, "snapshot_id": j["snapshot_id"], "started_at": now(), "state": "RUNNING"}
                j, _ = self.db.change(j["id"], j["version"], "attempt.reserved",
                    {"attempt_count": number, "work_state": "RUNNING", "draft_id": None, "review_id": None, "approval_id": None,
                     "delivery_state": "STALE" if j["delivery_state"] in {"DRAFT", "APPROVED", "STALE"} else "NONE"},
                    records=[("attempts", attempt["id"], attempt)], payload={"attempt": number, "message": f"Candidate attempt {number}/{j['limits']['attempts']} reserved"})
            else:
                j, _ = self.db.change(j["id"], j["version"], "attempt.resumed", {"work_state": "RUNNING", "resume_stage": None}, payload={"message": "Resuming retained stage boundary"})
            snapshot = self.db.record("snapshots", j["snapshot_id"])
            refs = [snapshot["id"]]
            packet = {"ticket": snapshot["ticket"], "source_ref": snapshot["id"], "evidence_refs": refs,
                      "operator_feedback": j["feedback"], "capabilities": ["draft_response"], "unknowns": ["No code execution or test evidence"]}
            if j.get("schedule"):
                packet["planned_schedule"] = j["schedule"]
            if snapshot["ticket"]["source"] == "local":
                packet.update(work_product="Proposed ticket, plan, criteria or reply as requested; no implementation claims",
                              capabilities=["draft_ticket", "draft_plan", "draft_acceptance_criteria", "draft_response"], unknowns=[])
            if resume in {None, "TRIAGE"} and not j["triage"]:
                j, token = self.stage(j, "TRIAGE")
                triage = await self.call(j, token, "TRIAGE", packet)
                j = self.current(job_id, token, snapshot["id"])
                j, _ = self.db.change(job_id, j["version"], "triage.completed", {"triage": triage}, payload={"message": "Triage saved", "source_ref": snapshot["id"]})
                j, stop = self.checkpoint(job_id, "TRIAGE", None if triage["needs_human"] else "DRAFT",
                    "WAITING_USER" if triage["needs_human"] else None, triage["reason"])
                if stop:
                    return
            if resume != "REVIEW":
                packet["triage"] = j["triage"]
                previous = self.db.records("reviews", job_id)
                if previous:
                    packet["previous_review"] = previous[-1]["result"]
                j, token = self.stage(j, "DRAFT")
                text = await self.call(j, token, "DRAFT", packet)
                require(type(text) is str and 0 < len(text.encode()) <= 30000, "Incomplete or oversized candidate")
                j = self.current(job_id, token, snapshot["id"])
                draft_id, operation_id = identity("draft_"), identity("op_")
                payload = comment_payload(text, operation_id, j["recipe"]["visibility"])
                draft = {"id": draft_id, "snapshot_id": snapshot["id"], "number": j["attempt_count"], "text": text,
                         "payload": payload, "payload_hash": digest(payload), "text_hash": digest(text),
                         "operation_id": operation_id, "created_at": now(), "complete": True}
                j, _ = self.db.change(job_id, j["version"], "draft.completed", {"draft_id": draft_id, "delivery_state": "DRAFT"},
                    records=[("drafts", draft_id, draft)], payload={"draft_id": draft_id, "hash": draft["payload_hash"], "message": "Draft saved; review pending"})
                j, stop = self.checkpoint(job_id, "DRAFT", "REVIEW")
                if stop:
                    return
            draft = self.db.record("drafts", j["draft_id"])
            j, token = self.stage(j, "REVIEW")
            review = await self.call(j, token, "REVIEW", {**packet, "draft": draft["text"], "evidence_refs": [*refs, draft["id"]]})
            validate(review, REVIEW)
            require(all(f["evidence_refs"] and set(f["evidence_refs"]) <= {*refs, draft["id"]} for f in review["findings"]), "Review contains invalid evidence references")
            if snapshot["ticket"]["source"] == "local":
                evidence_text = (snapshot["ticket"]["title"] + "\n" + snapshot["ticket"]["body"]).lower()
                for label, value in re.findall(r"(?im)^\s*(?:[-*]\s*)?(?:\*\*)?(assigned to|assignee|estimate|priority)(?:\*\*)?\s*:(?:\*\*)?\s*(.+)$", draft["text"]):
                    value = value.strip().strip("* ")
                    unknown = re.search(r"\b(?:tbd|unknown|unassigned|not (?:specified|provided|assigned)|to be (?:decided|confirmed|estimated)|proposed|suggested)\b", value, re.I)
                    if value.lower() not in evidence_text and not unknown:
                        if review["verdict"] == "PASS":
                            review["verdict"] = "REVISE"
                        review["findings"].append({"code": "UNSUPPORTED_PLANNING_COMMITMENT", "severity": "error",
                            "claim_or_excerpt": label + ": " + value, "evidence_refs": [snapshot["id"], draft["id"]],
                            "suggested_revision": "Remove this unsupplied assignment, estimate or priority, or label it TBD. Do not invent a project commitment."})
            if re.search(r"\b(?:I|we) (?:have )?(?:fixed|changed|modified|tested|ran tests)|\b(?:tests (?:have )?passed|bug (?:is|has been) fixed)\b", draft["text"], re.I):
                review["verdict"] = "NEEDS_INPUT"
                review["findings"].append({"code": "UNSUPPORTED_EXECUTION_CLAIM", "severity": "error", "claim_or_excerpt": "Candidate describes execution without evidence", "evidence_refs": [draft["id"]], "suggested_revision": "Describe recommendations and work still required"})
            j = self.current(job_id, token, snapshot["id"])
            review_id = identity("review_")
            artifact = {"id": review_id, "draft_id": draft["id"], "draft_hash": draft["payload_hash"], "snapshot_id": snapshot["id"], "result": review, "created_at": now()}
            attempt = self.db.record("attempts", f"{job_id}:{j['attempt_count']}")
            attempt.update(state="COMPLETED", finished_at=now(), draft_id=draft["id"], review_id=review_id)
            j, _ = self.db.change(job_id, j["version"], "review.completed", {"review_id": review_id},
                records=[("reviews", review_id, artifact), ("attempts", attempt["id"], attempt)],
                payload={"verdict": review["verdict"], "review_id": review_id, "message": "Review complete: " + review["verdict"]})
            state = {"PASS": "DRAFT_READY", "REVISE": "QUEUED", "NEEDS_INPUT": "WAITING_USER"}[review["verdict"]]
            reason = {"PASS": "Draft ready for operator review; external changes: none", "REVISE": "Review requested another bounded candidate", "NEEDS_INPUT": "Review requires operator input"}[review["verdict"]]
            if state == "QUEUED" and (j["attempt_count"] >= j["limits"]["attempts"] or j["call_count"] >= j["limits"]["calls"]):
                state, reason = "WAITING_USER", "Original candidate or provider-call budget exhausted"
            self.checkpoint(job_id, "REVIEW", final_state=state, reason=reason)
        except StaleResult:
            return
        except asyncio.CancelledError:
            j = self.db.job(job_id)
            if j["work_state"] == "RUNNING":
                self.db.change(job_id, j["version"], "attempt.interrupted", {"work_state": "INTERRUPTED", "operation_token": None}, payload={"message": "Foreground execution stopped; incomplete output is unusable"})
            raise
        except Exception as exc:
            if self.db.failed:
                self.continuous = False
                raise
            j = self.db.job(job_id)
            if j["work_state"] not in {"CANCELLED", "REJECTED", "INTERRUPTED"}:
                self.db.change(job_id, j["version"], "attempt.failed", {"work_state": "FAILED", "operation_token": None,
                    "reason": str(exc) if isinstance(exc, Refused) else type(exc).__name__}, payload={"message": str(exc) if isinstance(exc, Refused) else type(exc).__name__})

    async def stop(self):
        self.closed, self.continuous = True, False
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, 3)
            except (asyncio.CancelledError, TimeoutError):
                pass
        elif self.task:
            await asyncio.gather(self.task, return_exceptions=True)
