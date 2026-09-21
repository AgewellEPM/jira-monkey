import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jira_monkey as legacy
from monkey.adapters import LocalQueue, RequestError
from monkey.app import App
from monkey.common import Refused, clone, digest
from monkey.database import Database
from monkey.demo import TICKET, demo_app


class RecoveryAndControl(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.app, self.jira = demo_app(self.temp.name, delay=0.01)
        self.jid = (await self.app.dispatch("fetch", key=TICKET["key"]))["job_id"]

    async def asyncTearDown(self):
        await self.app.close()
        self.temp.cleanup()

    async def ready(self):
        await self.app.dispatch("run", target=self.jid)
        await self.app.worker.task

    async def test_owner_lock_excludes_second_process_instance(self):
        with self.assertRaises(Refused):
            Database(self.temp.name)

    async def test_pause_after_draft_resume_reviews_same_candidate(self):
        await self.app.dispatch("run", target=self.jid)
        await self.app.models.started.wait()
        await self.app.dispatch("pause", target=self.jid, after="draft")
        await self.app.worker.task
        j = self.app.db.job(self.jid)
        self.assertEqual(j["resume_stage"], "REVIEW")
        did = j["draft_id"]
        await self.app.dispatch("resume", target=self.jid)
        await self.app.worker.task
        j = self.app.db.job(self.jid)
        self.assertEqual(j["attempt_count"], 1)
        self.assertEqual(j["draft_id"], did)
        self.assertEqual(j["work_state"], "DRAFT_READY")

    async def test_active_revision_direction_retained(self):
        await self.app.dispatch("run", target=self.jid)
        await self.app.models.started.wait()
        receipt = await self.app.dispatch("retry", target=self.jid, note="Ask for exact reproduction steps")
        self.assertIn("still running", receipt["message"])
        await self.app.worker.task
        j = self.app.db.job(self.jid)
        self.assertEqual(j["work_state"], "QUEUED")
        self.assertEqual(j["feedback"], ["Ask for exact reproduction steps"])
        self.assertEqual(j["attempt_count"], 1)

    async def test_pause_stops_queue_before_another_job(self):
        second = self.app.db.add({**TICKET, "key": "DEMO-148"}, fixture=True)
        await self.app.dispatch("work")
        await self.app.models.started.wait()
        await self.app.dispatch("pause", target=self.jid, after="review")
        await self.app.worker.task
        self.assertEqual(self.app.db.job(second["id"])["attempt_count"], 0)
        self.assertFalse(self.app.worker.continuous)

    async def test_natural_draft_phrase_is_not_misparsed_as_bare_show(self):
        from monkey.cli import line
        await self.ready()
        result = await line(self.app, "show teh draft")
        self.assertIn("candidate", result)

    async def test_ambiguous_status_question_requires_target(self):
        self.app.db.add({**TICKET, "key": "DEMO-148"}, fixture=True)
        self.app.focus = None
        with self.assertRaises(Refused):
            await self.app.conversation.handle("What is that job doing?")

    async def test_natural_publish_confirmation_bound_to_hash(self):
        await self.ready()
        await self.app.dispatch("approve", target=self.jid, note="Reviewed")
        preview = self.app.publication_preview(self.app.db.job(self.jid))
        self.assertEqual(self.jira.posts, 0)
        with self.assertRaises(Refused):
            await self.app.confirm("yes", TICKET["key"])
        token = self.app.pending["token"]
        await self.app.dispatch("retry", target=self.jid, note="Change it")
        with self.assertRaises(Refused):
            await self.app.confirm(token, TICKET["key"])
        self.assertEqual(self.jira.posts, 0)

    async def test_actual_overlapping_publish_requests(self):
        await self.ready()
        await self.app.dispatch("approve", target=self.jid, note="Reviewed")
        original = self.jira.post
        async def delayed(*args):
            await asyncio.sleep(.03)
            return await original(*args)
        self.jira.post = delayed
        sends = [self.app.dispatch("publish", target=self.jid, confirm=TICKET["key"]) for _ in range(2)]
        results = await asyncio.gather(*sends, return_exceptions=True)
        self.assertEqual(self.jira.posts, 1)
        self.assertEqual(sum(isinstance(r, Refused) for r in results), 1)

    async def test_provider_failures_retry_within_shared_budget(self):
        async def failed(*args):
            raise RequestError("Fixture rate limit", 503)
        self.app.models.draft = failed
        await self.ready()
        j = self.app.db.job(self.jid)
        self.assertEqual(j["retry_count"], 2)
        self.assertEqual(j["call_count"], 4)
        self.assertEqual(j["work_state"], "FAILED")
        self.assertEqual(j["recipe"]["provider"], "ollama")

    async def test_source_timestamp_only_does_not_invalidate(self):
        await self.ready()
        await self.app.dispatch("approve", target=self.jid, note="Reviewed")
        self.jira.ticket["revision"] = "timestamp-only-update"
        await self.app.dispatch("publish", target=self.jid, confirm=TICKET["key"])
        self.assertEqual(self.app.db.job(self.jid)["delivery_state"], "POSTED_VERIFIED")

    async def test_import_identity_can_be_verified_without_losing_draft(self):
        j = self.app.db.add({**TICKET, "revision": "imported"}, fixture=True)
        await self.app.dispatch("run", target=j["id"])
        await self.app.worker.task
        before = self.app.db.job(j["id"])
        await self.app.dispatch("refresh", target=j["id"])
        after = self.app.db.job(j["id"])
        self.assertEqual(after["issue_id"], "42")
        self.assertEqual(before["draft_id"], after["draft_id"])
        self.assertEqual(after["attempt_count"], 1)
        await self.app.dispatch("approve", target=j["id"], note="Verified target")
        await self.app.dispatch("publish", target=j["id"], confirm=TICKET["key"])
        self.assertEqual(self.jira.posts, 1)

    async def test_operator_priority_at_local_boundary(self):
        queue = LocalQueue()
        order = []
        release = asyncio.Event()
        started = asyncio.Event()
        async def owner():
            async with queue.slot(label="first"):
                started.set()
                await release.wait()
        async def waiting(operator):
            async with queue.slot(operator=operator, label="chat" if operator else "worker"):
                order.append("chat" if operator else "worker")
        first = asyncio.create_task(owner())
        await started.wait()
        worker = asyncio.create_task(waiting(False))
        await asyncio.sleep(0)
        chat = asyncio.create_task(waiting(True))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, worker, chat)
        self.assertEqual(order, ["chat", "worker"])

    async def test_preview_migration_preserves_completed_evidence_and_counters(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            old = legacy.Store(root)
            config = {"provider": "openai", "model": "fixture", "ollama_model": "fixture",
                      "site": TICKET["instance"], "email": "", "review_policy": "human", "max_attempts": 3}
            old.write("config.json", config)
            job = old.add(TICKET, fixture=True)
            models = legacy.DemoModels()
            legacy.run_job(old, job, models)
            legacy.run_job(old, job, models)
            old_text = job["attempts"][-1]["result"]["text"]
            db = Database(root)
            try:
                new = db.job(job["id"])
                self.assertEqual(new["attempt_count"], 2)
                self.assertEqual(new["work_state"], "DRAFT_READY")
                self.assertIsNone(new["approval_id"])
                self.assertEqual(db.record("drafts", new["draft_id"])["text"], old_text)
                self.assertTrue(list(Path(root).glob("backup-v0-*")))
                self.assertTrue((Path(root) / ("job-" + job["id"] + ".json")).exists())
            finally:
                db.close()
