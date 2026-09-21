from __future__ import annotations

import asyncio
import getpass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from contextlib import contextmanager

from jira_monkey import Store as FileStore, private_directory
from .common import (ACTIVE_DELIVERY, Refused, clone, digest, encoded, identity,
                     invariant, material_hash, now, require, ticket_snapshot)
from .config import configuration, recipe
from .platform_files import StateLease, check_state_file

TABLES = {"snapshots", "attempts", "drafts", "reviews", "approvals", "outbox", "provider_calls", "artifacts", "schedules"}


class Database:
    """All writes run synchronously on the application's event-loop thread."""
    def __init__(self, root, recover=True):
        require('..' not in Path(root).parts, 'Parent traversal in state path')
        self.root = Path(root).absolute()
        private_directory(self.root)
        self.fd = None
        self.lease = None
        self.db = None
        self.audit = None
        self.failed = False
        self.changed = asyncio.Event()
        try:
            try:
                self.lease = StateLease(self.root / 'lock')
                self.fd = self.lease.fd
            except BlockingIOError:
                raise Refused("Another Jira Monkey owns this state directory; use its open prompt") from None
            path = self.root / "monkey.sqlite3"
            require(not any((self.root / name).is_symlink() for name in ("monkey.sqlite3", "monkey.sqlite3-wal", "monkey.sqlite3-shm")), "Linked database refused")
            for name in ('monkey.sqlite3','monkey.sqlite3-wal','monkey.sqlite3-shm'):
                candidate=self.root/name
                if candidate.exists():
                    check_state_file(candidate)
            self.db = sqlite3.connect(path, timeout=0.25, isolation_level=None)
            if os.name != 'nt':
                os.chmod(path, 0o600)
            check_state_file(path)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=250")
            self.migrate()
            from .audit import Audit
            self.audit = Audit(self)
            if recover:
                self.recover_startup()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lease is not None:
            self.lease.close()
            self.lease = None
            self.fd = None

    @contextmanager
    def transaction(self):
        require(not self.failed, "Fatal storage error; new mutations stopped. Preserve " + str(self.root))
        try:
            self.db.execute("BEGIN IMMEDIATE")
            yield
            if self.audit:
                self.audit.commit()
            self.db.commit()
            if self.audit:
                self.audit.checkpoint()
            self.changed.set()
        except BaseException as exc:
            self.db.rollback()
            if isinstance(exc, (sqlite3.Error, OSError)):
                self.failed = True
            raise

    def migrate(self):
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        require(version <= 4, "Database is newer than this application; restore the matching executable")
        if version in {2,3,4}:
            return
        if version == 1:
            path = self.root / ("backup-schema1-" + identity() + ".sqlite3")
            backup = sqlite3.connect(path)
            try:
                self.db.backup(backup)
            finally:
                backup.close()
            if os.name != 'nt':
                os.chmod(path, 0o600)
            check_state_file(path)
            with self.transaction():
                self.db.execute("CREATE TABLE schedules(id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), data TEXT NOT NULL)")
                self.db.execute("CREATE INDEX schedules_job ON schedules(job_id)")
                self.db.execute("PRAGMA user_version=2")
            return
        old_files = sorted(self.root.glob("job-*.json"))
        legacy = FileStore(self.root)
        old_jobs = [legacy.read(p.name) for p in old_files]
        for job in old_jobs:
            ticket_snapshot(job["ticket"])
        if old_files or (self.root / "config.json").exists():
            backup = self.root / ("backup-v0-" + identity())
            backup.mkdir(mode=0o700)
            for p in [*old_files, self.root / "config.json"]:
                if p.exists():
                    shutil.copy2(p, backup / p.name)
        with self.transaction():
            self.db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY, issue_key TEXT NOT NULL, site TEXT NOT NULL, version INTEGER NOT NULL, work_state TEXT NOT NULL, data TEXT NOT NULL)")
            for table in sorted(TABLES):
                self.db.execute(f"CREATE TABLE {table}(id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), data TEXT NOT NULL)")
                self.db.execute(f"CREATE INDEX {table}_job ON {table}(job_id)")
            self.db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, job_id TEXT REFERENCES jobs(id), kind TEXT NOT NULL, created_at TEXT NOT NULL, job_version INTEGER, data TEXT NOT NULL)")
            self.db.execute("CREATE INDEX events_job ON events(job_id,seq)")
            self.db.execute("CREATE INDEX jobs_state ON jobs(work_state)")
            self.db.execute("CREATE TABLE commands(id TEXT PRIMARY KEY, job_id TEXT REFERENCES jobs(id), data TEXT NOT NULL)")
            self.db.execute("CREATE TABLE chat_messages(id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, data TEXT NOT NULL)")
            self.db.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            for old in old_jobs:
                self._import_legacy(old)
            self.db.execute("PRAGMA user_version=2")

    def config(self):
        p = self.root / "config.json"
        return configuration(FileStore(self.root).read("config.json") if p.exists() else None)

    def configure(self, value):
        require(not self.failed, "Storage is unavailable")
        value=configuration(value)
        before=digest(self.config())
        after=digest(value)
        if before==after and (self.root/'config.json').exists():
            return
        data={'path':str(self.root/'config.json'),'before_hash':before,'after_hash':after,
              'model_routes':value['model_routes'],'routing_policy':value['routing_policy']}
        if self.audit: self.audit.observe('configuration.write.requested',data)
        FileStore(self.root).write("config.json", value)
        require(digest(self.config())==after,'Configuration read-back differs from the requested value')
        if self.audit: self.audit.observe('configuration.write.completed',data)

    def _insert_job(self, job):
        self.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)", (job["id"], job["key"], job["site"], job["version"], job["work_state"], encoded(job).decode()))

    def record(self, table, record_id):
        require(table in TABLES, "Invalid record type")
        row = self.db.execute(f"SELECT data FROM {table} WHERE id=?", (record_id,)).fetchone()
        require(row is not None, "Missing " + table + " record")
        return json.loads(row[0])

    def records(self, table, job_id=None):
        require(table in TABLES, "Invalid record type")
        return [json.loads(r[0]) for r in self.db.execute(f"SELECT data FROM {table}" + (" WHERE job_id=?" if job_id else "") + " ORDER BY rowid", (job_id,) if job_id else ())]

    def _record(self, table, record_id, job_id, data):
        require(table in TABLES, "Invalid record type")
        self.db.execute(f"INSERT INTO {table} VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (record_id, job_id, encoded(data).decode()))

    def job(self, job_id):
        row = self.db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        require(row is not None, "Unknown job ID: " + str(job_id))
        return json.loads(row[0])

    def jobs(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM jobs ORDER BY rowid")]

    def _event(self, job, kind, data):
        self.db.execute("INSERT INTO events(event_id,job_id,kind,created_at,job_version,data) VALUES(?,?,?,?,?,?)",
                        (identity("evt_"), job["id"] if job else None, kind, now(), job["version"] if job else None, encoded({"schema_version": 1, **data}).decode()))

    def events(self, after=0, job_id=None, limit=1000):
        rows = self.db.execute("SELECT * FROM events WHERE seq>?" + (" AND job_id=?" if job_id else "") + " ORDER BY seq LIMIT ?", (after, job_id, limit) if job_id else (after, limit))
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def sequence(self):
        return self.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def receipt(self, command_id):
        row = self.db.execute("SELECT data FROM commands WHERE id=?", (command_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def change(self, job_id, version, kind, update, *, payload=None, command=None, records=()):
        with self.transaction():
            if command and (prior := self.receipt(command["command_id"])):
                return self.job(job_id), prior
            old = self.job(job_id)
            require(old["version"] == version, "Job changed; inspect it and retry the command")
            job = clone(old)
            if callable(update):
                update(job)
            else:
                job.update(update)
            job["version"] += 1
            job["updated_at"] = now()
            invariant(old, job)
            if job.get('windows_binding_id') and not old.get('windows_binding_id'):
                # Older executables lack the exact-session request guard.
                # Commit this floor with the binding, receipt and event.
                self.db.execute('PRAGMA user_version=4')
            self.db.execute("UPDATE jobs SET version=?,work_state=?,data=? WHERE id=? AND version=?", (job["version"], job["work_state"], encoded(job).decode(), job_id, version))
            for table, rid, record in records:
                self._record(table, rid, job_id, record)
            self._event(job, kind, payload or {})
            receipt = {"command": command, "event_seq": self.sequence(), "job_id": job_id,
                       "work_state": job["work_state"], "delivery_state": job["delivery_state"],
                       "job_version": job["version"], "message": (payload or {}).get("message", kind)}
            if command:
                self.db.execute("INSERT INTO commands VALUES(?,?,?)", (command["command_id"], job_id, encoded(receipt).decode()))
        return job, receipt

    def _new(self, ticket, c, fixture, issue_id=None, job_id=None):
        sid = identity("snapshot_")
        job_id = job_id or "JM-" + identity()[:12]
        snapshot = {"id": sid, "schema_version": 1, "ticket": clone(ticket), "content_hash": digest(ticket),
                    "material_hash": material_hash(ticket), "captured_at": now(), "origin": ticket["instance"],
                    "issue_id": issue_id, "server_updated": ticket["revision"]}
        job = {"id": job_id, "site": ticket["instance"], "key": ticket["key"], "source": ticket["source"], "issue_id": issue_id,
               "version": 1, "work_state": "QUEUED", "stage": None, "delivery_state": "NONE",
               "snapshot_id": sid, "recipe": recipe(c), "fixture": fixture,
               "limits": {"attempts": c["max_attempts"], "calls": c["max_provider_calls"], "retries": c["max_transport_retries"]},
               "attempt_count": 0, "call_count": 0, "retry_count": 0, "draft_id": None, "review_id": None,
               "approval_id": None, "outbox_id": None, "triage": None, "pause_after": None,
               "resume_stage": None, "resume_state": None, "operation_token": None, "feedback": [],
               "created_at": now(), "updated_at": now(), "reason": "Captured; no model called"}
        return job, snapshot

    def add(self, ticket, *, fixture=False, issue_id=None, command=None):
        ticket_snapshot(ticket)
        for j in self.jobs():
            s = self.record("snapshots", j["snapshot_id"])
            if s["content_hash"] == digest(ticket) and j["fixture"] == fixture:
                return j
        job, snapshot = self._new(ticket, self.config(), fixture, issue_id)
        with self.transaction():
            self._insert_job(job)
            self._record("snapshots", snapshot["id"], job["id"], snapshot)
            self._event(job, "ticket.captured", {"snapshot_id": snapshot["id"], "message": "Ticket snapshot captured"})
            if command:
                self.db.execute("INSERT INTO commands VALUES(?,?,?)", (command["command_id"], job["id"], encoded({"command": command, "job_id": job["id"], "event_seq": self.sequence(), "message": "Ticket captured and queued"}).decode()))
        return job

    def add_work(self,objective,work_type,scope,command):
        import time
        require(work_type in {'build','research','self-improvement'},'Unknown general work type')
        c=self.config()
        ticket={'source':'local','instance':'local://monkey','key':work_type.upper()+'-'+identity()[:8],
            'revision':now(),'title':' '.join(objective.split())[:240],'body':objective}
        job,snapshot=self._new(ticket,c,False)
        job.update(work_type=work_type,agent_scope=clone(scope),agent_limits={'calls':c['agent_max_calls'],
            'tools':c['agent_max_tools'],'seconds':c['agent_max_seconds']},agent_tool_count=0,
            agent_state='READY',agent_feedback=[],agent_plan_id=None,agent_result_id=None,agent_deadline=time.time()+c['agent_max_seconds'],
            agent_signoff_id=None,work_state='WAITING_USER',reason='General work captured; no ticket or pre-existing verifier required')
        job['limits']['calls']=c['agent_max_calls']
        invariant(None,job)
        with self.transaction():
            # Older runtimes do not enforce the captured application recipe.
            # Raise the compatibility floor atomically with the first such job,
            # so an executable rollback cannot silently execute it without pins.
            if 'application_runtime' in scope and self.db.execute('PRAGMA user_version').fetchone()[0] < 3:
                self.db.execute('PRAGMA user_version=3')
            self._insert_job(job)
            self._record('snapshots',snapshot['id'],job['id'],snapshot)
            self._event(job,'agent.captured',{'message':'General '+work_type+' work captured','scope_hash':digest(scope),'snapshot_id':snapshot['id'],
                'database_version':self.db.execute('PRAGMA user_version').fetchone()[0]})
            self.db.execute('INSERT INTO commands VALUES(?,?,?)',(command['command_id'],job['id'],encoded({'command':command,'job_id':job['id'],'event_seq':self.sequence()}).decode()))
        return job

    def recover_startup(self):
        for job in self.jobs():
            update, records = {}, []
            if job["work_state"] == "RUNNING":
                update.update(work_state="INTERRUPTED", operation_token=None, reason="Foreground process ended during an attempt; explicit recover required")
            if job["delivery_state"] == "POSTING":
                out = self.record("outbox", job["outbox_id"])
                state = "POSTED_UNVERIFIED" if out.get("comment_id") else "POST_UNKNOWN"
                out.update(state=state, recovery_at=now())
                records.append(("outbox", out["id"], out))
                update["delivery_state"] = state
            if update:
                self.change(job["id"], job["version"], "recovery.classified", update, records=records,
                            payload={"message": "Interrupted work/delivery retained; no requests replayed"})

    def _import_legacy(self, old):
        c = configuration(old["config"])
        job, snapshot = self._new(old["ticket"], c, old["fixture"], job_id=old["id"])
        job["attempt_count"] = len(old["attempts"])
        # Reserve a conservative floor for legacy calls whose counters were absent.
        job["call_count"] = min(c["max_provider_calls"], sum(1 + int("triage" in a) + int("result" in a) for a in old["attempts"]))
        state = old["state"]
        job["work_state"] = {"queued": "QUEUED", "triaging": "INTERRUPTED", "working": "INTERRUPTED", "reviewing": "INTERRUPTED", "rejected": "REJECTED"}.get(state, "WAITING_USER")
        job["reason"] = "Imported preview evidence; exact approval must be renewed"
        self._insert_job(job)
        self._record("snapshots", snapshot["id"], job["id"], snapshot)
        self._record("artifacts", "legacy_" + job["id"], job["id"], old)
        for ordinal, prior in enumerate(old["attempts"], 1):
            attempt_id = f"{job['id']}:{ordinal}"
            attempt = {"id": attempt_id, "number": ordinal, "snapshot_id": snapshot["id"],
                       "state": "COMPLETED" if "review" in prior else "INTERRUPTED",
                       "legacy": True, "original": prior}
            self._record("attempts", attempt_id, job["id"], attempt)
            if "result" not in prior or "text" not in prior["result"]:
                continue
            from .worker import comment_payload
            text = prior["result"]["text"]
            did, operation = identity("draft_"), identity("op_")
            payload = comment_payload(text, operation, c["visibility"])
            draft = {"id": did, "snapshot_id": snapshot["id"], "number": ordinal,
                     "text": text, "payload": payload, "payload_hash": digest(payload), "text_hash": digest(text),
                     "operation_id": operation, "created_at": None, "complete": True, "legacy": True}
            self._record("drafts", did, job["id"], draft)
            job.update(draft_id=did, delivery_state="DRAFT", triage=prior.get("triage"))
            if "review" in prior:
                rid = identity("review_")
                previous = prior["review"]
                result = {"verdict": {"pass": "PASS", "retry": "REVISE", "human": "NEEDS_INPUT"}[previous["decision"]],
                          "findings": [{"code": "LEGACY_REVIEW", "severity": "info", "claim_or_excerpt": previous["reason"],
                                        "evidence_refs": [snapshot["id"]], "suggested_revision": "\n".join(previous["feedback"])}],
                          "unresolved_questions": []}
                review = {"id": rid, "draft_id": did, "draft_hash": draft["payload_hash"], "snapshot_id": snapshot["id"], "result": result, "legacy": True}
                self._record("reviews", rid, job["id"], review)
                job["review_id"] = rid
        if job["review_id"] and state not in {"queued", "triaging", "working", "reviewing", "rejected"}:
            last_review = self.record("reviews", job["review_id"])
            if last_review["result"]["verdict"] == "PASS":
                job["work_state"] = "DRAFT_READY"
        self.db.execute("UPDATE jobs SET data=?,work_state=? WHERE id=?", (encoded(job).decode(), job["work_state"], job["id"]))
        if old.get("delivery"):
            out_id = identity("outbox_")
            delivery = "POSTED_UNVERIFIED" if old["delivery"].get("comment_id") else "POST_UNKNOWN"
            out = {"id": out_id, "state": delivery, "legacy": True, "body": old["delivery"]["body"],
                   "comment_id": old["delivery"].get("comment_id"), "site": job["site"], "key": job["key"],
                   "reason": "Legacy delivery lacks author/property proof; read-only reconciliation required"}
            self._record("outbox", out_id, job["id"], out)
            job.update(outbox_id=out_id, delivery_state=delivery)
            self.db.execute("UPDATE jobs SET data=? WHERE id=?", (encoded(job).decode(), job["id"]))
        self._event(job, "migration.imported", {"message": "Preview evidence imported; original JSON and backup retained"})

    def chat(self, session, role, text, seq, target=None):
        with self.transaction():
            self.db.execute("INSERT INTO chat_messages(session_id,data) VALUES(?,?)", (session, encoded({"role": role, "text": text[:12000], "evidence_seq": seq, "target": target, "at": now()}).decode()))

    def session(self, session_id, data):
        with self.transaction():
            self.db.execute("INSERT INTO sessions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (session_id, encoded(data).decode()))

    def global_event(self, kind, data, command=None, record=None):
        with self.transaction():
            self._event(None, kind, data)
            receipt = {"command": command, "event_seq": self.sequence(), **data}
            if command:
                self.db.execute("INSERT INTO commands VALUES(?,?,?)", (command["command_id"], None, encoded(receipt).decode()))
            if record:
                table, rid, row = record
                self._record(table, rid, None, row)
        return receipt
