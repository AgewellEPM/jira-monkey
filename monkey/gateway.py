"""Monkey's authenticated loopback MCP and HTTP API, owned by the live application."""
import asyncio
from contextvars import ContextVar
from importlib import import_module
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import time

from . import VERSION
from .common import Refused, encoded, require
from .sml import put
from .security import bounded_json, strict_json

ORIGIN = ContextVar('monkey_command_origin',default='operator_repl')


class PrivateAPI:
    def __init__(self, app, token, port):
        self.app,self.token,self.port = app,token,port
        self.active = 0
        self.tokens, self.updated = 60., time.monotonic()

    async def __call__(self, scope, receive, send):
        from starlette.responses import JSONResponse
        if scope['type']=='websocket':
            return await send({'type':'websocket.close','code':1008})
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        pairs = scope.get('headers',[])
        headers = dict(pairs)
        expected = ('Bearer '+self.token).encode()
        duplicates = any(sum(k==name for k,_ in pairs)!=1 for name in (b'host',b'authorization') if name in headers)
        duplicates |= sum(k==b'content-length' for k,_ in pairs)>1
        if (duplicates or b'transfer-encoding' in headers and b'content-length' in headers or
                headers.get(b'host') not in {('127.0.0.1:'+str(self.port)).encode(),('localhost:'+str(self.port)).encode()}
                or b'origin' in headers):
            return await JSONResponse({'error':'Local non-browser API clients only'},status_code=403)(scope,receive,send)
        if not hmac.compare_digest(headers.get(b'authorization',b''),expected):
            return await JSONResponse({'error':'Monkey API authentication required'},status_code=401)(scope,receive,send)
        current = time.monotonic()
        self.tokens = min(60., self.tokens+(current-self.updated)*2)
        self.updated = current
        if self.tokens < 1 or self.active >= 8:
            return await JSONResponse({'error':'Monkey API request limit reached; retry later'},status_code=429,headers={'Retry-After':'1'})(scope,receive,send)
        self.tokens -= 1
        self.active += 1
        received, started = 0, False
        async def bounded_receive():
            nonlocal received
            async with asyncio.timeout(5):
                message = await receive()
            received += len(message.get('body',b''))
            require(received<=65536,'API request too large')
            return message
        async def tracked_send(message):
            nonlocal started
            started |= message['type']=='http.response.start'
            await send(message)
        marker = ORIGIN.set('monkey_api_client')
        try:
            async with asyncio.timeout(15):
                await self.app(scope,bounded_receive,tracked_send)
        except (TimeoutError,Refused):
            if not started:
                await JSONResponse({'error':'API request exceeded its time or size limit'},status_code=400)(scope,receive,send)
        finally:
            ORIGIN.reset(marker)
            self.active -= 1


class Gateway:
    def __init__(self, app):
        self.app = app
        self.task = self.server = self.socket = None
        self.port = None
        self.config_path = None
        self._lock = asyncio.Lock()

    def status(self):
        running = bool(self.task and not self.task.done() and self.server.started)
        return {'running':running,'api_url':'http://127.0.0.1:'+str(self.port)+'/v1' if running else None,
            'mcp_url':'http://127.0.0.1:'+str(self.port)+'/mcp' if running else None,
            'client_config':str(self.config_path) if running else None,
            'authority':'External clients can inspect, capture and propose; approvals stay in Monkey. Only a previously authorized mission can run.',
            'message':'Monkey owns this MCP/API endpoint while the application remains open.' if running else '/api start opens Monkey’s own MCP/API in this application.'}

    async def invoke(self, operation, arguments):
        bounded_json(arguments)
        require(type(arguments) is dict and len(encoded(arguments))<=60000,'Use a bounded JSON argument object')
        methods = {
            'status':lambda:self.app.dispatch('status'),
            'jobs':lambda:self.app.dispatch('jobs'),
            'services':lambda:self.app.dispatch('services'),
            'tools':lambda:self.app.dispatch('tools',**arguments),
            'events':lambda:self.app.dispatch('events',target=arguments['job_id']),
            'task':lambda:self.app.dispatch('task',text=arguments['text']),
            'recall':lambda:self.app.dispatch('recall',text=arguments['text']),
            'remember':lambda:self.app.dispatch('remember',text=arguments['text']),
            'mission':lambda:self.app.dispatch('mission',target=arguments['job_id'],text=arguments['objective']),
            'mission_result':lambda:self.app.dispatch('mission-review',target=arguments['job_id']),
            'run_mission':lambda:self.app.dispatch('mission-run',target=arguments['job_id']),
            'agent_result':lambda:self.app.dispatch('agent',target=arguments['job_id']),
            'windows_result':lambda:self.app.dispatch('windows',target=arguments['job_id']),
            'lessons':lambda:self.app.dispatch('lessons',text=arguments['text']),
        }
        allowed = {'status':set(),'jobs':set(),'services':set(),'tools':{'server'},'events':{'job_id'},'task':{'text'},
            'recall':{'text'},'remember':{'text'},'mission':{'job_id','objective'},'mission_result':{'job_id'},'run_mission':{'job_id'},
            'propose_tool':{'job_id','server','tool','arguments'},'agent_result':{'job_id'},'windows_result':{'job_id'},'lessons':{'text'}}
        require(type(operation) is str and operation in allowed and not set(arguments)-allowed[operation], 'Unsupported API operation or argument; operator approvals are not exposed')
        required = allowed[operation]-({'server'} if operation=='tools' else set())
        require(required <= set(arguments), 'Required API arguments are missing')
        for field in set(arguments)-{'arguments'}:
            value = arguments[field]
            require(field=='server' and value is None and operation=='tools' or type(value) is str and 0<len(value)<=6000, 'Invalid API argument type or size')
        if 'job_id' in arguments:
            require(self.app.db.job(arguments['job_id'])['id']==arguments['job_id'], 'Use an exact job ID; API clients cannot use the operator focus')
        marker = ORIGIN.set('monkey_api_client')
        try:
            if operation=='propose_tool':
                job = self.app.db.job(arguments['job_id'])
                return self.app.connectors.tool_plan(job,arguments['server'],arguments['tool'],arguments['arguments'],self.app.command('tool',job))
            return await methods[operation]()
        finally:
            ORIGIN.reset(marker)

    def application(self):
        from .gateway_runtime import MCPServer, ToolError, Request, JSONResponse
        server = MCPServer('Monkey',version=VERSION,log_level='WARNING',instructions='Use recorded Monkey work. Proposals do not approve themselves. Credentials and operator approvals belong in the local Monkey terminal.')
        async def invoke(operation,arguments):
            try:
                return await self.invoke(operation,arguments)
            except Refused as exc:
                raise ToolError(str(exc)) from None

        @server.tool()
        async def monkey_windows_result(job_id: str) -> dict:
            """Read recorded Windows session, workflow and cleanup reports; grants no action authority."""
            return await invoke('windows_result', {'job_id': job_id})

        @server.tool()
        async def monkey_agent_result(job_id: str) -> dict:
            """Read the actual coding/research plan, observations, sources and result."""
            return await invoke('agent_result',{'job_id':job_id})

        @server.tool()
        async def monkey_lessons(text: str) -> dict:
            """Read matching evidence-linked procedures; these grant no tool authority."""
            return await invoke('lessons',{'text':text})

        @server.tool()
        async def monkey_status() -> dict:
            """Read committed worker, queue and delivery status."""
            return await invoke('status',{})

        @server.tool()
        async def monkey_jobs() -> list[dict]:
            """List captured tasks and their actual recorded states."""
            return await invoke('jobs',{})

        @server.tool()
        async def monkey_services() -> dict:
            """Inspect Monkey-owned service adapters and setup status; no credentials returned."""
            return await invoke('services',{})

        @server.tool()
        async def monkey_tools(server: str | None = None) -> list[dict] | dict:
            """Read the exact schemas of configured service tools."""
            return await invoke('tools',{'server':server})

        @server.tool()
        async def monkey_task(text: str) -> dict:
            """Capture a local work request. It does not execute or publish anything."""
            return await invoke('task',{'text':text})

        @server.tool()
        async def monkey_events(job_id: str) -> list[dict]:
            """Inspect the committed event journal of an exact task."""
            return await invoke('events',{'job_id':job_id})

        @server.tool()
        async def monkey_recall(text: str) -> dict:
            """Recall retained evidence and advisory memory; recalling grants no authority."""
            return await invoke('recall',{'text':text})

        @server.tool()
        async def monkey_remember(text: str) -> dict:
            """Retain a bounded advisory note with client provenance; it never grants tools or signs off work."""
            return await invoke('remember',{'text':text})

        @server.tool()
        async def monkey_propose_tool(job_id: str, server: str, tool: str, arguments: dict) -> dict:
            """Propose literal service arguments for operator review. Does not call the service."""
            return await invoke('propose_tool',{'job_id':job_id,'server':server,'tool':tool,'arguments':arguments})

        @server.tool()
        async def monkey_mission(job_id: str, objective: str) -> dict:
            """Request bounded local mission planning; no approval authority is granted."""
            return await invoke('mission',{'job_id':job_id,'objective':objective})

        @server.tool()
        async def monkey_mission_result(job_id: str) -> dict:
            """Read exact mission proposals, agent calls, questions and outcomes."""
            return await invoke('mission_result',{'job_id':job_id})

        @server.tool()
        async def monkey_run_mission(job_id: str) -> dict:
            """Start only a mission previously authorized in Monkey; cannot approve or replay it."""
            return await invoke('run_mission',{'job_id':job_id})

        application = server.streamable_http_app(stateless_http=True,json_response=True,max_request_body_size=65536,max_sessions=16)
        async def rpc(request: Request):
            try:
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    require(len(raw)<=65536,'API request too large')
                data = strict_json(raw,limit=65536)
                require(type(data) is dict and set(data)=={'operation','arguments'},'Supply operation and arguments')
                value = await self.invoke(data['operation'],data['arguments'])
                return JSONResponse({'result':value})
            except Exception as exc:
                return JSONResponse({'error':str(exc) if isinstance(exc,Refused) else 'Invalid request or unavailable recorded state'},status_code=400)
        async def status(request: Request):
            return JSONResponse(await invoke('status',{}))
        application.add_route('/v1/rpc',rpc,methods=['POST'])
        application.add_route('/v1/status',status,methods=['GET'])
        return application

    async def start(self, port=0):
        async with self._lock:
            require(not self.task or self.task.done(),'Monkey MCP/API is already running; /api status shows its generated connection file')
            require(type(port) is int and 0<=port<=65535,'Use a valid local port; 0 selects a free port')
            runtime = await asyncio.to_thread(import_module, '.gateway_runtime', __package__)
            token = secrets.token_urlsafe(40)
            self.socket = socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            try:
                self.socket.bind(('127.0.0.1',port))
                self.port = self.socket.getsockname()[1]
                self.socket.setblocking(False)
                config = runtime.uvicorn.Config(PrivateAPI(self.application(),token,self.port),log_config=None,access_log=False,
                    log_level='critical',lifespan='on',timeout_graceful_shutdown=2,limit_concurrency=32,
                    proxy_headers=False,server_header=False,timeout_keep_alive=3,h11_max_incomplete_event_size=16384)
                self.server = runtime.LocalServer(config)
                self.task = asyncio.create_task(self.server.serve(sockets=[self.socket]),name='monkey-mcp-api')
                async with asyncio.timeout(8):
                    while not self.server.started:
                        require(not self.task.done(),'Monkey MCP/API could not start')
                        await asyncio.sleep(.01)
                self.config_path = self.app.db.root/'gateway'/('client-'+secrets.token_hex(8)+'.json')
                url = 'http://127.0.0.1:'+str(self.port)
                put(self.config_path,{'mcpServers':{'monkey':{'transport':'streamable-http','url':url+'/mcp',
                    'headers':{'Authorization':'Bearer '+token}}},'api':{'url':url+'/v1','headers':{'Authorization':'Bearer '+token}}})
                self.app.db.global_event('gateway.started',{'message':'Monkey MCP/API started on loopback; private client configuration generated.'},self.app.command('api-start'))
                return self.status()
            except BaseException:
                await self._stop()
                raise

    async def _stop(self):
        if self.server:
            self.server.should_exit = True
        if self.task and not self.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self.task),5)
            except (TimeoutError,asyncio.CancelledError):
                self.task.cancel()
                await asyncio.gather(self.task,return_exceptions=True)
        if self.socket:
            self.socket.close()
        if self.config_path:
            self.config_path.unlink(missing_ok=True)
        self.task = self.server = self.socket = None

    async def close(self):
        async with self._lock:
            await self._stop()
