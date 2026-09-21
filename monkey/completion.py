"""Recorded completion rules shared by exact commands and the terminal browser."""
from .common import Refused, digest, require
from .scheduling import schedule_hash


UNCERTAIN = {'POSTING', 'POST_UNKNOWN', 'POSTED_UNVERIFIED', 'POSTED_MISMATCH', 'STALE'}


def signed_off(app, job):
    if not job['approval_id'] or not job['draft_id'] or job['delivery_state'] in {'STALE', 'NONE', 'DRAFT'}:
        return None
    approval = app.db.record('approvals', job['approval_id'])
    draft = app.db.record('drafts', job['draft_id'])
    exact = approval['binding']
    if (exact['payload_hash'] != draft['payload_hash'] or digest(draft['payload']) != draft['payload_hash']
            or exact['snapshot_id'] != job['snapshot_id'] or exact.get('schedule_hash') != schedule_hash(job)):
        return None
    return approval


def completed_at(app, job):
    if (job['work_state'] in {'CANCELLED', 'REJECTED'} and job['delivery_state'] not in UNCERTAIN - {'STALE'}
            and job.get('tool_delivery') not in {'CALLING', 'UNKNOWN'}):
        return job['updated_at']
    if job.get('work_type'):
        try:
            return app.agent.completed_at(job)
        except (Refused, OSError):
            return None
    if job.get('schedule', {}).get('status') == 'RETURNED':
        return None
    mission_completed = None
    if job.get('mission_plan_id'):
        try:
            mission_completed = app.missions.completed_at(job)
            if not mission_completed:
                return None
        except (Refused, OSError):
            return None
    tool_completed = None
    if job.get('tool_plan_id'):
        try:
            tool_completed = mission_completed or app.connectors.completed_at(job)
            if not tool_completed:
                return None
        except (Refused, OSError):
            return None
    if job.get('contract_id'):
        if not job.get('execution_signoff_id') or job.get('execution_state') != 'COMPLETED':
            return None
        try:
            signoff = app.execution.record(job, 'execution_signoff_id')
            _, result, _ = app.execution.verify_result(job)
            require(signoff['result_hash'] == result['result_hash'], 'Stale project sign-off')
            return max(signoff['at'], tool_completed or '')
        except (Refused, OSError):
            return None
    if job['delivery_state'] in UNCERTAIN - {'STALE'}:
        return None
    if tool_completed:
        return tool_completed
    if job['work_state'] in {'CANCELLED', 'REJECTED'}:
        return job['updated_at']
    if job['delivery_state'] == 'STALE':
        return None
    approval = signed_off(app, job)
    if not approval:
        return None
    source = app.db.record('snapshots', job['snapshot_id'])['ticket']['source']
    if source == 'jira':
        if job['delivery_state'] != 'POSTED_VERIFIED':
            return None
        outbox = app.db.record('outbox', job['outbox_id'])
        return outbox.get('verification', {}).get('checked_at', outbox.get('updated_at'))
    return approval['approved_at']
