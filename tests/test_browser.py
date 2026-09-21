import asyncio
import datetime as dt
import tempfile
import unittest

from prompt_toolkit import PromptSession
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from monkey.browser import Browser
from monkey.common import Refused
from monkey.demo import TICKET, demo_app


class TicketBrowser(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.app, self.jira = demo_app(self.folder.name, delay=.001)
        self.browser = Browser(self.app)

    async def asyncTearDown(self):
        await self.app.close()
        self.folder.cleanup()

    async def until(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.01)

    async def local_ready(self):
        result = await self.app.dispatch("request", text="Draft a sprint ticket for password reset")
        jid = result["job_id"]
        await self.until(lambda: self.app.db.job(jid)["work_state"] == "DRAFT_READY")
        return jid

    async def test_signoff_moves_exact_local_draft_into_calendar(self):
        jid = await self.local_ready()
        current, finished, past = self.browser.groups()
        self.assertEqual([j["id"] for j in current], [jid])
        self.assertEqual(len(finished), 1)
        self.assertEqual(past, [])
        self.browser.section = 1
        await self.browser.activate()
        await self.browser.activate()
        for _ in range(3):
            self.browser.slide(1)
        await self.browser.activate()
        current, finished, past = self.browser.groups()
        self.assertEqual(current, [])
        self.assertEqual([j["id"] for j in past], [jid])
        self.assertIsNotNone(self.browser.date_of(past[0]))
        self.assertEqual(self.jira.posts, 0)

    async def test_approved_jira_draft_remains_current_until_verified(self):
        jid = (await self.app.dispatch("fetch", key=TICKET["key"]))["job_id"]
        await self.app.dispatch("run", target=jid)
        await self.app.worker.task
        await self.app.dispatch("approve", target=jid, note="Reviewed")
        self.assertEqual(self.browser.groups()[2], [])
        await self.app.dispatch("publish", target=jid, confirm=TICKET["key"])
        self.assertEqual(len(self.browser.groups()[2]), 1)

    async def test_changed_candidate_requires_reopening_signoff(self):
        jid = await self.local_ready()
        self.browser.section = 1
        await self.browser.activate()
        await self.browser.activate()
        for _ in range(3):
            self.browser.slide(1)
        await self.app.dispatch("retry", target=jid, note="Change the scope")
        with self.assertRaises(Refused):
            await self.browser.activate()
        self.assertIsNone(self.app.db.job(jid)["approval_id"])

    async def test_calendar_selects_saved_day_and_leap_months(self):
        jid = await self.local_ready()
        await self.app.dispatch("approve", target=jid, note="Reviewed locally")
        self.browser.section, self.browser.pane = 3, "calendar"
        self.browser.day = self.browser.date_of(self.app.db.job(jid))
        await self.browser.activate()
        self.assertEqual([j["id"] for j in self.browser.rows()], [jid])
        self.browser.day = dt.date(2024, 1, 31)
        self.browser.month(1)
        self.assertEqual(self.browser.day, dt.date(2024, 2, 29))
        self.browser.month(-1)
        self.assertEqual(self.browser.day, dt.date(2024, 1, 29))

    async def test_real_prompt_key_bindings_browse_without_mutation(self):
        jid = await self.local_ready()
        replies = []
        with create_pipe_input() as pipe:
            session = PromptSession(input=pipe, output=DummyOutput(), reserve_space_for_menu=0)
            session.key_bindings = self.browser.bindings(lambda: session, replies.append)
            prompt = asyncio.create_task(session.prompt_async(lambda: self.browser.render(session.default_buffer.text)))
            try:
                pipe.send_text("\x1b[B\x1b[C\r")
                await self.until(lambda: self.browser.pane == "detail")
                self.assertEqual(self.app.focus, jid)
                pipe.send_text("\x1b[C")
                await self.until(lambda: self.browser.tab == 1)
                self.assertTrue(any("Please share" in line for line in self.browser.detail_lines(72)))
                self.assertIsNone(self.app.db.job(jid)["approval_id"])
                self.assertEqual(self.jira.posts, 0)
                pipe.send_text('\x1b[C'*8)
                await self.until(lambda:self.browser.tab==9)
                self.assertTrue(self.browser.audit_view['valid'])
                self.assertTrue(any('Ed25519' in line for line in self.browser.detail_lines(90)))
                pipe.send_text("what are you doing?\n")
                self.assertEqual(await asyncio.wait_for(prompt, 3), "what are you doing?")
            finally:
                if not prompt.done():
                    session.app.exit(result="")
                await asyncio.gather(prompt, return_exceptions=True)

    async def test_closed_request_is_history_without_fake_signoff(self):
        jid = await self.local_ready()
        await self.app.dispatch("reject", target=jid, note="Not proceeding")
        past = self.browser.groups()[2]
        self.assertEqual(len(past), 1)
        self.assertIsNone(self.browser.signed_off(past[0]))
