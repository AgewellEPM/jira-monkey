import asyncio
from pathlib import Path
import tempfile
import unittest

from monkey.cli import line
from monkey.common import Refused, ticket_snapshot
from monkey.demo import demo_app


class Requests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.app, self.jira = demo_app(self.folder.name, delay=.001)

    async def asyncTearDown(self):
        await self.app.close()
        self.folder.cleanup()

    async def settle(self, jid):
        async with asyncio.timeout(3):
            while self.app.db.job(jid)["work_state"] in {"QUEUED", "RUNNING"}:
                await asyncio.sleep(.01)

    async def test_direct_sprint_request_runs_without_jira(self):
        result = await line(self.app, "Draft a sprint ticket for a customer's password reset")
        jid = result["job_id"]
        await self.settle(jid)
        j = self.app.db.job(jid)
        self.assertEqual(j["source"], "local")
        self.assertEqual(j["work_state"], "DRAFT_READY")
        self.assertEqual(self.jira.reads + self.jira.posts, 0)
        self.assertIn("customer's", self.app.db.record("snapshots", j["snapshot_id"])["ticket"]["body"])

    async def test_other_sources_can_draft_but_never_use_jira_publish(self):
        for source in ("linear", "github", "zendesk", "custom_tracker"):
            ticket = {"source": source, "instance": "https://example.invalid", "key": "team/42", "revision": "1", "title": "Support request", "body": "Draft a useful clarification"}
            j = self.app.db.add(ticket, fixture=True)
            await self.app.dispatch("run", target=j["id"])
            await self.app.worker.task
            await self.app.dispatch("approve", target=j["id"], note="Reviewed locally")
            with self.assertRaises(Refused):
                await self.app.dispatch("publish", target=j["id"], confirm=j["key"])
        self.assertEqual(self.jira.posts, 0)

    async def test_new_requests_queue_while_first_is_running(self):
        first = await line(self.app, "/sprint Plan account recovery")
        second = await line(self.app, "/ticket Write a support reply")
        self.assertNotEqual(first["job_id"], second["job_id"])
        await self.settle(second["job_id"])
        self.assertEqual(self.app.db.job(first["job_id"])["work_state"], "DRAFT_READY")
        self.assertEqual(self.app.db.job(second["job_id"])["work_state"], "DRAFT_READY")

    async def test_local_request_cannot_enter_publish_confirmation(self):
        result = await line(self.app, "/request Draft a bug ticket")
        await self.settle(result["job_id"])
        j = self.app.db.job(result["job_id"])
        with self.assertRaises(Refused):
            self.app.publication_preview(j)
        with self.assertRaises(Refused):
            await self.app.dispatch("refresh", target=j["id"])
        self.assertIsNone(self.app.pending)

    async def test_jira_validation_remains_strict(self):
        with self.assertRaises(Refused):
            ticket_snapshot({"source": "jira", "instance": "https://example.invalid", "key": "team/42", "revision": "1", "title": "T", "body": "B"})

    async def test_invented_sprint_assignments_cannot_receive_passing_review(self):
        async def draft(*args):
            return "# Password reset\n- **Assigned to**: Backend team\n- **Estimate**: 3 days\n- **Priority**: Medium urgency", self.app.models.usage()
        self.app.models.draft = draft
        result = await self.app.dispatch("request", text="Draft a sprint ticket for password reset")
        await self.settle(result["job_id"])
        j = self.app.db.job(result["job_id"])
        review = self.app.db.record("reviews", j["review_id"])["result"]
        self.assertEqual(review["verdict"], "REVISE")
        self.assertEqual(j["work_state"], "WAITING_USER")
        self.assertEqual(j["attempt_count"], 3)
        with self.assertRaises(Refused):
            await self.app.dispatch("approve", target=j["id"], note="Should require correction")
