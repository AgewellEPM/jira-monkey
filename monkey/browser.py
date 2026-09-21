"""Keyboard ticket tree, evidence reader and completion calendar."""
from __future__ import annotations

import calendar
import datetime as dt
import textwrap

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.utils import get_cwidth

from .common import Refused, digest, require, safe
from .presentation import delivery, fit, padded, width
from .scheduling import schedule_hash
from .completion import completed_at, signed_off

MENU = ("🍌 Start ticket", "🌳 Current tickets", "🌅 Finished tickets", "📅 Past / completed tickets",'🍌 Connections','🍌 Build something','🍌 Research a topic')
TABS = ("Overview", "Draft", "Events", "Sign-off", "Dates", "Project", "Plan", "Tools",'Mission','Trace','Agent')


class Browser:
    def __init__(self, app):
        self.app = app
        self.section = 0
        self.pane = "menu"
        self.index = 0
        self.day = dt.datetime.now().astimezone().date()
        self.job_id = None
        self.tab = 0
        self.offset = 0
        self.approval_view = None
        self.date_action = 0
        self.calendar_mode = "completed"
        self.project_action = 0
        self.plan_view = None
        self.tool_view = None
        self.mission_view = None
        self.audit_view = None
        self.agent_view = None

    def signed_off(self, job):
        return signed_off(self.app, job)

    def completed_at(self, job):
        return completed_at(self.app, job)

    def groups(self):
        current, finished, past = [], [], []
        for job in self.app.db.jobs():
            completed = self.completed_at(job)
            if completed:
                past.append(job)
            else:
                current.append(job)
            if job["review_id"] or job.get("execution_state") in {"AWAITING_SIGNOFF", "COMPLETED",'AGENT_AWAITING_REVIEW'} or job.get('tool_delivery') in {'RETURNED_UNVERIFIED','SIGNED_OFF','REPORTED_ERROR','RESOLVED_WITH_EVIDENCE'} or job["work_state"] in {"CANCELLED", "REJECTED"}:
                finished.append(job)
        return current, finished, past

    def date_of(self, job):
        stamp = self.completed_at(job)
        return dt.datetime.fromisoformat(stamp).astimezone().date() if stamp else None

    def rows(self):
        groups = self.groups()
        rows = groups[self.section - 1] if 1<=self.section<=3 else []
        if self.section == 3 and self.pane == "list":
            rows = self.day_jobs()
        return list(reversed(rows))

    def planned_on(self, job, day):
        s = job.get("schedule", {})
        if s.get("status") != "SCHEDULED" or job["work_state"] in {"CANCELLED", "REJECTED"}:
            return False
        return dt.datetime.fromisoformat(s["start_local"]).date() <= day <= dt.datetime.fromisoformat(s["finish_local"]).date()

    def day_jobs(self):
        return ([j for j in self.app.db.jobs() if self.planned_on(j, self.day)] if self.calendar_mode == "planned"
                else [j for j in self.groups()[2] if self.date_of(j) == self.day])

    def move(self, amount):
        if self.pane == 'services':
            self.index = max(0,min(len(self.app.services.list()['services'])-1,self.index+amount))
            return
        if self.pane == "detail" and self.tab == 5:
            self.project_action = (self.project_action + amount) % 4
            return
        if self.pane == "menu":
            self.section = (self.section + amount) % len(MENU)
            self.index = 0
        elif self.pane == "calendar":
            self.day += dt.timedelta(days=7 * amount)
        elif self.pane == "detail":
            if self.tab == 4:
                self.date_action = max(0, min(1, self.date_action + amount))
            else:
                self.offset = max(0, self.offset + amount)
        else:
            self.index = max(0, min(len(self.rows()) - 1, self.index + amount))

    def back(self):
        if self.pane == "detail":
            self.pane = "list"
            self.approval_view = None
        elif self.pane == "list" and self.section == 3:
            self.pane = "calendar"
        else:
            self.pane = "menu"

    def slide(self, amount):
        if self.section in {5,6} and self.pane!='detail': return
        if self.section==4 and self.pane!='detail':
            if amount<0:
                self.back()
            else:
                self.pane = 'services'
            return
        if self.pane == "detail":
            self.tab = (self.tab + amount) % len(TABS)
            self.offset = 0
            self.approval_view = None
            if self.tab == 3:
                j = self.app.db.job(self.job_id)
                d = self.app.db.record("drafts", j["draft_id"]) if j["draft_id"] else None
                self.approval_view = {"version": j["version"], "draft": d, "key": j["key"]}
                if j.get("contract_id") and j.get("execution_state") == "AWAITING_SIGNOFF":
                    self.approval_view["execution"] = self.app.execution.record(j, "execution_id")
            if self.tab == 6:
                j = self.app.db.job(self.job_id)
                self.plan_view = {"version": j["version"], "plan": self.app.execution.record(j, "plan_id")} if j.get("plan_id") else None
            if self.tab == 7:
                j = self.app.db.job(self.job_id)
                self.tool_view = {'version':j['version'],'plan':self.app.execution.record(j,'tool_plan_id')} if j.get('tool_plan_id') else None
            if self.tab == 8:
                j = self.app.db.job(self.job_id)
                self.mission_view = {'version':j['version'],'plan':self.app.execution.record(j,'mission_plan_id'),
                    'result':self.app.execution.record(j,'mission_result_id') if j.get('mission_result_id') else None} if j.get('mission_plan_id') else None
            if self.tab == 9:
                try: self.audit_view=self.app.audit.view(self.job_id)
                except Refused as exc: self.audit_view={'error':str(exc)}
            if self.tab == 10:
                j=self.app.db.job(self.job_id)
                self.agent_view={'version':j['version'],'result':self.app.execution.record(j,'agent_result_id')} if j.get('agent_result_id') else None
        elif self.pane == "calendar":
            self.day += dt.timedelta(days=amount)
        elif amount < 0:
            self.back()
        elif self.section:
            self.pane = "calendar" if self.section == 3 else "list"
        # Start-ticket selection is handled by Enter, leaving arrows read-only.

    def month(self, amount):
        ordinal = self.day.year * 12 + self.day.month - 1 + amount
        year, month = divmod(ordinal, 12)
        self.day = dt.date(year, month + 1, min(self.day.day, calendar.monthrange(year, month + 1)[1]))

    def bindings(self, get_session, emit, enabled=lambda: True):
        keys = KeyBindings()
        empty = Condition(lambda: enabled() and not get_session().default_buffer.text)

        @keys.add("up", filter=empty)
        def up(event):
            self.move(-1)

        @keys.add("down", filter=empty)
        def down(event):
            self.move(1)

        @keys.add("left", filter=empty)
        def left(event):
            self.slide(-1)

        @keys.add("right", filter=empty)
        def right(event):
            self.slide(1)

        @keys.add("tab", filter=empty)
        def tab(event):
            if self.pane == "calendar":
                self.calendar_mode = "planned" if self.calendar_mode == "completed" else "completed"
            else:
                self.slide(1)

        @keys.add("escape", filter=empty)
        @keys.add("s-tab", filter=empty)
        def back(event):
            self.back()

        @keys.add("pageup", filter=empty)
        def pageup(event):
            self.month(-1) if self.pane == "calendar" else self.move(-6)

        @keys.add("pagedown", filter=empty)
        def pagedown(event):
            self.month(1) if self.pane == "calendar" else self.move(6)

        @keys.add("enter", filter=empty)
        async def enter(event):
            try:
                result = await self.activate()
                if result and "prefill" in result:
                    event.current_buffer.insert_text(result["prefill"])
                elif result:
                    emit(result)
            except Refused as exc:
                emit({"error": str(exc)})
            event.app.invalidate()
        return keys

    async def activate(self):
        if self.pane=='services':
            return self.app.services.form(self.app.services.list()['services'][self.index]['id'])
        if self.pane == "menu":
            if self.section in {5,6}: return {'prefill':'/build ' if self.section==5 else '/research '}
            if self.section == 0:
                return {"prefill": "/request "}
            self.slide(1)
            return None
        if self.pane == "calendar":
            self.pane, self.index = "list", 0
            return None
        if self.pane == "detail":
            j = self.app.db.job(self.job_id)
            if self.tab==10:
                require(j.get('work_type'),'Use /build or /research for general agent work')
                if j['agent_state']=='AWAITING_REVIEW':
                    viewed=self.agent_view
                    require(viewed and viewed['version']==j['version'],'Reopen Agent to inspect the current exact result')
                    return {'form':{'operation':'agent-accept','target':j['id'],'expected_version':j['version'],
                        'values':{'exact_hash':viewed['result']['result_hash']},'index':0,'fields':[('note','Your review of the actual result, checks and sources','')]}}
                return {'prefill':'/steer '+j['id']+' '}
            if self.tab == 8:
                viewed = self.mission_view
                if j.get('mission_state') in {'PLAN_READY','AWAITING_SIGNOFF'}:
                    require(viewed and viewed['version']==j['version'],'Reopen Mission to inspect the current exact plan or result')
                    signing = j['mission_state']=='AWAITING_SIGNOFF'
                    return {'form':{'operation':'mission-signoff' if signing else 'mission-authorize','target':j['id'],
                        'expected_version':j['version'],'values':{'exact_hash':viewed['result']['result_hash'] if signing else viewed['plan']['plan_hash']},'index':0,
                        'fields':[('note','Review the exact observed mission outcome' if signing else 'Authorize the displayed sequence, literal arguments and checks','')]}}
                if j.get('mission_state')=='AUTHORIZED':
                    return await self.app.dispatch('mission-run',j['id'])
                require(j.get('mission_state')!='RUNNING','Mission is running; use /pause or /cancel')
                if viewed and viewed['plan']['questions'] and not j.get('mission_run_id'):
                    return {'form':{'operation':'mission-answer','target':j['id'],'expected_version':j['version'],'values':{},'index':0,
                        'fields':[('text','Clarify the displayed question for the original mission objective','')]}}
                return {'form':{'operation':'mission','target':j['id'],'expected_version':j['version'],'values':{},'index':0,
                    'fields':[('text','What should this bounded tool mission achieve?','')]}}
            if self.tab == 7:
                if not self.app.connectors.connections():
                    return {'prefill':'/connect '}
                require(j.get('tool_delivery') not in {'UNKNOWN','CALLING'}, 'Inspect /tool-result and the actual service before resolving this uncertain operation')
                if j.get('tool_delivery') == 'PROPOSED':
                    viewed = self.tool_view
                    require(viewed and viewed['version']==j['version'], 'Reopen Tools to inspect the current request before approval')
                    return {'form':{'operation':'tool-run','target':j['id'],'expected_version':j['version'],
                        'values':{'exact_hash':viewed['plan']['plan_hash']},'index':0,
                        'fields':[('note','Approve the displayed service, operation and exact arguments','')]}}
                return {'form':{'operation':'tool-request','target':j['id'],'expected_version':j['version'],'values':{},'index':0,
                    'fields':[('text','What should a connected tool do? Monkey will propose it first.','')]}}
            if self.tab == 5:
                if self.project_action:
                    return await self.app.dispatch(("project", "explore", "plan", "execute")[self.project_action], target=j["id"])
                old = self.app.execution.record(j, "contract_id") if j.get("contract_id") else {}
                from .command_text import join
                return {"form": {"operation": "project", "target": j["id"], "expected_version": j["version"], "values": {}, "index": 0,
                    "fields": [("path", "Project directory", old.get("workspace", "")),
                               ("objective", "What result should this ticket deliver?", old.get("objective", "")),
                               ("writes", "Editable files · space separated, quote paths with spaces", join(old.get("write_paths", []))),
                               ("reads", "Reference files · optional", join(old.get("read_paths", []))),
                               ("verify", "Verifier · absolute executable and existing test file", join(old.get("verify_argv", []))),
                               ("expect", "Expected result · editable/path=expected text", "")]}}
            if self.tab == 6:
                viewed = self.plan_view
                require(viewed and viewed["version"] == j["version"], "Reopen Plan to review the current proposal")
                return {"form": {"operation": "authorize", "target": j["id"], "expected_version": j["version"],
                    "values": {"exact_hash": viewed["plan"]["plan_hash"]}, "index": 0,
                    "fields": [("note", "Review note for the exact displayed plan", "")]}}
            if self.tab == 4:
                j = self.app.db.job(self.job_id)
                if self.date_action:
                    return {"form": {"operation": "return", "target": j["id"], "expected_version": j["version"],
                        "fields": [("note", "Why does this ticket need new dates?", "")], "values": {}, "index": 0}}
                old = j.get("schedule", {})
                return {"form": {"operation": "schedule", "target": j["id"], "expected_version": j["version"],
                    "fields": [("start", "Start · YYYY-MM-DD HH:MM", old.get("start_local", "")),
                               ("finish", "Finish · YYYY-MM-DD HH:MM", old.get("finish_local", "")),
                               ("timezone", "Timezone", old.get("timezone", self.app.db.config()["timezone"])),
                               ("note", "Reason / note · optional", "")], "values": {}, "index": 0}}
            if self.tab == 3:
                if j.get('work_type'): return {'message':'Open the Agent tab to inspect and accept the exact coding or research result.'}
                viewed = self.approval_view
                j = self.app.db.job(self.job_id)
                if j.get('mission_plan_id') and j.get('mission_state')!='COMPLETED':
                    return {'message':'Inspect the Mission tab to sign off every approved step and its outcome checks.'}
                if j.get('tool_plan_id') and j.get('tool_delivery') not in {'SIGNED_OFF','MISSION_SIGNED_OFF'}:
                    require(j.get('tool_delivery') == 'RETURNED_UNVERIFIED', 'Finish a separate verification call, then inspect its result in Tools')
                    return {'form':{'operation':'tool-signoff','target':j['id'],'expected_version':j['version'],
                        'values':{'verification_id':j['tool_call_id']},'index':0,'fields':[
                            ('call_id','Action call ID · inspect recorded calls in Tools',''),
                            ('pointer','JSON pointer in the separate verification response',''),
                            ('expected','Exact expected JSON value · quote strings',''),
                            ('note','Your review of the exact action and verification result','')]}}
                if j.get("contract_id"):
                    require(viewed and viewed.get("execution") and viewed["version"] == j["version"], "Reopen Sign-off after inspecting Tools and the exact completed result")
                    return {"form": {"operation": "signoff", "target": j["id"], "expected_version": j["version"],
                        "values": {"exact_hash": viewed["execution"]["result_hash"]}, "index": 0,
                        "fields": [("note", "Confirm the actual local result you inspected", "")]}}
                require(viewed and viewed["draft"] and j["version"] == viewed["version"], "Ticket or draft changed. Reopen Sign-off to review the current candidate")
                require(j["draft_id"] == viewed["draft"]["id"], "The displayed draft is no longer current")
                receipt = await self.app.dispatch("approve", target=j["id"], note="Operator signed off the exact reviewed draft in the Monkey ticket browser")
                self.tab, self.offset, self.approval_view = 0, 0, None
                return receipt
            return None
        rows = self.rows()
        if not rows:
            return None
        self.index = min(self.index, len(rows) - 1)
        self.job_id = rows[self.index]["id"]
        await self.app.dispatch("focus", target=self.job_id)
        self.pane, self.tab, self.offset = "detail", 0, 0
        if self.app.db.job(self.job_id).get('work_type'): self.slide(10)
        return None

    def detail_lines(self, columns):
        db = self.app.db
        j = db.job(self.job_id)
        snapshot = db.record("snapshots", j["snapshot_id"])
        draft = db.record("drafts", j["draft_id"]) if j["draft_id"] else None
        review = db.record("reviews", j["review_id"]) if j["review_id"] else None
        signed = self.signed_off(j)
        if self.tab==10 or j.get('work_type') and self.tab in {0,1,3,5,6}:
            if j.get('work_type'):
                from .presentation import agent_lines
                lines=agent_lines(self.app.agent.view(j))
            else: lines=['General coding and research use /build and /research.']
        elif self.tab == 0:
            lines = [snapshot["ticket"]["title"],
                     "Work: " + j["work_state"] + (" · " + j["stage"] if j["stage"] else ""),
                     "Review: " + (review["result"]["verdict"] if review else "not completed"),
                     "Sign-off: " + (signed["operator"] + " · " + signed["approved_at"] if signed else "awaiting operator"),
                     "Delivery: " + delivery(j["delivery_state"]),
                     f"Candidates: {j['attempt_count']}/{j['limits']['attempts']} · model calls: {j['call_count']}/{j['limits']['calls']}",
                     "Execution: " + j.get("execution_state", "choose Project or Tools for execution"), j["reason"],
                     "Source: " + snapshot["ticket"]["source"] + " · " + snapshot["ticket"]["instance"],
                     "Snapshot: " + snapshot["id"], "", "Captured request:", snapshot["ticket"]["body"]]
        elif self.tab == 1:
            lines = [f"Draft {draft['number']} · {delivery(j['delivery_state'])}", draft["text"], "", "Payload hash: " + draft["payload_hash"]] if draft else ["No completed draft yet.", "Current work: " + (j["stage"] or j["work_state"])]
        elif self.tab == 2:
            lines = [f"{e['created_at'][11:19]} · {e['kind']} · {e['data'].get('message', '')}" for e in db.events(job_id=j["id"])]
        elif self.tab == 3:
            viewed = self.approval_view or {}
            d = viewed.get("draft")
            if j.get('mission_plan_id'):
                lines = ['Mission: '+j.get('mission_state','NONE'),'Use the Mission tab for the exact sequence, all agent results and final sign-off.',j['reason']]
            elif j.get('tool_plan_id') and j.get('tool_delivery') != 'SIGNED_OFF':
                lines = ['Tool outcome: '+j.get('tool_delivery','NONE'),'Inspect the action and a separate verification response in Tools.',
                    'Enter binds your review to the action call, verification call and exact expected value.',
                    'Latest call: '+j.get('tool_call_id','none'),'A response alone does not mark this ticket completed.']
            elif j.get('tool_delivery') == 'SIGNED_OFF' and not j.get('contract_id'):
                row = self.app.execution.record(j,'tool_signoff_id')
                lines = ['Tool outcome signed off by '+row['operator'],row['at'],row['note'],row['authority']]
            elif j.get("contract_id"):
                result = viewed.get("execution")
                lines = (["Inspect Tools and the observed outcome before pressing Enter.", "Exact local result: " + result["result_hash"],
                    "Edits and pinned verification receipts: " + str(len(result["effects"])),
                    "Your confirmation is bound to this task, plan, rules, dates and result.", "Kist Captain checks before sign-off; Jira publishing remains separate."] if result else ["Verified project execution is required before sign-off.", j["reason"]])
            else:
                lines = (["Enter signs off this exact draft locally.", "This does not publish a comment.", "", "Candidate " + str(d["number"]),
                      "Payload hash: " + d["payload_hash"], "", d["text"]] if d else ["A completed, passing draft is required before sign-off."])
        elif self.tab == 4:
            s = j.get("schedule", {})
            lines = ["Start: " + s.get("start_local", "not assigned"), "Finish: " + s.get("finish_local", "not assigned"),
                     s.get("timezone", self.app.db.config()["timezone"]) + " · " + s.get("status", "UNSCHEDULED") + " · revision " + str(s.get("revision", 0)),
                     ("› " if self.date_action == 0 else "  ") + "🍌 Set / change dates",
                     ("› " if self.date_action == 1 else "  ") + "↩ Return ticket for new dates",
                     "↑↓ choose · Enter edit · changes renew sign-off"]
        elif self.tab == 5:
            contract = self.app.execution.record(j, "contract_id") if j.get("contract_id") else {}
            lines = [contract.get("workspace", "No project attached"), "State: " + j.get("execution_state", "UNCONFIGURED")]
            lines += [("› " if self.project_action == i else "  ") + name for i, name in enumerate(("🍌 Set project, objective & checks", "Explore files", "Propose a plan", "Execute authorized plan"))]
        elif self.tab == 6:
            p = (self.plan_view or {}).get("plan")
            if not p:
                lines = ["Explore the project, then propose a plan from the Project tab."]
            else:
                import difflib
                proposal = p["proposal"]
                lines = [proposal["understanding"], "Plan: " + p["plan_hash"], "↑↓ inspect changes · Enter authorize with your review note"]
                lines += ["Needs clarification: " + question for question in proposal["questions"]]
                for edit in proposal["edits"]:
                    lines += ["File: " + edit["path"], edit["reason"]]
                    lines += list(difflib.unified_diff((p["before"][edit["path"]]["text"] or "").splitlines(), edit["content"].splitlines(), fromfile="before/" + edit["path"], tofile="proposed/" + edit["path"], lineterm=""))
                lines += ["Advisory restriction: " + r["path"] + " · " + r["reason"] for r in proposal["learned_rules"]]
        elif self.tab == 8:
            from .presentation import mission_lines
            lines = mission_lines(self.app.missions.view(j))
        elif self.tab == 9:
            from .presentation import content
            import json
            lines=content(self.audit_view or {'error':'Use /audit '+j['id']+' to check the signed trace'},json.dumps).splitlines()
        else:
            import json
            connections = self.app.connectors.connections()
            lines = ["Tools · " + j.get('tool_delivery', j.get("execution_state", "no request yet")), j["reason"]]
            plan = (self.tool_view or {}).get('plan')
            if plan:
                lines += [plan['server']+' / '+plan['tool'],json.dumps(plan.get('route',{}),ensure_ascii=False),
                    'Exact request: '+plan['plan_hash'],json.dumps(plan['arguments'],indent=2,ensure_ascii=False)]
                if j.get('tool_delivery') == 'PROPOSED':
                    lines += ['↑↓ inspect all arguments · Enter approve this exact request with your note']
            elif connections:
                lines += ['Connected: '+', '.join(connections), 'Enter to describe a tool request · /tools lists discovered operations']
            else:
                lines += ['No connected services yet · Enter to configure an MCP server or API']
            for record in db.records("artifacts", j["id"]):
                if record.get('kind') == 'mcp.raw_result':
                    self.app.execution.check_seal(record)
                    lines += ['Call: '+record['call_id'],'Returned result: '+record['result_hash'],json.dumps(record['result'],indent=2,ensure_ascii=False),
                        'A server response is retained; verify the actual service outcome separately.']
                if record.get("kind") == "project.effect":
                    result = record["evidence"]["result"]
                    lines += [record["at"] + " · " + result["kind"], "SML task: " + record["evidence"]["task_id"]]
                    if result["kind"] == "write":
                        lines += [result["file"]["path"] + " · " + result["file"]["sha256"], result["file"]["text"]]
                    else:
                        lines += ["Verifier exit: " + str(result["test"]["exit_code"]), result["test"]["output"], result["test"]["sandbox"]]
        wrapped = []
        for line in lines:
            for part in safe(line).expandtabs(4).splitlines() or [""]:
                wrapped.extend(textwrap.wrap(part, max(18, columns), replace_whitespace=False) or [""])
        return wrapped

    def render(self, input_text=""):
        w = width()
        if input_text:
            selected = self.app.db.job(self.app.focus)['key'] if self.app.focus else None
            heading = 'MESSAGE MONKEY' + (' · ' + selected if selected else '')
            return FormattedText([('class:eyebrow', '  ' + fit(heading, w - 2) + '\n'),
                                  ('class:prompt', '  › ')])
        if self.pane == "detail":
            j = self.app.db.job(self.job_id)
            parts = [('class:brand', '\n  ' + fit(j['key'], w - 2) + '\n'), ('', '  ')]
            visible = [self.tab]
            for index in sorted(range(len(TABS)), key=lambda i: (abs(i - self.tab), i)):
                if index not in visible and sum(len(TABS[i]) + 3 for i in [*visible, index]) <= w - 4:
                    visible.append(index)
            for index in sorted(visible):
                parts.append(('class:selected' if index == self.tab else 'class:muted', ' ' + TABS[index] + ' '))
                parts.append(('', ' '))
            parts.append(('class:rule', '\n  ' + '─' * (w - 2) + '\n'))
            lines = self.detail_lines(w - 4)
            self.offset = min(self.offset, max(0, len(lines) - 6))
            for line in lines[self.offset:self.offset + 6]:
                parts.append(("class:body", "  " + line + "\n"))
            parts.extend([('class:rule', '  ' + '─' * (w - 2) + '\n'),
                          ('class:muted', '  ' + fit(f"{self.offset + 1}–{min(len(lines), self.offset + 6)} / {len(lines)} · Tab {self.tab + 1}/{len(TABS)} · ↑↓ scroll · ←→ tabs · Esc back", w - 2) + '\n'),
                          ('class:prompt', '  › ')])
            return FormattedText(parts)
        groups = self.groups()
        left = [label + ("  " + str(len(groups[i - 1])) if 1<=i<=3 else "") for i, label in enumerate(MENU)]
        right = []
        if self.section == 4:
            right = [('accent','Connections')]
            services = self.app.services.list()['services']
            for i,item in enumerate(services):
                right.append(('selected' if self.pane=='services' and self.index==i else 'body',item['name']+' · '+item['state']))
            right += [('muted','Enter to connect · hidden credentials'),('code','/api start  ·  Your MCP/API endpoint')]
        elif self.section in {5,6}:
            right=[('accent','Build something' if self.section==5 else 'Research a topic'),
                ('strong','Turn an outcome into a plan.' if self.section==5 else 'Start with a question.'),
                ('body','Plans, tools and checks stay visible.' if self.section==5 else 'Keep the sources beside the answer.'),
                ('muted',''),('body','Enter to start · no ticket needed.'),
                ('code','/steer  ·  Change direction as it runs')]
        elif self.section == 0:
            right = [('accent', 'Start ticket'), ('strong', 'What needs doing?'),
                     ('body', 'Tasks, sprints, bugs and support.'), ('muted', ''),
                     ('body', 'Describe the outcome below.'), ('muted', 'Enter starts a ticket.'),
                     ('code', '/help  ·  /connect  ·  /jobs')]
        elif self.section == 3 and self.pane != "list":
            dates = {self.date_of(j) for j in groups[2]}
            compact = w < 32
            right = [("accent", "📅 " + self.day.strftime("%B %Y") + " · " + self.calendar_mode),
                     ("muted", "Mo Tu We Th Fr Sa Su" if compact else " Mo  Tu  We  Th  Fr  Sa  Su")]
            for week in calendar.monthcalendar(self.day.year, self.day.month):
                row = ""
                for number in week:
                    if not number:
                        row += '   ' if compact else '    '
                    elif number == self.day.day:
                        row += f'{number:2d}‹' if compact else f'[{number:2d}]'
                    else:
                        cell_day = dt.date(self.day.year, self.day.month, number)
                        marked = any(self.planned_on(j, cell_day) for j in self.app.db.jobs()) if self.calendar_mode == "planned" else cell_day in dates
                        row += (f'{number:2d}' if compact else f'{number:3d}') + ('•' if marked else ' ')
                right.append(("body", row))
            count = len(self.day_jobs())
            right.append(("muted", f"{self.day.strftime('%d %b')} · {count} tickets · Tab changes calendar"))
        else:
            rows = self.rows()
            title = "Current tickets" if self.section == 1 else "Finished work · inspect & sign off" if self.section == 2 else self.calendar_mode.title() + self.day.strftime(" · %d %b %Y")
            right = [("accent", title)]
            if not rows:
                right.extend([("muted", "└─ No tickets here yet."), ("muted", "🍌 Start ticket to add your first.")])
            else:
                self.index = min(self.index, len(rows) - 1)
                start = max(0, self.index - 3)
                for i, j in enumerate(rows[start:start + 5], start):
                    stage = j.get('agent_state') or (j["stage"] if j["work_state"] == "RUNNING" else j["work_state"])
                    signed = "signed off" if self.signed_off(j) else "needs sign-off" if j["review_id"] else stage.lower().replace("_", " ")
                    right.append(("selected" if self.pane == "list" and self.index == i else "body", f"{'└' if i == len(rows) - 1 else '├'}─ {j['key']} · {signed}"))
                right.append(("muted", f"{self.index + 1}/{len(rows)} · Enter to review"))
        parts = [("", "\n")]
        if w >= 76:
            left_width = 32
            right_width = w - left_width - 9

            def row(label, left_style, text, right_style):
                parts.extend([('class:rule', '  │ '), ('class:' + left_style, padded(label, left_width)),
                              ('class:rule', ' │ '), ('class:panel class:' + right_style, padded(text, right_width)),
                              ('class:rule', ' │\n')])

            parts.append(('class:rule', '  ╭' + '─' * (left_width + 2) + '┬' + '─' * (right_width + 2) + '╮\n'))
            row('WORKSPACE', 'eyebrow', right[0][1], 'brand')
            parts.append(('class:rule', '  ├' + '─' * (left_width + 2) + '┼' + '─' * (right_width + 2) + '┤\n'))
            body = right[1:]
            for i in range(max(len(left), len(body))):
                selected = self.section == i and i < len(left)
                label = ('› ' if selected else '  ') + left[i] if i < len(left) else ''
                style, text = body[i] if i < len(body) else ('body', '')
                row(label, 'selected' if selected else 'nav', text, style)
            parts.append(('class:rule', '  ╰' + '─' * (left_width + 2) + '┴' + '─' * (right_width + 2) + '╯\n'))
        else:
            parts.append(('class:eyebrow', '  WORKSPACE\n'))
            for i in range(max(0, self.section - 1), min(len(left), self.section + 2)):
                parts.append(('class:selected' if self.section == i else 'class:nav',
                              '  ' + padded(('› ' if self.section == i else '  ') + left[i], w - 2) + '\n'))
            parts.append(('class:rule', '  ' + '─' * (w - 2) + '\n'))
            for style, text in right:
                parts.append(("class:" + style, "  " + fit(text, w - 2) + "\n"))
        hint = "↑↓ choose · → / Enter open · Esc back · /help" if self.pane != "calendar" else "↑↓ week · ←→ day · PgUp/PgDn month · Enter tickets"
        parts.extend([("class:muted", "  " + fit(hint, w - 2) + "\n"), ("class:prompt", "\n  › ")])
        return FormattedText(parts)
