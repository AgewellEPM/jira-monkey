"""Typed Windows supervision over existing, exactly approved MCP requests.

GhostBridge remains the session owner and verifies native approvals/receipts.
This module creates no transport, keys, consent, target or action authority.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid

from .common import Refused, clone, digest, identity, now, require
from .security import strict_json


TARGETS = {'legacy.win10': 'windows10', 'legacy.win11': 'windows11'}
LEGACY = {
    'catalog': 'legacy_session_catalog', 'open': 'legacy_session_open',
    'status': 'legacy_session_status', 'observe': 'legacy_accessibility_tree',
    'close': 'legacy_session_close',
}
WORKFLOW = {
    'readiness': 'vcard_status', 'workflow-start': 'vcard_run_start',
    'workflow-status': 'vcard_run_status', 'workflow-step': 'vcard_run_step',
    'provider-tick': 'vcard_provider_tick', 'cleanup-tick': 'vcard_cleanup_tick',
    'workflow-pause': 'vcard_run_pause', 'workflow-receipt': 'vcard_run_receipt',
}
ACTIONS = {**LEGACY, **WORKFLOW}
TERMINAL = {'completed', 'blocked', 'paused', 'recovery-required'}
RUN_STATES = {'ready', 'awaiting-provider', 'cleanup-required', *TERMINAL}


def sha(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'Expected an exact lowercase SHA-256 digest')
    return value


def response_value(raw):
    require(type(raw) is dict and not raw.get('isError'), 'GhostBridge reported an error; inspect its retained response')
    values = []
    if raw.get('structuredContent') is not None:
        values.append(raw['structuredContent'])
    for block in raw.get('content', []):
        if block.get('type') == 'text':
            values.append(strict_json(block['text'], limit=1000000))
    require(values and all(type(v) is dict and v == values[0] for v in values),
            'GhostBridge response must contain one unambiguous JSON object')
    require(values[0].get('ok') is not False, 'GhostBridge did not accept the operation; no success inferred')
    return values[0]


class Windows:
    def __init__(self, app):
        self.app, self.db, self.core = app, app.db, app.execution

    def binding(self, job):
        row = self.core.record(job, 'windows_binding_id')
        require(row['kind'] == 'windows.binding' and row['job_id'] == job['id'], 'Windows binding changed')
        return row

    def bind(self, job, server, workflow_server, target_id, profile_id, note):
        require(not self.core.active and not self.app.worker.active, 'Wait for active work before binding a Windows task')
        require(not job.get('windows_binding_id') and not job.get('work_type') and not job.get('tool_plan_id')
                and not job.get('contract_id') and not job.get('mission_id') and job['work_state'] in {'QUEUED', 'WAITING_USER'},
                'Use a new local task for one Windows session; existing bindings and work cannot be replaced')
        require(job['source'] == 'local' and job['delivery_state'] == 'NONE', 'Windows supervision requires a local execution task')
        require(target_id in TARGETS, 'Select the exact legacy.win10 or legacy.win11 target; no generation substitution')
        require(type(profile_id) is str and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]{0,127}', profile_id),
                'Supply the exact owner-enrolled Windows workflow profile ID')
        require(type(note) is str and note.strip() and len(note) <= 6000, 'Record the purpose of this Windows task')
        require(server != workflow_server, 'Select distinct lifecycle and workflow connections')
        connections = self.app.connectors.connections()
        pins = {}
        for role, name, required in [('legacy', server, LEGACY), ('workflow', workflow_server, WORKFLOW)]:
            row = connections.get(name)
            require(row is not None and row['transport'] in {'streamable-http', 'sse', 'stdio'}, 'Connect an MCP provider for each Windows role first')
            self.app.connectors.config(row)
            available = {t['name'] for t in row['tools']}
            require(set(required.values()) <= available, 'The selected provider lacks the exact Windows tool contract')
            pins[role] = {'name': name, 'connection_id': row['id']}
        record = self.core.seal({'id': identity('windows_'), 'kind': 'windows.binding', 'at': now(),
            'job_id': job['id'], 'target_id': target_id, 'system_id': TARGETS[target_id], 'profile_id': profile_id,
            'connections': pins, 'operator': self.app.operator, 'purpose': note.strip(),
            'mode': 'interactive', 'authority': 'tracking and exact proposals only; native consent remains in GhostBridge'})
        self.core.save(job['id'], 'windows.bound', {'windows_binding_id': record['id'], 'work_state': 'WAITING_USER'},
                       'Windows target recorded. No guest or workflow has been started.', record, self.app.command('windows-bind', job))
        return self.view(self.db.job(job['id']))

    def records(self, job):
        return [self.core.check_seal(r) for r in self.db.records('artifacts', job['id'])
                if r.get('kind') == 'windows.receipt']

    def state(self, job):
        binding = self.binding(job)
        result = {'binding': binding, 'catalog': None, 'session': None, 'workflow': None,
                  'readiness': None, 'terminal_receipt': None, 'last_error': None, 'head': None,
                  'session_at': None, 'workflow_at': None, 'readiness_at': None, 'observation': None, 'observation_at': None}
        for row in self.records(job):
            require(row['binding_id'] == binding['id'], 'Windows receipt belongs to a different target binding')
            result['head'] = row['id']
            if row['classification'] == 'INVALID_OR_ERROR':
                result['last_error'] = row['error']
                if row['action'] in {'open', 'status', 'close'}:
                    result['session_at'] = None
                if row['action'].startswith('workflow-') or row['action'] in {'provider-tick', 'cleanup-tick'}:
                    result['workflow_at'] = None
                if row['action'] == 'readiness':
                    result['readiness_at'] = None
                continue
            key = row['projection_kind']
            if key in {'catalog', 'session', 'workflow', 'readiness', 'terminal_receipt', 'observation'}:
                result[key] = row['projection']
                if key in {'session', 'workflow', 'readiness', 'observation'}:
                    result[key + '_at'] = row['at']
            # A tick only requests provider/cleanup processing. It does not
            # prove an advanced workflow state. Require a new status receipt.
            if row['action'] in {'provider-tick', 'cleanup-tick'}:
                result['workflow_at'] = None
        return result

    @staticmethod
    def fresh(value, at, label):
        require(value is not None and at is not None, 'Read fresh ' + label + ' before preparing this operation')
        elapsed = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(at)).total_seconds()
        require(0 <= elapsed <= 30, 'Recorded ' + label + ' is stale; request fresh status first')
        return value

    def current_connections(self, binding):
        connections = self.app.connectors.connections()
        for pin in binding['connections'].values():
            row = connections.get(pin['name'])
            require(row and row['id'] == pin['connection_id'], 'Windows provider changed; this binding cannot be redirected')
            self.app.connectors.config(row)

    def arguments(self, job, action, supplied, current_plan_id=None):
        require(action in ACTIONS and type(supplied) is dict, 'Select a supported Windows operation')
        state = self.state(job)
        binding = state['binding']
        self.current_connections(binding)
        if action in {'catalog', 'readiness'}:
            require(not supplied, 'This operation accepts no caller-selected arguments')
            args = {}
        elif action == 'open':
            require(not supplied and state['catalog'] and not state['session'], 'Read the enrolled catalog before opening a new Windows session')
            require(not any(r.get('kind') == 'mcp.approval' and r['plan_id'] != current_plan_id
                            and self.db.record('artifacts', r['plan_id']).get('windows_action') == 'open'
                            for r in self.db.records('artifacts', job['id'])), 'An open was already submitted; inspect its outcome without opening another session')
            for other in self.db.jobs():
                if other['id'] != job['id'] and other.get('windows_binding_id'):
                    observed = self.state(other)
                    started = observed['session'] is not None or any(r.get('kind') == 'mcp.approval'
                        and self.db.record('artifacts', r['plan_id']).get('windows_action') == 'open'
                        for r in self.db.records('artifacts', other['id']))
                    require(not started or observed['session'] and observed['session']['lifecycle'] == 'COLD'
                            and not observed['session']['slotHeld'] and other.get('tool_delivery') not in {'CALLING', 'UNKNOWN'},
                            'Another tracked Windows task has not proven its session closed')
            args = {'targetId': binding['target_id'], 'mode': 'interactive',
                    'idempotencyKey': binding['id'], 'purpose': binding['purpose']}
        elif action in {'status', 'close', 'observe'}:
            require(not supplied and state['session'], 'Inspect the exact session before this operation')
            session = state['session']
            args = {'sessionId': session['sessionId']}
            if action != 'status':
                self.fresh(session, state['session_at'], 'session status')
                require(session['lifecycle'] != 'COLD' and session['slotHeld'], 'The recorded Windows session is not active')
            if action == 'close':
                args.update(expectedStateDigest=session['stateDigest'], purpose=binding['purpose'])
            if action == 'observe':
                require(session['lifecycle'] == 'HOT', 'Wait for a HOT Windows session before observation')
                if state['observation_at']:
                    elapsed = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(state['observation_at'])).total_seconds()
                    require(elapsed >= 1, 'Windows observations are limited to one frame per second')
                args = {'limit': 80}
        elif action == 'workflow-start':
            require(not state['workflow'] and set(supplied) == {'workflowDigest', 'dryRunDigest', 'inputEnvelopePath', 'inputExpectedSHA256'},
                    'Supply one exact approved workflow, dry run and sealed-input reference')
            for field in ('workflowDigest', 'dryRunDigest', 'inputExpectedSHA256'):
                sha(supplied[field])
            require(type(supplied['inputEnvelopePath']) is str and supplied['inputEnvelopePath'].startswith('/'), 'Use the exact owner-side sealed-input path')
            args = clone(supplied)
        else:
            require(not supplied and state['workflow'], 'Read the exact workflow before this operation')
            workflow = state['workflow']
            args = {'runId': workflow['runId']}
            if action not in {'workflow-status', 'workflow-receipt'}:
                self.fresh(workflow, state['workflow_at'], 'workflow status')
                args['expectedStateDigest'] = workflow['stateDigest']
                allowed = {'workflow-step': {'ready', 'awaiting-provider', 'cleanup-required'},
                           'provider-tick': {'awaiting-provider'}, 'cleanup-tick': {'cleanup-required'},
                           'workflow-pause': RUN_STATES}
                require(workflow['status'] in allowed[action], 'This workflow stage does not admit that operation')
            if action == 'workflow-receipt':
                require(workflow['status'] in TERMINAL, 'The workflow has no reported terminal receipt yet')
        needs_actuation = action in {'workflow-start', 'provider-tick'} or (
            action == 'workflow-step' and state['workflow']['status'] != 'cleanup-required')
        if needs_actuation:
            session = self.fresh(state['session'], state['session_at'], 'session status')
            require(session['lifecycle'] == 'HOT' and session['slotHeld'], 'Workflow action requires this exact HOT session')
            ready = self.fresh(state['readiness'], state['readiness_at'], 'provider readiness')
            require(ready.get('allowExecution') is True and ready.get('mutatingExecutionReady') is True,
                    'GhostBridge execution enrollment is not ready; model selection cannot grant it')
        return state, args

    def prepare(self, job, action, supplied):
        state, args = self.arguments(job, action, supplied)
        binding = state['binding']
        role = 'legacy' if action in LEGACY else 'workflow'
        record = self.app.connectors.prepare_plan(job, binding['connections'][role]['name'], ACTIONS[action], args)
        record = {k: v for k, v in record.items() if k not in {'seal', 'plan_hash'}}
        record.update(windows_binding_id=binding['id'], windows_action=action, windows_head=state['head'])
        record['plan_hash'] = digest(record)
        record = self.core.seal(record)
        self.core.save(job['id'], 'windows.proposed', {'tool_plan_id': record['id'], 'tool_delivery': 'PROPOSED'},
                       'Inspect this exact Windows request, then approve it with /tool-run. Native consent still applies.',
                       record, self.app.command('windows-plan', job))
        return {'tool_plan': record, 'action_taken': False}

    def guard(self, job, plan):
        if not job.get('windows_binding_id'):
            require(not plan.get('windows_binding_id'), 'A Windows request lost its task binding')
            return
        require(plan.get('windows_binding_id') == job['windows_binding_id'], 'Use /windows-plan for this bound Windows task')
        action = plan.get('windows_action')
        state, args = self.arguments(job, action, plan['arguments'] if action == 'workflow-start' else {}, current_plan_id=plan['id'])
        binding = state['binding']
        role = 'legacy' if action in LEGACY else 'workflow'
        pin = binding['connections'][role]
        require(plan['windows_head'] == state['head'] and plan['arguments'] == args and plan['tool'] == ACTIONS[action]
                and plan['connection_id'] == pin['connection_id'] and plan['server'] == pin['name'],
                'Windows target, session, state or exact request changed; inspect fresh evidence')

    def project(self, state, action, value, args):
        binding = state['binding']
        if action == 'catalog':
            require(value.get('schema') == 'ghostbridge.owner-session-catalog-view.v1', 'Unknown owner catalog schema')
            entries = [r for r in value.get('targets', []) if r.get('targetId') == binding['target_id']]
            require(len(entries) == 1 and entries[0].get('resourceClass') == 'vm' and 'interactive' in entries[0].get('allowedModes', []),
                    'The exact Windows target is not enrolled for interactive VM use')
            return 'catalog', {'digest': sha(value['catalogDigest']), 'target': entries[0]}
        if action in {'open', 'status', 'close'}:
            require(value.get('schema') == 'ghostbridge.owner-session-result.v1', 'Unknown lifecycle response schema')
            session = value.get('session')
            require(type(session) is dict and session.get('schema') == 'ghostbridge.owner-session-state.v1'
                    and session.get('targetId') == binding['target_id'] and session.get('resourceClass') == 'vm'
                    and session.get('mode') == 'interactive', 'Lifecycle response belongs to a different target or mode')
            require(type(session.get('sessionId')) is str and re.fullmatch('[0-9a-f-]{36}', session['sessionId']), 'Invalid exact owner session ID')
            require(str(uuid.UUID(session['sessionId'])) == session['sessionId'], 'Use the exact canonical owner session ID')
            if state['session']:
                require(session['sessionId'] == state['session']['sessionId'], 'Owner session changed; substitution refused')
            if action != 'open':
                require(session['sessionId'] == args['sessionId'], 'Lifecycle response does not match the requested session')
            sha(session.get('stateDigest'))
            require(session.get('lifecycle') in {'COLD', 'WARM', 'HOT', 'UNCERTAIN'} and type(session.get('slotHeld')) is bool,
                    'Invalid lifecycle or VM-slot evidence')
            require(session['lifecycle'] != 'COLD' or not session['slotHeld'], 'COLD evidence must release the VM slot')
            return 'session', session
        if action == 'observe':
            require(value.get('schema') == 'ghostbridge.legacy-accessibility-tree.v1'
                    and value.get('platform') == binding['target_id'].split('.')[1], 'Observation is not from the bound Windows generation')
            return 'observation', {'raw_hash': digest(value), 'captured_at': value.get('capturedAt'),
                                   'session_identity_proven': False,
                                   'authority': 'frame-bound untrusted observation; no action address or verified postcondition'}
        if action == 'readiness':
            require(value.get('schema') == 'ghostbridge.vcard-mcp-status.v1', 'Unknown workflow readiness schema')
            return 'readiness', {k: value.get(k) for k in ('allowExecution', 'mutatingExecutionReady', 'mutatingExecutionReadinessBlockers')}
        if action == 'workflow-receipt':
            receipt = value.get('receipt')
            require(type(receipt) is dict and state['workflow'] and receipt.get('runId') == state['workflow']['runId']
                    and receipt.get('workflowDigest') == state['workflow']['workflowDigest'] and type(receipt.get('cleanup')) is dict,
                    'Terminal receipt lacks the exact workflow and cleanup binding')
            return 'terminal_receipt', receipt
        if action in {'provider-tick', 'cleanup-tick'}:
            return 'provider_report', {'raw_hash': digest(value), 'state_verified': False}
        require(value.get('schema') == 'ghostbridge.vcard-run-state.v1' and type(value.get('session')) is dict, 'Unknown workflow state schema')
        session = state['session']
        require(session and value['session'].get('sessionId') == session['sessionId']
                and value['session'].get('systemId') == binding['system_id'] and value['session'].get('profileId') == binding['profile_id']
                and value.get('mode') == 'interactive', 'Workflow lacks the exact owner session, generation and profile binding')
        require(type(value.get('runId')) is str and re.fullmatch('[0-9a-f]{32}', value['runId']) and value.get('status') in RUN_STATES,
                'Malformed workflow run or stage')
        sha(value.get('stateDigest')); sha(value.get('workflowDigest'))
        require(type(value.get('terminalSuccess')) is bool and (not value['terminalSuccess'] or value['status'] == 'completed'),
                'Workflow terminal success contradicts its state')
        if state['workflow']:
            require(value['runId'] == state['workflow']['runId'] and value['workflowDigest'] == state['workflow']['workflowDigest']
                    and value['session'] == state['workflow']['session'], 'Workflow identity or session changed')
        if action == 'workflow-start':
            require(value['workflowDigest'] == args['workflowDigest'] and value.get('dryRunDigest') == args['dryRunDigest'],
                    'Started workflow differs from its approved input')
        else:
            require(value['runId'] == args['runId'], 'Workflow response belongs to another request')
        return 'workflow', value

    def retain(self, job, plan, raw, call_id, raw_id):
        if not job.get('windows_binding_id'):
            return
        state = self.state(job)
        action = plan['windows_action']
        try:
            kind, projection = self.project(state, action, response_value(raw), plan['arguments'])
            error = None
        except (Refused, KeyError, TypeError, ValueError) as exc:
            kind, projection, error = None, None, str(exc) if isinstance(exc, Refused) else 'Malformed Windows provider response'
        record = self.core.seal({'id': identity('winreceipt_'), 'kind': 'windows.receipt', 'at': now(),
            'binding_id': state['binding']['id'], 'job_id': job['id'], 'action': action,
            'call_id': call_id, 'raw_id': raw_id, 'raw_hash': digest(raw), 'plan_id': plan['id'],
            'classification': 'INVALID_OR_ERROR' if error else 'REMOTE_REPORT', 'projection_kind': kind,
            'projection': projection, 'error': error, 'authority': 'recorded provider report; no independent native correctness claim'})
        self.core.save(job['id'], 'windows.response', {}, 'Windows response retained; native state and cleanup remain separate.', record)
        require(error is None, error)

    def sync(self, job, call_id):
        require(not self.core.active, 'Wait for the active request before linking inspection evidence')
        call, raw = self.app.connectors.observed(call_id)
        plans = [self.core.check_seal(r) for r in self.db.records('artifacts', call['job_id'])
                 if r.get('kind') == 'mcp.plan' and r.get('plan_hash') == call['plan_hash']]
        require(len(plans) == 1, 'Inspection request is not uniquely bound')
        plan = plans[0]
        action = {'legacy_session_status': 'status', 'vcard_run_status': 'workflow-status'}.get(plan['tool'])
        require(action is not None, 'Only separately recorded status reads can be linked; this does not resolve or resend an action')
        binding = self.binding(job)
        pin = binding['connections']['legacy' if action == 'status' else 'workflow']
        require(plan['connection_id'] == pin['connection_id'] and plan['server'] == pin['name'], 'Inspection came from a different provider')
        require(not any(r['call_id'] == call_id for r in self.records(job)), 'This inspection is already recorded; its age cannot be reset')
        # Use the observation's original timestamp, never the link time, for
        # freshness. A copied historical read cannot refresh a CAS token.
        state = self.state(job)
        kind, projection = self.project(state, action, response_value(raw['result']), plan['arguments'])
        previous = [r['at'] for r in self.records(job) if r.get('projection_kind') == kind and r['classification'] == 'REMOTE_REPORT']
        require(not previous or dt.datetime.fromisoformat(raw['at']) >= max(dt.datetime.fromisoformat(at) for at in previous),
                'Inspection predates newer retained Windows evidence; state cannot rewind')
        record = self.core.seal({'id': identity('winreceipt_'), 'kind': 'windows.receipt', 'at': raw['at'],
            'linked_at': now(), 'binding_id': binding['id'], 'job_id': job['id'], 'action': action,
            'call_id': call_id, 'raw_id': raw['id'], 'raw_hash': raw['result_hash'], 'plan_id': plan['id'],
            'classification': 'REMOTE_REPORT', 'projection_kind': kind, 'projection': projection, 'error': None,
            'authority': 'separate recorded inspection; uncertainty and original consumed action remain retained'})
        self.core.save(job['id'], 'windows.inspection_linked', {}, 'Inspection evidence linked. No action was replayed or signed off.', record,
                       self.app.command('windows-sync', job))
        return self.view(self.db.job(job['id']))

    def view(self, job):
        state = self.state(job)
        session, workflow = state['session'], state['workflow']
        return {'job_id': job['id'], 'target_id': state['binding']['target_id'], 'profile_id': state['binding']['profile_id'],
            'session': session, 'workflow': workflow, 'provider_readiness': state['readiness'],
            'observation': state['observation'], 'terminal_receipt': state['terminal_receipt'],
            'delivery': job.get('tool_delivery', 'NONE'), 'last_error': state['last_error'],
            'session_closed_reported': bool(session and session['lifecycle'] == 'COLD' and not session['slotHeld']),
            'workflow_completed_reported': bool(workflow and workflow['status'] == 'completed' and workflow['terminalSuccess']),
            'native_qualification': 'not established by coordinator records', 'automatic_resend': False,
            'receipts': self.records(job), 'message': 'Recorded Windows activity. Session closure, workflow completion and cleanup proof are separate.'}
