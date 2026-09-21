import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import httpx

from monkey.common import Refused
from monkey.demo import TICKET, demo_app
from monkey.gateway import ORIGIN


class DashboardIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(dir='/private/tmp')
        self.app, self.jira = demo_app(self.folder.name, delay=.001)
        opened = await self.app.dispatch('dashboard', action='start')
        self.url, key = opened['dashboard_url'].split('#key=')
        self.headers = {'Authorization': 'Bearer ' + key, 'Origin': self.url.rstrip('/')}
        self.http = httpx.AsyncClient(base_url=self.url, headers=self.headers, timeout=5, trust_env=False)

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.app.close()
        self.folder.cleanup()

    async def until(self, predicate):
        async with asyncio.timeout(5):
            while not predicate(): await asyncio.sleep(.01)

    async def post(self, *, text=None, job=None, operation=None, arguments=None, identifier=None, version=None):
        body = {'id': identifier or str(uuid.uuid4()), 'kind': 'message' if text is not None else 'action',
                'job_id': job['id'] if job else None, 'expected_version': version if version is not None else job['version'] if job else None}
        body.update({'text': text} if text is not None else {'operation': operation, 'arguments': arguments or {}})
        response = await self.http.post('/api/requests', json=body)
        return response, body

    async def result(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        rid = response.json()['id']
        def row(): return next(r for r in self.app.dashboard.messages() if r['id'] == rid)
        await self.until(lambda: row()['status'] != 'ACCEPTED')
        return row()

    async def ready(self):
        jid = (await self.app.dispatch('request', text='Draft a sprint ticket for password reset'))['job_id']
        await self.until(lambda: self.app.db.job(jid)['work_state'] == 'DRAFT_READY')
        return self.app.db.job(jid)

    async def test_assets_are_local_and_do_not_expose_work_without_private_key(self):
        async with httpx.AsyncClient(base_url=self.url, trust_env=False) as public:
            page = await public.get('/')
            self.assertEqual(page.status_code, 200)
            self.assertIn('Ask Monkey', page.text)
            self.assertIn("frame-ancestors 'none'", page.headers['Content-Security-Policy'])
            self.assertEqual(page.headers['Cache-Control'], 'no-store')
            self.assertEqual((await public.get('/dashboard.js')).status_code, 200)
            self.assertEqual((await public.get('/dashboard.css')).status_code, 200)
            self.assertEqual((await public.get('/api/state')).status_code, 401)
            self.assertEqual((await public.get('/api/jobs/unknown')).status_code, 401)

    async def test_browser_origin_host_body_and_api_client_authority_are_separate(self):
        self.assertEqual((await self.http.get('/api/state', headers={'Origin': 'https://attacker.invalid'})).status_code, 400)
        self.assertEqual((await self.http.get('/api/state', headers={'Host': 'attacker.invalid'})).status_code, 400)
        self.assertEqual((await self.http.get('/api/state', headers={'Sec-Fetch-Site': 'cross-site'})).status_code, 400)
        self.assertEqual((await self.http.post('/api/requests', content='x' * 33000, headers={'Content-Type': 'application/json'})).status_code, 400)
        self.assertEqual((await self.http.post('/api/requests', json={}, headers={'Origin': 'null'})).status_code, 400)
        response, _ = await self.post(text='/Dashboard stop')
        self.assertEqual(response.status_code, 400)
        marker = ORIGIN.set('monkey_api_client')
        try:
            with self.assertRaises(Refused): await self.app.dispatch('dashboard', action='status')
            with self.assertRaises(Refused): await self.app.dispatch('approve', target='invented', note='Web role claimed by external client')
        finally: ORIGIN.reset(marker)
        self.assertEqual(self.app.db.jobs(), [])

    async def test_commands_use_the_same_jobs_and_replay_ids_never_repeat_work(self):
        response, body = await self.post(text='/task Draft an account recovery ticket')
        self.assertEqual((await self.result(response))['status'], 'REPLIED')
        replay = await self.http.post('/api/requests', json=body)
        self.assertEqual(replay.json()['id'], response.json()['id'])
        self.assertEqual(len(self.app.db.jobs()), 1)
        body['text'] = '/task A different request'
        self.assertEqual((await self.http.post('/api/requests', json=body)).status_code, 400)
        state = (await self.http.get('/api/state')).json()
        self.assertEqual(state['jobs'][0]['job_id'], self.app.db.jobs()[0]['id'])
        self.assertEqual(state['counts']['current'], 1)
        self.assertTrue(any(e['kind'] == 'dashboard.command.settled' for e in self.app.db.events()))
        trace = self.app.audit.view()
        self.assertTrue(trace['valid'])
        self.assertTrue(any(r['kind'] == 'dashboard.request' for r in trace['trace']))

    async def test_status_and_controls_do_not_wait_for_an_inflight_message(self):
        job = (await self.ready())
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(app, text):
            entered.set(); await release.wait(); return {'message': 'Retained response'}
        with patch('monkey.cli.line', slow):
            response, _ = await self.post(text='Explain this slowly', job=job)
            await asyncio.wait_for(entered.wait(), 3)
            started = time.perf_counter()
            result = await self.http.get('/api/state')
            self.assertEqual(result.status_code, 200)
            self.assertLess(time.perf_counter() - started, .5)
            pause, _ = await self.post(job=job, operation='pause', arguments={'after': 'now'})
            self.assertEqual((await self.result(pause))['status'], 'REPLIED')
            release.set()
            await self.result(response)
        self.assertEqual(self.app.db.job(job['id'])['work_state'], 'PAUSED')

    async def test_message_keeps_its_selected_job_when_terminal_focus_changes(self):
        first = (await self.app.dispatch('task', text='First ticket'))['job_id']
        second = (await self.app.dispatch('task', text='Second ticket'))['job_id']
        previous_second = self.app.db.job(second)['work_state']
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(app, text):
            entered.set(); await release.wait()
            return await app.dispatch('pause', app.focus)
        with patch('monkey.cli.line', slow):
            response, _ = await self.post(text='Pause this one', job=self.app.db.job(first))
            await asyncio.wait_for(entered.wait(), 3)
            await self.app.dispatch('focus', second)
            release.set(); await self.result(response)
        self.assertEqual(self.app.db.job(first)['work_state'], 'PAUSED')
        self.assertEqual(self.app.db.job(second)['work_state'], previous_second)
        self.assertEqual(self.app.focus, second)

    async def test_review_is_bound_to_displayed_version_and_candidate(self):
        job = await self.ready()
        detail = (await self.http.get('/api/jobs/' + job['id'] + '?tab=draft')).json()
        wrong, _ = await self.post(job=job, operation='approve', arguments={'note': 'Reviewed', 'draft_hash': '0' * 64})
        self.assertEqual(wrong.status_code, 400)
        await self.app.dispatch('retry', job['id'], note='Request more reproduction details')
        stale, _ = await self.post(job=job, operation='approve', arguments={'note': 'Old view', 'draft_hash': detail['draft']['payload_hash']})
        self.assertEqual(stale.status_code, 400)
        self.assertIsNone(self.app.db.job(job['id'])['approval_id'])
        self.assertEqual(self.jira.posts, 0)

    async def test_local_signoff_is_in_calendar_without_publishing(self):
        job = await self.ready()
        draft = self.app.db.record('drafts', job['draft_id'])
        response, _ = await self.post(job=job, operation='approve', arguments={'note': 'Reviewed exact local draft', 'draft_hash': draft['payload_hash']})
        self.assertEqual((await self.result(response))['status'], 'REPLIED')
        state = (await self.http.get('/api/state?view=calendar')).json()
        self.assertEqual(state['total'], 1)
        self.assertEqual(state['counts']['completed'], 1)
        self.assertEqual(state['counts']['published'], 0)
        self.assertEqual(self.jira.posts, 0)
        self.assertEqual(sum(state['calendar_days'].values()), 1)

    async def test_uncertain_publication_stays_uncertain_on_replayed_web_request(self):
        jid = (await self.app.dispatch('fetch', key=TICKET['key']))['job_id']
        await self.app.dispatch('run', jid); await self.app.worker.task
        await self.app.dispatch('approve', jid, note='Reviewed fixture')
        job = self.app.db.job(jid); draft = self.app.db.record('drafts', job['draft_id'])
        preview_response, _ = await self.post(job=job, operation='publish_preview', arguments={'draft_hash': draft['payload_hash']})
        preview = (await self.result(preview_response))['publication_preview']
        self.assertEqual(self.jira.posts, 0)
        _, token, key = preview['confirmation'].split()
        self.jira.scenario = 'response-loss'
        response, body = await self.post(job=job, operation='confirm', arguments={'token': token, 'key': key})
        await self.result(response)
        self.assertEqual(self.jira.posts, 1)
        self.assertEqual(self.app.db.job(jid)['delivery_state'], 'POST_UNKNOWN')
        await self.http.post('/api/requests', json=body)
        self.assertEqual(self.jira.posts, 1)

    async def test_shutdown_interrupts_pending_reply_removes_listener_and_rotates_key(self):
        entered = asyncio.Event()
        async def slow(app, text):
            entered.set(); await asyncio.Event().wait()
        port = self.app.dashboard.port
        with patch('monkey.cli.line', slow):
            response, _ = await self.post(text='A pending explanation')
            await asyncio.wait_for(entered.wait(), 3)
            await self.app.dashboard.close()
        self.assertEqual(next(r for r in self.app.dashboard.messages() if r['id'] == response.json()['id'])['status'], 'INTERRUPTED')
        self.assertFalse(self.app.dashboard.tasks)
        with self.assertRaises(OSError): await asyncio.open_connection('127.0.0.1', port)
        await self.app.dispatch('dashboard', action='start', port=port)
        self.assertEqual((await self.http.get('/api/state')).status_code, 401)

    async def test_pagination_history_and_planned_dates_are_recorded_data(self):
        for number in range(53): await self.app.dispatch('task', text='Fixture task ' + str(number))
        page = (await self.http.get('/api/state?offset=50')).json()
        self.assertEqual(len(page['jobs']), 3)
        self.assertEqual(page['total'], 53)
        searched = (await self.http.get('/api/state?q=Fixture%20task%2049')).json()
        self.assertEqual(searched['total'], 1)
        history = (await self.http.get('/api/history?before=' + str(self.app.db.sequence()))).json()['events']
        self.assertTrue(history)
        self.assertEqual(history, sorted(history, key=lambda e: e['seq']))
        job = self.app.db.jobs()[0]
        await self.app.dispatch('schedule', job['id'], start='2026-09-14T10:00', finish='2026-09-16T16:00', timezone='America/New_York', note='Fixture sprint dates')
        planned = (await self.http.get('/api/state?view=calendar&calendar_mode=planned&month=2026-09&day=2026-09-15')).json()
        self.assertEqual(planned['total'], 1)
        self.assertEqual(planned['calendar_days'], {'2026-09-14': 1, '2026-09-15': 1, '2026-09-16': 1})
        await self.app.dispatch('cancel', job['id'])
        cancelled = (await self.http.get('/api/state?view=calendar&calendar_mode=planned&month=2026-09&day=2026-09-15')).json()
        self.assertEqual(cancelled['total'], 0)
        self.assertEqual(cancelled['calendar_days'], {})
