"""A local browser face for the foreground Monkey application, not a second worker."""
from __future__ import annotations

import asyncio
import calendar
from contextlib import contextmanager
import datetime as dt
import hmac
import hashlib
from importlib import import_module
import json
from pathlib import Path
import re
import secrets
import socket
import time

from . import VERSION
from .common import Refused, digest, now, require, safe
from .gateway import ORIGIN
from .operator_context import REQUEST_FOCUS
from .security import strict_json

ASSETS = {'/': ('index.html', 'text/html'), '/dashboard.css': ('dashboard.css', 'text/css'),
          '/dashboard.js': ('dashboard.js', 'text/javascript')}
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"}
ACTION_ARGS = {'focus': set(), 'run': set(), 'pause': {'after'}, 'resume': set(),
    'cancel': set(), 'retry': {'note'}, 'reject': {'note'}, 'approve': {'note', 'draft_hash'},
    'publish_preview': {'draft_hash'}, 'confirm': {'token', 'key'}, 'reconcile': set(),
    'schedule': {'start', 'finish', 'timezone', 'note'}}


class Dashboard:
    def __init__(self, app):
        self.app = app
        self.task = self.server = self.socket = None
        self.port = self.token = None
        self.tasks = set()
        self._lock = asyncio.Lock()
        self._tokens, self._updated = 60., time.monotonic()
        self._completion = {}
        prior_requests = list(self.app.db.db.execute("SELECT data FROM artifacts WHERE id LIKE 'webcmd_%' ORDER BY rowid"))
        for raw in prior_requests:
            row = json.loads(raw[0])
            if row['status'] == 'ACCEPTED':
                self.save({**row, 'status': 'INTERRUPTED', 'finished_at': now(),
                    'reply': 'The previous foreground session ended before a reply was retained. Inspect the job and its events before issuing new work.'})

    def status(self, private=False):
        running = bool(self.task and not self.task.done() and self.server.started)
        url = f'http://127.0.0.1:{self.port}/' if running else None
        return {'running': running, 'dashboard_url': url + '#key=' + self.token if running and private else url,
            'message': 'Your browser and terminal share this foreground Monkey session.' if running else
                       '/dashboard starts the web workspace in this Monkey session.'}

    def messages(self):
        rows = self.app.db.db.execute("SELECT data FROM artifacts WHERE id LIKE 'webcmd_%' ORDER BY rowid DESC LIMIT 80")
        return list(reversed([json.loads(row[0]) for row in rows]))

    def save(self, row):
        stage = 'accepted' if row['status'] == 'ACCEPTED' else 'settled'
        message = 'Dashboard request accepted.' if stage == 'accepted' else 'Dashboard reply ready.' if row['status'] == 'REPLIED' else 'Dashboard request needs attention.'
        self.app.db.global_event('dashboard.command.' + stage,
            {'message': message, 'request_id': row['id'], 'status': row['status']},
            record=('artifacts', row['id'], row))

    def groups(self):
        # Use the exact completion rules used by the terminal, including stale
        # approvals, uncertain delivery and returned sprint dates.
        from .browser import Browser
        browser = Browser(self.app)
        jobs = self.app.db.jobs()
        output = []
        for job in jobs:
            stamp = (job['version'], job.get('updated_at'))
            cached = self._completion.get(job['id'])
            if not cached or cached[0] != stamp:
                cached = (stamp, browser.completed_at(job))
                self._completion[job['id']] = cached
            completed = cached[1]
            completed_day = dt.datetime.fromisoformat(completed).astimezone().date().isoformat() if completed else None
            output.append({**self.app.summary(job), 'completed_at': completed, 'completed_day': completed_day, 'updated_at': job['updated_at'],
                'needs_you': self.app.needs_you(job), 'has_draft': bool(job['draft_id']),
                'reviewed': bool(job['review_id']), 'group': 'completed' if completed else 'current'})
        return output

    def snapshot(self, query):
        after = int(query.get('after', '0'))
        offset = int(query.get('offset', '0'))
        require(0 <= after <= 2**63 - 1 and 0 <= offset <= 1000000, 'Invalid event cursor or page')
        view, search, day = query.get('view', 'overview'), query.get('q', '')[:300].casefold(), query.get('day', '')
        require(view in {'overview', 'current', 'review', 'calendar', 'activity', 'connections'}, 'Unknown dashboard view')
        calendar_mode = query.get('calendar_mode', 'completed')
        require(calendar_mode in {'completed', 'planned'}, 'Unknown calendar mode')
        all_jobs = self.groups()
        counts = {'current': sum(j['group'] == 'current' for j in all_jobs),
            'review': sum(j['needs_you'] for j in all_jobs), 'completed': sum(j['group'] == 'completed' for j in all_jobs),
            'published': sum(j['delivery_state'] == 'POSTED_VERIFIED' for j in all_jobs)}
        jobs = list(reversed(all_jobs))
        if view == 'current': jobs = [j for j in jobs if j['group'] == 'current']
        if view == 'review': jobs = [j for j in jobs if j['needs_you']]
        if view == 'calendar':
            if calendar_mode == 'completed':
                jobs = [j for j in jobs if j['completed_at'] and (not day or j['completed_day'] == day)]
            else:
                jobs = [j for j in jobs if j.get('schedule') and j['schedule'].get('status') == 'SCHEDULED' and
                        j['work_state'] not in {'CANCELLED', 'REJECTED'} and
                        (not day or j['schedule']['start_local'][:10] <= day <= j['schedule']['finish_local'][:10])]
        if search: jobs = [j for j in jobs if search in (j['key'] + ' ' + j['title'] + ' ' + j['source']).casefold()]
        days = {}
        for job in all_jobs:
            if job['completed_at']:
                key = job['completed_day']
                days[key] = days.get(key, 0) + 1
        if calendar_mode == 'planned':
            month = query.get('month', dt.date.today().strftime('%Y-%m'))
            require(re.fullmatch(r'[0-9]{4}-[0-9]{2}', month), 'Use a YYYY-MM calendar month')
            year, number = map(int, month.split('-'))
            days = {}
            for number_day in range(1, calendar.monthrange(year, number)[1] + 1):
                date = dt.date(year, number, number_day).isoformat()
                count = sum(bool((s := j.get('schedule')) and s.get('status') == 'SCHEDULED' and
                    j['work_state'] not in {'CANCELLED', 'REJECTED'} and s['start_local'][:10] <= date <= s['finish_local'][:10]) for j in all_jobs)
                if count: days[date] = count
        events = self.app.db.events(after, limit=100)
        c = self.app.db.config()
        state = self.app.status()
        state.pop('jobs', None)
        return {**state, 'version': VERSION, 'offline': self.app.offline, 'counts': counts,
            'jobs': jobs[offset:offset + 50], 'total': len(jobs), 'offset': offset,
            'calendar_days': days, 'events': events, 'cursor': events[-1]['seq'] if events else after,
            'more_events': len(events) == 100, 'messages': self.messages(), 'services': self.app.services.list()['services'],
            'models': {'chat': c['chat_model'], 'draft': c['model'], 'draft_provider': c['provider']}, 'observed_at': now()}

    async def detail(self, job_id, tab):
        require(self.app.db.job(job_id)['id'] == job_id, 'Use an exact job ID')
        operations = {'overview': 'show', 'draft': 'draft', 'events': 'events', 'dates': 'dates',
                      'tools': 'tool-result', 'plan': 'execution', 'trace': 'audit', 'mission': 'mission-review', 'agent': 'agent'}
        require(tab in operations, 'Unknown evidence tab')
        value = await self.app.dispatch(operations[tab], job_id)
        job = self.app.db.job(job_id)
        draft = self.app.db.record('drafts', job['draft_id']) if job['draft_id'] else None
        snapshot = self.app.db.record('snapshots', job['snapshot_id'])
        return {'job': self.app.summary(job), 'draft': draft, 'text': self.describe(value),
                'tab': tab, 'version': job['version'], 'destination': {
                    'instance': snapshot['ticket']['instance'], 'key': snapshot['ticket']['key'],
                    'snapshot_hash': snapshot['content_hash']}}

    @staticmethod
    def describe(value):
        from .presentation import content
        from .ui import render
        if isinstance(value, dict) and 'form' in value:
            return 'Continue this guided setup in the Monkey terminal with /connect. Credentials stay in the hidden terminal form.'
        try:
            text = safe(content(value, render))
        except (KeyError, TypeError, ValueError):
            # A presentation error must not disguise an already committed action
            # as a failed operation or invite the operator to repeat its effect.
            text = safe(json.dumps(value, ensure_ascii=False, indent=2))
        return text if len(text) <= 120000 else text[:120000] + '\n[View the complete record in the terminal.]'

    def accept(self, data):
        require(type(data) is dict and data.get('kind') in {'message', 'action'}, 'Choose message or action')
        common = {'id', 'kind', 'job_id', 'expected_version'}
        allowed = common | ({'text'} if data['kind'] == 'message' else {'operation', 'arguments'})
        require(set(data) == allowed, 'Unexpected or missing dashboard request field')
        require(isinstance(data['id'], str) and re.fullmatch(r'[a-f0-9-]{32,36}', data['id']), 'Use a unique request ID')
        rid, hashed = 'webcmd_' + data['id'], digest(data)
        prior = self.app.db.db.execute('SELECT data FROM artifacts WHERE id=?', (rid,)).fetchone()
        if prior:
            row = json.loads(prior[0])
            require(row['request_hash'] == hashed, 'Request ID was already used for different input')
            return row
        require(len(self.tasks) < 8, 'Monkey already has eight pending dashboard requests')
        job = self.app.db.job(data['job_id']) if data['job_id'] else None
        require(data['job_id'] is None or job and job['id'] == data['job_id'], 'Use an exact job ID')
        require(data['expected_version'] is None or type(data['expected_version']) is int, 'Invalid job version')
        if data['kind'] == 'message':
            from .ui import is_secret
            text = data['text']
            require(type(text) is str and 0 < len(text.strip()) <= 6000, 'Enter a message of at most 6,000 characters')
            require(not is_secret(text), 'Keep credentials in Monkey’s hidden connection setup')
            require(text.strip().split()[0].lstrip('/').lower() not in {'quit', 'exit', 'repl', 'demo', 'dashboard', 'api', 'setup', 'builder', 'service-save'},
                    'Use this session’s terminal for application lifecycle and credential setup')
        else:
            op, args = data['operation'], data['arguments']
            require(type(op) is str and op in ACTION_ARGS and type(args) is dict and set(args) <= ACTION_ARGS[op], 'Unknown action or argument')
            require(job and data['expected_version'] == job['version'], 'This ticket changed. Refresh and inspect it before acting')
            require(all(type(value) is str and len(value) <= 4000 for value in args.values()), 'Use bounded text arguments')
            if op in {'approve', 'publish_preview'}:
                require(job['draft_id'], 'There is no complete draft')
                require(args.get('draft_hash') == self.app.db.record('drafts', job['draft_id'])['payload_hash'], 'The displayed draft changed; inspect it again')
            if op in {'approve', 'retry', 'reject'}: require(args.get('note', '').strip(), 'Add your review or direction')
        row = {'id': rid, 'request_hash': hashed, 'kind': data['kind'], 'text': data.get('text', data.get('operation')),
            'job_id': data['job_id'], 'status': 'ACCEPTED', 'accepted_at': now(), 'session_id': self.app.session_id}
        self.save(row)
        task = asyncio.create_task(self.execute(data, row), name='monkey-dashboard-command')
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return row

    async def execute(self, data, row):
        origin = ORIGIN.set('operator_dashboard')
        focus = REQUEST_FOCUS.set(data['job_id'])
        result = None
        try:
            async with self.app.audit.run(data['job_id'], 'dashboard-request'):
                self.app.audit.observe('dashboard.request', {'request_id': row['id'], 'request_hash': row['request_hash'],
                    'text': row['text'], 'job_id': data['job_id']}, data['job_id'])
                if data['kind'] == 'message':
                    from .cli import line
                    result = await line(self.app, data['text'])
                else:
                    op, args = data['operation'], dict(data['arguments'])
                    job = self.app.db.job(data['job_id'])
                    require(job['version'] == data['expected_version'], 'Ticket changed before the action ran; inspect it again')
                    if op == 'publish_preview':
                        result = self.app.publication_preview(job)
                    elif op == 'confirm':
                        require(self.app.pending and self.app.pending['job_id'] == job['id'], 'No matching publication preview')
                        result = await self.app.confirm(args.get('token'), args.get('key'))
                    else:
                        args.pop('draft_hash', None)
                        result = await self.app.dispatch(op, job['id'], expected_version=data['expected_version'], **args)
                row = {**row, 'status': 'REPLIED', 'reply': self.describe(result)}
                if isinstance(result, dict) and 'confirmation' in result:
                    row['publication_preview'] = {**result, 'job_version': self.app.db.job(result['job_id'])['version']}
        except asyncio.CancelledError:
            row = {**row, 'status': 'INTERRUPTED', 'reply': 'The request was interrupted. Inspect the retained job and delivery state before issuing new work.'}
        except (Exception, SystemExit) as exc:
            row = {**row, 'status': 'ERROR', 'reply': safe(str(exc)) if isinstance(exc, Refused) else 'The request could not complete (' + type(exc).__name__ + '). Inspect the event journal.'}
        finally:
            try:
                if not self.app.db.failed: self.save({**row, 'finished_at': now()})
            finally:
                REQUEST_FOCUS.reset(focus)
                ORIGIN.reset(origin)

    async def application(self, scope, receive, send):
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        if scope['type'] != 'http':
            if scope['type'] == 'websocket': await send({'type': 'websocket.close', 'code': 1008})
            return
        pairs = scope.get('headers', [])
        headers = dict(pairs)
        path = scope['path']
        base = f'http://127.0.0.1:{self.port}'
        response = None
        origin_marker = ORIGIN.set('operator_dashboard')
        try:
            require(all(sum(k == name for k, _ in pairs) <= 1 for name in (b'host', b'authorization', b'origin', b'content-length')), 'Duplicate request headers')
            require(headers.get(b'host') == f'127.0.0.1:{self.port}'.encode(), 'Use the exact local dashboard address')
            require(headers.get(b'origin', base.encode()) == base.encode() and
                    headers.get(b'sec-fetch-site', b'none') in {b'none', b'same-origin'}, 'Cross-site requests are not accepted')
            require(not (b'transfer-encoding' in headers and b'content-length' in headers), 'Ambiguous body framing')
            if path in ASSETS and scope['method'] == 'GET':
                filename, media = ASSETS[path]
                asset = Path(__file__).with_name('web') / filename
                payload = asset.read_bytes()
                self.app.audit.observe('dashboard.asset.read', {'path': str(asset), 'bytes': len(payload),
                    'sha256': hashlib.sha256(payload).hexdigest()})
                response = Response(payload, media_type=media)
            else:
                if not hmac.compare_digest(headers.get(b'authorization', b''), ('Bearer ' + (self.token or '')).encode()):
                    response = JSONResponse({'error': 'Open the private dashboard link from /dashboard in Monkey.'}, status_code=401)
                else:
                    clock = time.monotonic()
                    self._tokens = min(60., self._tokens + (clock - self._updated) * 10)
                    self._updated = clock
                    require(self._tokens >= 1, 'Dashboard request limit reached; wait a moment')
                    self._tokens -= 1
                    request = Request(scope, receive)
                    self.app.audit.observe('dashboard.read' if scope['method'] == 'GET' else 'dashboard.http_request',
                        {'method': scope['method'], 'path': safe(path)[:300]})
                    if scope['method'] == 'GET' and path == '/api/state':
                        response = JSONResponse(self.snapshot(request.query_params))
                    elif scope['method'] == 'GET' and path == '/api/history':
                        before = int(request.query_params.get('before', '0'))
                        require(0 < before <= 2**63 - 1, 'Use an existing event cursor')
                        rows = self.app.db.db.execute('SELECT * FROM events WHERE seq < ? ORDER BY seq DESC LIMIT 100', (before,))
                        events = [{**dict(r), 'data': json.loads(r['data'])} for r in rows]
                        response = JSONResponse({'events': list(reversed(events))})
                    elif scope['method'] == 'GET' and path.startswith('/api/jobs/'):
                        response = JSONResponse(await self.detail(path[len('/api/jobs/'):], request.query_params.get('tab', 'overview')))
                    elif scope['method'] == 'POST' and path == '/api/requests':
                        require(headers.get(b'origin') == base.encode(), 'Same-origin operator request required')
                        require(headers.get(b'content-type', b'').split(b';')[0] == b'application/json', 'Use JSON for dashboard commands')
                        raw = bytearray()
                        async with asyncio.timeout(5):
                            async for chunk in request.stream():
                                raw.extend(chunk)
                                require(len(raw) <= 32768, 'Dashboard command is too large')
                        row = self.accept(strict_json(raw, limit=32768))
                        response = JSONResponse(row, status_code=202)
                    else:
                        response = JSONResponse({'error': 'Unknown dashboard endpoint'}, status_code=404)
        except (Exception, SystemExit) as exc:
            response = JSONResponse({'error': safe(str(exc)) if isinstance(exc, Refused) else 'Dashboard request could not be read'}, status_code=400)
        finally:
            ORIGIN.reset(origin_marker)
        response.headers.update(HEADERS)
        await response(scope, receive, send)

    async def start(self, port=0):
        async with self._lock:
            if self.task and not self.task.done(): return self.status(private=True)
            require(type(port) is int and 0 <= port <= 65535, 'Use a valid local port')
            uvicorn = await asyncio.to_thread(import_module, 'uvicorn')

            class Server(uvicorn.Server):
                @contextmanager
                def capture_signals(self):
                    yield

            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self.socket.bind(('127.0.0.1', port))
                self.port = self.socket.getsockname()[1]
                self.socket.setblocking(False)
                self.token = secrets.token_urlsafe(40)
                self.server = Server(uvicorn.Config(self.application, log_config=None, access_log=False, log_level='critical',
                    interface='asgi3', lifespan='off', timeout_graceful_shutdown=2, limit_concurrency=32, proxy_headers=False,
                    server_header=False, timeout_keep_alive=3, h11_max_incomplete_event_size=16384))
                self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]), name='monkey-dashboard')
                async with asyncio.timeout(8):
                    while not self.server.started:
                        require(not self.task.done(), 'Dashboard could not start')
                        await asyncio.sleep(.01)
                self.app.db.global_event('dashboard.started', {'message': 'Web dashboard connected to this foreground Monkey session.',
                    'url': self.status()['dashboard_url']}, self.app.command('dashboard-start'))
                return self.status(private=True)
            except BaseException:
                await self.close()
                raise

    async def close(self):
        if self.server: self.server.should_exit = True
        if self.task and not self.task.done():
            try: await asyncio.wait_for(asyncio.shield(self.task), 5)
            except (TimeoutError, asyncio.CancelledError):
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
        for task in tuple(self.tasks): task.cancel()
        if self.tasks: await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
        if self.socket: self.socket.close()
        self.token = None
        if not self.app.db.failed and self.port:
            self.app.db.global_event('dashboard.stopped', {'message': 'Web dashboard stopped. Work remains in Monkey’s journal.'})
        self.task = self.server = self.socket = None
        self.port = None
