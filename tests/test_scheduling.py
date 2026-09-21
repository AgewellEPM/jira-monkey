import asyncio
import datetime as dt
import tempfile
import unittest
from unittest.mock import patch

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from monkey.browser import Browser
from monkey.common import Refused
from monkey.database import Database
from monkey.demo import demo_app
from monkey.scheduling import instant
from monkey.ui import repl


class SprintScheduling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.app, self.jira = demo_app(self.folder.name, delay=.001)
        result = await self.app.dispatch("request", text="Draft a sprint ticket for password reset")
        self.jid = result["job_id"]
        await self.until(lambda: self.app.db.job(self.jid)["work_state"] == "DRAFT_READY")

    async def asyncTearDown(self):
        await self.app.close()
        self.folder.cleanup()

    async def until(self, predicate):
        async with asyncio.timeout(4):
            while not predicate():
                await asyncio.sleep(.01)

    async def assign(self, **overrides):
        return await self.app.dispatch("schedule", target=self.jid,
            **({"start": "2026-09-14 09:00", "finish": "2026-09-25 17:00", "timezone": "America/New_York"} | overrides))

    async def test_dates_roundtrip_with_timezone_and_approval_binding(self):
        await self.assign()
        s = (await self.app.dispatch("dates", target=self.jid))["schedule"]
        self.assertEqual(s["start_utc"], "2026-09-14T13:00:00+00:00")
        self.assertEqual(s["finish_local"], "2026-09-25T17:00:00-04:00")
        await self.app.dispatch("approve", target=self.jid, note="Approved draft and dates")
        j = self.app.db.job(self.jid)
        self.assertIsNotNone(Browser(self.app).signed_off(j))
        await self.assign(finish="2026-09-28 17:00")
        j = self.app.db.job(self.jid)
        self.assertIsNone(j["approval_id"])
        self.assertEqual(len(self.app.db.records("schedules", self.jid)), 2)
        self.assertEqual(j["attempt_count"], 1)

    async def test_invalid_or_ambiguous_times_do_not_change_schedule(self):
        for start, finish in [("2026-09-14", "2026-09-15 12:00"),
                              ("2026-03-08 02:30", "2026-03-08 04:00"),
                              ("2026-11-01 01:30", "2026-11-01 04:00"),
                              ("2026-09-15 12:00", "2026-09-14 12:00")]:
            with self.assertRaises(Refused):
                await self.assign(start=start, finish=finish)
        self.assertEqual(self.app.db.records("schedules", self.jid), [])
        self.assertEqual(instant("2026-11-01T01:30-05:00", "America/New_York")[0], "2026-11-01T06:30:00+00:00")

    async def test_return_reschedule_resume_preserves_counters_and_history(self):
        await self.assign()
        await self.app.dispatch("approve", target=self.jid, note="Reviewed")
        before = self.app.db.job(self.jid)
        await self.app.dispatch("return", target=self.jid, note="Dependency delayed; choose a new sprint")
        returned = self.app.db.job(self.jid)
        self.assertEqual(returned["work_state"], "PAUSED")
        self.assertIsNone(returned["approval_id"])
        with self.assertRaises(Refused):
            await self.app.dispatch("resume", target=self.jid)
        with self.assertRaises(Refused):
            await self.app.dispatch("approve", target=self.jid, note="Not yet")
        self.assertIsNone(Browser(self.app).completed_at(returned))
        await self.assign(start="2026-09-28 09:00", finish="2026-10-09 17:00")
        await self.app.dispatch("resume", target=self.jid)
        after = self.app.db.job(self.jid)
        self.assertEqual(after["work_state"], "DRAFT_READY")
        self.assertEqual(after["attempt_count"], before["attempt_count"])
        self.assertEqual(after["call_count"], before["call_count"])
        self.assertEqual(len(self.app.db.records("schedules", self.jid)), 3)

    async def test_planned_calendar_is_not_completion_evidence(self):
        await self.assign()
        browser = Browser(self.app)
        browser.section, browser.pane = 3, "list"
        browser.day = dt.date(2026, 9, 16)
        self.assertEqual(browser.rows(), [])
        browser.calendar_mode = "planned"
        self.assertEqual([j["id"] for j in browser.rows()], [self.jid])
        await self.app.dispatch("return", target=self.jid, note="Dates need correction")
        self.assertEqual(browser.rows(), [])

    async def test_browser_form_saves_only_after_all_operator_fields(self):
        browser = Browser(self.app)
        browser.pane, browser.tab, browser.job_id = "detail", 4, self.jid
        with create_pipe_input() as pipe:
            session = PromptSession(input=pipe, output=DummyOutput(), reserve_space_for_menu=0)
            with patch("monkey.ui.Browser", return_value=browser):
                task = asyncio.create_task(repl(self.app, session=session))
                def prompt_has(value):
                    return value in "".join(t[1] for t in to_formatted_text(session.message))
                try:
                    await self.until(lambda: session.app.is_running)
                    pipe.send_text("\r")
                    await self.until(lambda: prompt_has("Start ·"))
                    pipe.send_text("2026-09-14 09:00\n")
                    await self.until(lambda: prompt_has("Finish ·"))
                    self.assertIsNone(self.app.db.job(self.jid).get("schedule"))
                    pipe.send_text("2026-09-25 17:00\n")
                    await self.until(lambda: prompt_has("Timezone"))
                    pipe.send_text("America/New_York\n")
                    await self.until(lambda: prompt_has("Reason / note"))
                    pipe.send_text("Sprint one\n")
                    await self.until(lambda: self.app.db.job(self.jid).get("schedule") is not None)
                    self.assertEqual(self.app.db.job(self.jid)["schedule"]["note"], "Sprint one")
                    pipe.send_text("/quit\n")
                    await asyncio.wait_for(task, 3)
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    async def test_schema1_migration_preserves_jobs_and_backs_up(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as root:
            db = Database(root)
            db.db.execute("DROP TABLE schedules")
            db.db.execute("PRAGMA user_version=1")
            db.close()
            db = Database(root)
            try:
                self.assertEqual(db.db.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertTrue(list(db.root.glob("backup-schema1-*.sqlite3")))
            finally:
                db.close()
