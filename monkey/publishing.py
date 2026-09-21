from __future__ import annotations

import asyncio
import re

from .adapters import RequestError
from .common import ACTIVE_DELIVERY, Refused, adf_text, clone, digest, identity, material_hash, now, require
from .scheduling import schedule_hash


def binding(job, draft, snapshot):
    require(draft["complete"] and draft["snapshot_id"] == snapshot["id"], "Candidate refers to a different source")
    require(digest(draft["payload"]) == draft["payload_hash"] and digest(draft["text"]) == draft["text_hash"], "Candidate was modified; new review/approval required")
    require(adf_text(draft["payload"]["body"]).rstrip("\n") == draft["text"].rstrip("\n"), "Candidate text and payload disagree")
    require(draft["payload"].get("visibility") == job["recipe"]["visibility"], "Visibility changed")
    return {"site": job["site"], "issue_id": job["issue_id"], "key": job["key"],
            "snapshot_id": snapshot["id"], "snapshot_hash": snapshot["content_hash"],
            "payload_hash": draft["payload_hash"], "visibility": job["recipe"]["visibility"], "policy_version": 1,
            "schedule_hash": schedule_hash(job)}


class Publisher:
    def __init__(self, db, jira_factory):
        self.db, self.jira_factory = db, jira_factory
        self.locks = {}

    def approve(self, job, note, command):
        require(job.get("schedule", {}).get("status") != "RETURNED", "New sprint dates are required before sign-off")
        require(job["delivery_state"] not in ACTIVE_DELIVERY, "Delivery is already in flight or recorded; reconcile")
        require(job["work_state"] in {"DRAFT_READY", "PAUSED", "WAITING_USER"}, "Candidate is not ready for approval")
        require(job["draft_id"] and job["review_id"], "A completed candidate and review are required")
        draft = self.db.record("drafts", job["draft_id"])
        review = self.db.record("reviews", job["review_id"])
        require(review["draft_hash"] == draft["payload_hash"] and review["result"]["verdict"] == "PASS", "Current candidate needs a passing review before approval")
        snapshot = self.db.record("snapshots", job["snapshot_id"])
        exact = binding(job, draft, snapshot)
        aid = identity("approval_")
        approval = {"id": aid, "binding": exact, "operator": command["operator_identity"], "note": note, "approved_at": now()}
        _, receipt = self.db.change(job["id"], job["version"], "candidate.approved",
            {"approval_id": aid, "delivery_state": "APPROVED"}, command=command,
            records=[("approvals", aid, approval)], payload={"message": "Exact candidate approved. No comment posted", "payload_hash": draft["payload_hash"]})
        return receipt

    def validate_approval(self, job):
        require(job["delivery_state"] == "APPROVED", "Current candidate is not approved, or delivery already started")
        draft = self.db.record("drafts", job["draft_id"])
        approval = self.db.record("approvals", job["approval_id"])
        snapshot = self.db.record("snapshots", job["snapshot_id"])
        require(approval["binding"] == binding(job, draft, snapshot), "Approval is stale; inspect and reapprove")
        review = self.db.record("reviews", job["review_id"])
        require(review["draft_hash"] == draft["payload_hash"] and review["snapshot_id"] == snapshot["id"] and review["result"]["verdict"] == "PASS", "Review is stale")
        return draft, approval, snapshot

    async def publish(self, job_id, confirmation, command, expected_hash=None):
        self.db.audit.verify()
        async with self.db.audit.run(job_id,'publish'):
            return await self._publish(job_id,confirmation,command,expected_hash)

    async def _publish(self, job_id, confirmation, command, expected_hash=None):
        async with self.locks.setdefault(job_id, asyncio.Lock()):
            job = self.db.job(job_id)
            require(self.db.record("snapshots", job["snapshot_id"])["ticket"]["source"] == "jira", "This is a local or imported request. Its draft is ready to copy; publishing is available only for captured Jira tickets")
            require(job["version"] == command["expected_job_version"], "Job changed since publication request; inspect and confirm again")
            require(confirmation == job["key"], "Confirm the exact issue key with --confirm")
            draft, approval, snapshot = self.validate_approval(job)
            require(not expected_hash or expected_hash == draft["payload_hash"], "Confirmation refers to a different candidate hash")
            jira = self.jira_factory(job["recipe"])
            require(jira.fixture == job["fixture"], "Fixture comments cannot use live Jira")
            require(jira.c["site"] == job["site"], "Jira site differs from approved origin")
            fresh, issue_id = await jira.fetch(job["key"])
            require(fresh["instance"] == job["site"] and fresh["key"] == job["key"], "Jira returned a different target")
            current = self.db.job(job_id)
            require(current["version"] == job["version"], "Job changed during preflight; confirm current candidate again")
            if material_hash(fresh) != snapshot["material_hash"] or issue_id != job["issue_id"]:
                self.db.change(job_id, job["version"], "approval.stale", {"delivery_state": "STALE", "approval_id": None},
                    payload={"message": "Ticket evidence or immutable identity changed; refresh and review before approval"})
                raise Refused("Ticket changed or imported identity is unverified; /refresh then review and reapprove")
            author = await jira.author()
            current = self.db.job(job_id)
            require(current["version"] == job["version"], "Job changed during author preflight")
            self.validate_approval(current)
            oid = draft["operation_id"]
            out = {"id": oid, "state": "POSTING", "version": 1, "job_id": job_id, "site": job["site"],
                   "key": job["key"], "issue_id": issue_id, "payload": clone(draft["payload"]), "payload_hash": draft["payload_hash"],
                   "approval_id": approval["id"], "author": author, "started_at": now(), "comment_id": None}
            job, _ = self.db.change(job_id, job["version"], "delivery.posting", {"outbox_id": oid, "delivery_state": "POSTING"},
                records=[("outbox", oid, out)], command=command, payload={"message": "Outbox committed; sending the approved comment once", "operation_id": oid})
            try:
                response, status = await jira.post(job["key"], out["payload"])
                require(status == 201 and type(response.get("id")) is str and re.fullmatch(r"[0-9]+", response["id"]), "Comment creation receipt is incomplete")
                out.update(comment_id=response["id"], receipt={"status": status, "id": response["id"], "received_at": now()})
                self.delivery(job_id, out, "POSTED_UNVERIFIED", "Jira confirmed comment creation; read-back pending")
            except asyncio.CancelledError:
                self.delivery(job_id, out, "POST_UNKNOWN", "Publication cancelled locally; remote outcome unknown. Reconcile before any further action")
                raise
            except Exception as exc:
                if self.db.failed:
                    raise
                known = isinstance(exc, RequestError) and exc.status in {400, 401, 403, 404, 413, 422, 429}
                self.delivery(job_id, out, "POST_REJECTED" if known else "POST_UNKNOWN",
                              str(exc) if isinstance(exc, Refused) else "Creation outcome unknown; inspect and reconcile")
                return self.status(job_id)
            await self.verify(job_id, out, jira)
            return self.status(job_id)

    def delivery(self, job_id, out, state, message):
        current = self.db.job(job_id)
        out.update(state=state, version=out.get("version", 0) + 1, updated_at=now(), reason=message)
        self.db.change(job_id, current["version"], "delivery." + state.lower(), {"delivery_state": state},
                       records=[("outbox", out["id"], out)], payload={"message": message, "operation_id": out["id"], "comment_id": out.get("comment_id")})

    def matches(self, out, row):
        visibility = row.get("visibility")
        if visibility is not None:
            visibility = {k: visibility.get(k) for k in ("type", "value")}
        return (type(row.get("id")) is str and row.get("author", {}).get("accountId") == out["author"]
                and row.get("body") == out["payload"]["body"]
                and visibility == out["payload"].get("visibility")
                and any(p.get("key") == "jira-monkey.operation" and p.get("value") == {"id": out["id"], "schema_version": 1} for p in row.get("properties", [])))

    async def verify(self, job_id, out, jira):
        try:
            row = await jira.read(out["key"], out["comment_id"])
        except asyncio.CancelledError:
            raise  # Creation receipt is already durable. Never relabel it unknown.
        except Exception as exc:
            self.delivery(job_id, out, "POSTED_UNVERIFIED", "Creation confirmed; read-back unavailable: " + (str(exc) if isinstance(exc, Refused) else type(exc).__name__))
            return
        good = self.matches(out, row) and row.get("id") == out["comment_id"]
        out["verification"] = {"checked_at": now(), "matches": good, "comment_id": row.get("id"), "observed_hash": digest(row.get("body"))}
        self.delivery(job_id, out, "POSTED_VERIFIED" if good else "POSTED_MISMATCH", "Approved comment read back and verified" if good else "Read-back differs from approved payload or author; operator attention required")

    async def reconcile(self, job_id):
        async with self.locks.setdefault(job_id, asyncio.Lock()):
            job = self.db.job(job_id)
            require(job["delivery_state"] in {"POST_UNKNOWN", "POSTED_UNVERIFIED", "POSTED_MISMATCH"}, "No unresolved delivery to reconcile")
            out = self.db.record("outbox", job["outbox_id"])
            jira = self.jira_factory(job["recipe"])
            require(jira.fixture == job["fixture"] and jira.c["site"] == job["site"], "Reconciliation target/adapter mismatch")
            if not out.get("legacy"):
                current_ticket, issue_id = await jira.fetch(job["key"])
                require(issue_id == out["issue_id"] and current_ticket["instance"] == out["site"] and current_ticket["key"] == out["key"], "Reconciliation issue identity changed; delivery remains blocked")
            if out.get("legacy"):
                # Preview had no bound author/property. Preserve uncertainty even if
                # matching text is visible; don't manufacture stronger evidence.
                rows = await jira.comments(job["key"])
                out["reconciliation"] = {"at": now(), "visible_text_matches": sum(adf_text(r.get("body")).rstrip("\n") == out["body"] for r in rows), "complete": True, "legacy_author_unknown": True}
                self.delivery(job_id, out, job["delivery_state"], "Legacy text inspected; exact author/correlation evidence unavailable. No resend")
                return self.status(job_id)
            if out.get("comment_id"):
                await self.verify(job_id, out, jira)
                return self.status(job_id)
            try:
                rows = await jira.comments(job["key"])
            except (Refused, KeyError, TypeError) as exc:
                out["reconciliation"] = {"at": now(), "complete": False, "reason": str(exc) if isinstance(exc, Refused) else type(exc).__name__}
                self.delivery(job_id, out, "POST_UNKNOWN", "Reconciliation scan incomplete; no resend")
                return self.status(job_id)
            marked = [r for r in rows if any(p.get("key") == "jira-monkey.operation" and p.get("value", {}).get("id") == out["id"] for p in r.get("properties", []))]
            matches = [r for r in marked if self.matches(out, r)]
            out["reconciliation"] = {"at": now(), "complete": True, "marked_count": len(marked), "strong_matches": len(matches), "scanned": len(rows)}
            if len(marked) == len(matches) == 1:
                out["comment_id"] = matches[0]["id"]
                self.delivery(job_id, out, "POSTED_UNVERIFIED", "One matching operation, target, payload and author found")
                await self.verify(job_id, out, jira)
            else:
                self.delivery(job_id, out, "POST_UNKNOWN", "No unique strong match; delivery remains uncertain. No resend")
            return self.status(job_id)

    def status(self, job_id):
        j = self.db.job(job_id)
        return {"job_id": job_id, "delivery_state": j["delivery_state"], "message": self.db.record("outbox", j["outbox_id"])["reason"]}
