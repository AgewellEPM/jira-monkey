from __future__ import annotations

import copy
import datetime as dt
import re
import unicodedata
import uuid
from pathlib import Path

from jira_monkey import (MAX_BYTES, PROVIDERS, Refused, adf_text, clean_text,
                         decode, digest, encoded, origin, require, ticket_snapshot as jira_snapshot)


def ticket_snapshot(value):
    require(isinstance(value, dict) and set(value) == {"source", "instance", "key", "revision", "title", "body"},
            "Expected the six ticket snapshot fields")
    if value["source"] == "jira":
        return jira_snapshot(value)
    require(type(value["source"]) is str and re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value["source"]), "Invalid ticket source label")
    if value["source"] == "local":
        require(value["instance"] == "local://monkey", "Local requests belong to local://monkey")
    else:
        origin(value["instance"])
    require(type(value["key"]) is str and re.fullmatch(r"[A-Za-z0-9#][A-Za-z0-9_.#/-]{0,127}", value["key"]), "Invalid ticket reference")
    clean_text(value["revision"], 128)
    clean_text(value["title"], 2048)
    clean_text(value["body"], 24000, empty=True)
    return value


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def identity(prefix=""):
    return prefix + uuid.uuid4().hex


def clone(value):
    return copy.deepcopy(value)


def safe(value):
    """Plain terminal text only. Never hand untrusted strings to an ANSI parser."""
    text = str(value)
    return "".join(c if (c in "\n\t" or unicodedata.category(c)[0] != "C")
                   else "\\u%04x" % ord(c) for c in text)


def read_import(path):
    path = Path(path).expanduser()
    require(not path.is_symlink() and path.is_file(), "Import must be an explicitly selected regular file")
    with path.open("rb") as stream:
        value = decode(stream.read(MAX_BYTES + 1))
    if isinstance(value, dict) and set(value) == {"ticket"}:
        value = value["ticket"]
    return ticket_snapshot(value)


def material_hash(ticket):
    # Server update timestamps alone are not material ticket evidence.
    return digest({k: v for k, v in ticket.items() if k != "revision"})


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


STR = {"type": "string", "maxLength": 6000}
STRINGS = {"type": "array", "items": STR, "maxItems": 12}
TRIAGE = object_schema({"goal": STR, "criteria": STRINGS, "unknowns": STRINGS,
                        "needs_human": {"type": "boolean"}, "reason": STR})
FINDING = object_schema({"code": STR, "severity": {"type": "string", "enum": ["info", "warning", "error"]},
                         "claim_or_excerpt": STR, "evidence_refs": STRINGS, "suggested_revision": STR})
REVIEW = object_schema({"verdict": {"type": "string", "enum": ["PASS", "REVISE", "NEEDS_INPUT"]},
                       "findings": {"type": "array", "items": FINDING, "maxItems": 12},
                       "unresolved_questions": STRINGS})


def validate(value, shape):
    """Validate the small supported JSON-schema dialect, independently of Ollama."""
    if "oneOf" in shape:
        matches = 0
        for branch in shape["oneOf"]:
            try:
                validate(value, branch)
                matches += 1
            except Refused:
                pass
        require(matches == 1, "Intent must match exactly one operation schema")
        return value
    kind = shape["type"]
    if kind == "object":
        require(type(value) is dict,'Expected object')
        properties=shape.get('properties',{})
        require(set(shape.get('required',[]))<=set(value),'Missing required fields')
        extras=set(value)-set(properties)
        require(not extras or shape.get('additionalProperties',True) is not False,'Unexpected fields')
        for key,rule in properties.items():
            if key in value: validate(value[key],rule)
        if extras:
            from .security import bounded_json
            bounded_json(value)
            if type(shape.get('additionalProperties')) is dict:
                for key in extras: validate(value[key],shape['additionalProperties'])
    elif kind == "array":
        require(type(value) is list and shape.get('minItems',0) <= len(value) <= shape.get("maxItems", 20), "Invalid bounded list")
        for item in value:
            validate(item, shape["items"])
    elif kind == "string":
        require(type(value) is str and len(value) <= shape.get("maxLength", 6000), "Invalid bounded string")
        clean_text(value, shape.get("maxLength", 6000) * 4, empty=True)
    elif kind == "integer":
        require(type(value) is int and shape.get('minimum',value)<=value<=shape.get('maximum',value), 'Invalid integer field')
    elif kind == "boolean":
        require(type(value) is bool, "Expected boolean")
    elif kind == "null":
        require(value is None, "Expected null")
    else:
        raise Refused("Unsupported schema type")
    if "enum" in shape:
        require(value in shape["enum"], "Unknown value")
    return value


ACTIVE_DELIVERY = {"POSTING", "POST_UNKNOWN", "POSTED_UNVERIFIED", "POSTED_VERIFIED", "POSTED_MISMATCH"}
WORK_STATES = {"QUEUED", "RUNNING", "DRAFT_READY", "WAITING_USER", "PAUSED", "FAILED", "CANCELLED", "REJECTED", "INTERRUPTED"}
DELIVERY_STATES = {"NONE", "DRAFT", "APPROVED", "STALE", "POST_REJECTED", *ACTIVE_DELIVERY}


def invariant(old, new):
    require(new["work_state"] in WORK_STATES and new["delivery_state"] in DELIVERY_STATES, "Illegal state")
    require(new["stage"] in {None, "TRIAGE", "DRAFT", "REVIEW"}, "Illegal stage")
    require(new["attempt_count"] <= new["limits"]["attempts"] and new["call_count"] <= new["limits"]["calls"], "Budget exceeded")
    if new.get('windows_binding_id'):
        require(not new.get('work_type') and not new.get('contract_id') and not new.get('mission_id')
                and new['work_state'] != 'RUNNING', 'Windows supervision cannot be combined with another execution mode')
    if new.get('work_type'):
        require(new['work_type'] in {'build','research','self-improvement'},'Unknown general work type')
        require(type(new.get('agent_tool_count',0)) is int and 0<=new.get('agent_tool_count',0)<=new['agent_limits']['tools'],'Original general tool budget exceeded')
        if old:
            require(new.get('agent_tool_count',0)>=old.get('agent_tool_count',0),'General tool counter cannot reset')
    if old and old.get('work_type'):
        for field in ('work_type','agent_scope','agent_limits','agent_deadline'):
            require(new.get(field)==old.get(field),'General work scope or original budget changed')
    for field, limit in (('agent_count',3),('tool_count',24),('execution_runs',3)):
        value = new.get(field,0)
        require(type(value) is int and 0 <= value <= limit,'Original '+field+' budget exceeded')
        require(not old or value >= old.get(field,0),'Original '+field+' counter cannot reset')
    if old:
        if old.get('windows_binding_id'):
            require(new.get('windows_binding_id') == old['windows_binding_id'], 'Windows target binding cannot be replaced')
        for key in ("id", "site", "key", "recipe", "limits", "fixture"):
            require(new[key] == old[key], "Immutable job recipe/identity changed")
        require(new["attempt_count"] >= old["attempt_count"] and new["call_count"] >= old["call_count"], "Counters cannot reset")
        if old["delivery_state"] in ACTIVE_DELIVERY:
            require(new["delivery_state"] in ACTIVE_DELIVERY or (old["delivery_state"] == "POSTING" and new["delivery_state"] == "POST_REJECTED"), "Delivery cannot become sendable again")
        transitions = {
            "QUEUED": {"RUNNING", "PAUSED", "CANCELLED", "REJECTED", "FAILED", "WAITING_USER"},
            "RUNNING": {"QUEUED", "PAUSED", "CANCELLED", "REJECTED", "FAILED", "WAITING_USER", "DRAFT_READY", "INTERRUPTED"},
            "DRAFT_READY": {"QUEUED", "PAUSED", "CANCELLED", "REJECTED", "WAITING_USER"},
            "WAITING_USER": {"QUEUED", "PAUSED", "CANCELLED", "REJECTED"},
            "FAILED": {"QUEUED", "PAUSED", "CANCELLED", "REJECTED"},
            "PAUSED": {"QUEUED", "DRAFT_READY", "WAITING_USER", "FAILED", "INTERRUPTED", "CANCELLED", "REJECTED"},
            "INTERRUPTED": {"WAITING_USER", "CANCELLED", "REJECTED"}, "CANCELLED": set(), "REJECTED": set(),
        }
        require(new["work_state"] == old["work_state"] or new["work_state"] in transitions[old["work_state"]], "Illegal work transition")
        if old["work_state"] in {"CANCELLED", "REJECTED"}:
            require(new["work_state"] == old["work_state"], "Closed work cannot reopen")
    if new["delivery_state"] == "APPROVED":
        require(new["approval_id"] and new["draft_id"], "Approval requires a completed candidate")
