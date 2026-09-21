"""Versioned sprint schedules; local civil time must resolve to one instant."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .common import ACTIVE_DELIVERY, clean_text, clone, digest, identity, now, require


def instant(value, timezone):
    require(type(value) is str and ("T" in value or " " in value), "Give a date and time, for example 2026-09-14 09:00")
    try:
        zone = ZoneInfo(timezone)
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        require(False, "Use an ISO date/time and a valid IANA timezone, such as America/New_York")
    if parsed.tzinfo is not None:
        localized = parsed.astimezone(zone)
        require(localized.utcoffset() == parsed.utcoffset(), "The supplied UTC offset does not match that timezone at this date")
    else:
        possibilities = []
        for fold in (0, 1):
            candidate = parsed.replace(tzinfo=zone, fold=fold)
            if candidate.astimezone(dt.timezone.utc).astimezone(zone).replace(tzinfo=None) == parsed:
                possibilities.append(candidate)
        require(possibilities, "That local time does not exist during the daylight-saving transition")
        require(len({p.utcoffset() for p in possibilities}) == 1,
                "That local time occurs twice. Include the intended UTC offset explicitly")
        localized = possibilities[0]
    return localized.astimezone(dt.timezone.utc).isoformat(), localized.isoformat()


def schedule_hash(job):
    schedule = job.get("schedule")
    return digest(schedule) if schedule else None


class Scheduler:
    def __init__(self, app):
        self.app = app

    def execution_invalidation(self, job):
        update = {'mission_authorization_id':None,'mission_signoff_id':None,'tool_signoff_id':None}
        if job.get('mission_plan_id'):
            update['mission_state'] = 'NEEDS_INPUT'
        if job.get('tool_delivery') in {'SIGNED_OFF','MISSION_SIGNED_OFF'}:
            update['tool_delivery'] = 'RETURNED_UNVERIFIED'
        if (job.get('contract_id') or job.get('mission_plan_id')) and self.app.execution.active!=job['id']:
            update['execution_state'] = 'NEEDS_INPUT'
        return update

    def view(self, job):
        return {"job_id": job["id"], "key": job["key"], "schedule": clone(job.get("schedule")),
                "history": self.app.db.records("schedules", job["id"]),
                "actual_work_started": next((e["created_at"] for e in self.app.db.events(job_id=job["id"]) if e["kind"] == "attempt.reserved"), None),
                "note": "Planned dates are not evidence that work started or finished"}

    def assign(self, job, start, finish, timezone, note, command):
        require(job["work_state"] not in {"CANCELLED", "REJECTED"}, "Closed work needs a new follow-up ticket")
        start_utc, start_local = instant(start, timezone)
        finish_utc, finish_local = instant(finish, timezone)
        require(dt.datetime.fromisoformat(finish_utc) > dt.datetime.fromisoformat(start_utc), "Finish must be after start")
        clean_text(note, 2000, empty=True)
        previous = job.get("schedule")
        record = {"id": identity("schedule_"), "revision": (previous or {}).get("revision", 0) + 1,
                  "status": "SCHEDULED", "start_utc": start_utc, "finish_utc": finish_utc,
                  "start_local": start_local, "finish_local": finish_local, "timezone": timezone,
                  "note": note, "operator": command["operator_identity"], "recorded_at": now(),
                  "supersedes": (previous or {}).get("id")}
        update = {"schedule": record, "approval_id": None, "execution_signoff_id": None,
                  "plan_authorization_id": None, **self.execution_invalidation(job)}
        if job["delivery_state"] not in ACTIVE_DELIVERY:
            update["delivery_state"] = "DRAFT" if job["draft_id"] else "NONE"
        _, receipt = self.app.db.change(job["id"], job["version"], "schedule.assigned", update,
            command=command, records=[("schedules", record["id"], record)], payload={
                "message": "Sprint dates saved; prior sign-off and execution authorization require renewal",
                "schedule_id": record["id"], "start": start_local, "finish": finish_local, "timezone": timezone})
        return {**receipt, "schedule": record}

    def return_for_dates(self, job, note, command):
        clean_text(note, 2000)
        require(job["work_state"] not in {"CANCELLED", "REJECTED"}, "Closed work needs a new follow-up ticket")
        previous = job.get("schedule")
        record = {**(clone(previous) if previous else {}), "id": identity("schedule_"),
                  "revision": (previous or {}).get("revision", 0) + 1, "status": "RETURNED",
                  "note": note, "operator": command["operator_identity"], "recorded_at": now(),
                  "supersedes": (previous or {}).get("id")}
        update = {"schedule": record, "approval_id": None, "execution_signoff_id": None,
                  "plan_authorization_id": None, "reason": "Returned for new dates: " + note, **self.execution_invalidation(job)}
        if job["delivery_state"] not in ACTIVE_DELIVERY:
            update["delivery_state"] = "DRAFT" if job["draft_id"] else "NONE"
        if job["work_state"] == "RUNNING":
            update["pause_after"] = "now"
        elif job["work_state"] != "PAUSED":
            update.update(work_state="PAUSED", resume_state=job["work_state"], resume_stage=job["stage"])
        _, receipt = self.app.db.change(job["id"], job["version"], "schedule.returned", update,
            command=command, records=[("schedules", record["id"], record)], payload={
                "message": "Returned for new dates. Active drafting will pause at its next saved boundary" if job["work_state"] == "RUNNING" else "Returned for new dates; work is paused",
                "note": note, "schedule_id": record["id"]})
        if self.app.worker.active == job["id"]:
            self.app.worker.continuous = False
        return {**receipt, "schedule": record}
