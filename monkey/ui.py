from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import re
import shutil
import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import set_app
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, History as BaseHistory
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import print_formatted_text

from .cli import EXACT, line
from .common import Refused, safe
from . import presentation
from .browser import Browser


def is_secret(text):
    return bool(re.search(r"(?:_API_(?:KEY|TOKEN)\s*=|\bsk-[A-Za-z0-9]|Authorization\s*:|Bearer\s+)", text, re.I))


class History(FileHistory):
    def store_string(self, string):
        if not is_secret(string) and string.lstrip("/").split(" ", 1)[0] != "setup":
            super().store_string(safe(string))


class PrivateHistory(BaseHistory):
    """Suppress both memory and disk history for the entire credential prompt."""
    def __init__(self, history, private):
        super().__init__()
        self.delegate,self.private = history,private

    def load_history_strings(self):
        yield from self.delegate.load_history_strings()

    def append_string(self, string):
        if not self.private():
            super().append_string(string)

    def store_string(self, string):
        self.delegate.append_string(string)


class Commands(Completer):
    def __init__(self, app):
        self.app = app

    def get_completions(self, document, complete_event):
        current = document.get_word_before_cursor()
        choices = ["/" + c for c in sorted(EXACT)]
        for job in self.app.db.jobs():
            choices.extend([job["id"], job["key"]])
        for choice in choices:
            if choice.startswith(current):
                yield Completion(choice, start_position=-len(current))


def rail(app, width=None):
    width = width or shutil.get_terminal_size((80, 24)).columns
    environment=getattr(app,'build_environment',None)
    if environment and environment.busy:
        value=environment.status()
        return presentation.fit(f"  builder {value['phase'].lower()} · elapsed {value['elapsed_seconds']}s · /builder status",width)
    jobs = app.db.jobs()
    j = next((j for j in jobs if j["id"] == app.focus), None)
    execution_paused = bool(app.execution.active and app.db.job(app.execution.active).get('execution_state')=='PAUSED')
    worker = "paused" if execution_paused else "active" if app.worker.active or app.execution.active else "queue enabled" if app.worker.continuous else "paused" if j and j["work_state"] == "PAUSED" else "idle"
    if not j:
        route = "local Ollama" if app.db.config()["provider"] == "ollama" else "local chat"
        return presentation.fit(f"  {route}  ·  worker {worker}  ·  {sum(j['work_state']=='QUEUED' for j in jobs)} queued", width)
    state = j.get("execution_state") if j.get("contract_id") or j.get('tool_plan_id') or j.get('mission_plan_id') or app.execution.active==j['id'] else j["stage"] if j["work_state"] == "RUNNING" else j["work_state"]
    state = state or j['work_state']
    if j.get('work_type') and state!='PAUSED': state=j['agent_state']
    if j.get('work_type') and state!='PAUSED' and j.get('agent_retry_at'):
        import time
        if j['agent_retry_at']>time.time(): state='PROVIDER_WAIT'
    delivery = {"NONE": "not published", "DRAFT": "not published", "APPROVED": "approved, not published",
                "STALE": "stale, not published", "POST_UNKNOWN": "delivery unknown",
                "POSTED_UNVERIFIED": "posted, unverified", "POSTED_VERIFIED": "posted, verified",
                "POSTING": "sending approved comment"}.get(j["delivery_state"], j["delivery_state"].lower())
    first = f"{j['key']} | {state} | {delivery}"
    if j.get('tool_plan_id'):
        delivery = 'tool: '+j.get('tool_delivery','NONE').lower().replace('_',' ')
        first = f"{j['key']} | {state} | {delivery}"
    if width < 60:
        return safe(first)[:width]
    elapsed = ""
    stamp = j.get('execution_stage_started_at') if app.execution.active==j['id'] and state!='PAUSED' else j.get('stage_started_at') if j['work_state']=='RUNNING' else None
    if stamp:
        seconds = max(0,int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp)).total_seconds()))
        elapsed = f" | elapsed {seconds}s"
    first = f"{j['key']} | {state} | attempt {j['attempt_count']}/{j['limits']['attempts']}{elapsed}"
    if j.get('work_type'):
        first=f"{j['key']} | {state} | tools {j['agent_tool_count']}/{j['agent_limits']['tools']}{elapsed}"
        delivery='result accepted' if j['agent_state']=='COMPLETED' else 'review pending' if j['agent_state']=='AWAITING_REVIEW' else 'workspace '+j['agent_scope']['workspace'].rsplit('/',1)[-1]
    second = f"{delivery} | queue {sum(j['work_state']=='QUEUED' for j in jobs)} | worker {worker}"
    queue = getattr(app.models, "queue", None)
    if queue and queue.owner:
        local = next((value for value in app.http.activity.values() if value.get('stream_id')), {}) if app.http else {}
        if local.get('state') == 'receiving provisional output':
            second = f"local output {local['content_bytes']} bytes · provisional | {delivery}"
        else:
            second = "waiting for local provider | " + delivery
    if app.http and app.http.cooldown:
        import time
        delay = max([0, *(int(t - time.monotonic()) for t in app.http.cooldown.values())])
        if delay > 0:
            second += f" | cooldown {delay}s"
    return safe(first)[:width] + "\n" + safe(second)[:width]


def render(value):
    if isinstance(value,dict) and value.get('general_work'): return safe('\n'.join(presentation.agent_lines(value)))
    if isinstance(value, str):
        return safe(value)
    if isinstance(value, dict) and "recorded_evidence" in value:
        lines = [f"Recorded through event {value['evidence_snapshot_seq']}:",
                 *[f"{text}\n  Evidence: {ref}" for ref, text in value["recorded_evidence"].items()]]
        return safe("\n".join(lines))
    if isinstance(value, dict) and "recorded_reason" in value:
        lines = [value["recorded_reason"]]
        if value.get("review"):
            review = value["review"]
            lines.append("Review: " + review["result"]["verdict"])
            for finding in review["result"]["findings"]:
                lines.append(finding["claim_or_excerpt"])
                lines.append("Suggested revision: " + finding["suggested_revision"])
        lines.append(f"Evidence: {value['job']['evidence']} (through event {value['evidence_snapshot_seq']})")
        return safe("\n".join(lines))
    if isinstance(value, dict) and "jobs" in value and "worker" in value:
        rows = value["jobs"]
        lines = [f"Worker {value['worker']} | queued {value['queue']} | needs you {value['needs_you']}"]
        lines.extend(f"{r['key']} / {r['job_id']} | {r['work_state']}" +
                     (f" / {r['stage']}" if r["stage"] else "") +
                     f" | attempt {r['attempts']}/{r['attempt_limit']} | {r['delivery_state']}\n  {r['reason']}" for r in rows)
        return safe("\n".join(lines) if rows else lines[0] + "\nNo captured tickets yet. /fetch KEY or /import PATH")
    if isinstance(value, dict) and "candidate" in value and isinstance(value["candidate"], dict):
        c = value["candidate"]
        review = value["review"]["result"]["verdict"] if value.get("review") else "pending"
        return safe(f"{value['job_id']} | candidate {c['number']} | review {review} | {value['delivery_state']}\n"
                    f"Payload hash: {c['payload_hash']}\nVisibility: {c['payload'].get('visibility') or 'default audience'}\n\n{c['text']}\n\n"
                    f"Operation metadata: {json.dumps(c['payload']['properties'], ensure_ascii=True)}")
    if isinstance(value, dict) and "message" in value and "command" in value:
        return safe(value["message"] + (f"\nEvidence: /events {value['job_id']}" if value.get("job_id") else ""))
    return json.dumps(value, ensure_ascii=True, indent=2)


async def repl(app, json_mode=False, session=None, initial_form=None):
    tty = (session is not None or sys.stdin.isatty() and sys.stdout.isatty()) and not json_mode
    last_seq = app.db.sequence()
    alive = True
    last_chat = None
    messages = []
    multiline = False
    form = initial_form
    browser = Browser(app)

    def private_field():
        return bool(form and form['fields'][form['index']][0] in form.get('secret_fields',[]))

    def rich(value):
        # Background tasks are created outside prompt_async's app context.
        # Bind the actual prompt so styled output uses run_in_terminal safely.
        with set_app(session.app):
            print_formatted_text(value, style=presentation.STYLE, output=session.app.output, flush=True)

    def emit(value, kind="reply"):
        nonlocal form
        if tty and isinstance(value, dict) and "form" in value:
            form = value["form"]
            session.app.exit(result="\x00monkey:open-form")
            return
        if json_mode:
            print(json.dumps({"type": kind, "value": value}, ensure_ascii=True), flush=True)
        elif tty:
            rich(presentation.reply(value, render))
        else:
            print(render(value), flush=True)

    async def command(text):
        try:
            if is_secret(text):
                raise Refused("Credentials belong in the launch environment; input was not retained")
            value = await line(app, text)
            if value is not None:
                emit(value)
        except asyncio.CancelledError:
            emit("Conversation/request cancelled locally. Use /pause or /cancel for worker control.")
            raise
        except SystemExit:
            pass  # argparse help is a normal exact-command response.
        except Exception as exc:
            emit({"error": safe(str(exc)) if isinstance(exc, Refused) else type(exc).__name__,
                  "storage_stopped": app.db.failed})

    async def submit_form(submitted):
        try:
            emit(await app.dispatch(submitted['operation'],target=submitted['target'],
                expected_version=submitted['expected_version'],**submitted['values']))
        except Exception as exc:
            emit({'error':safe(str(exc)) if isinstance(exc,Refused) else type(exc).__name__})

    async def activity():
        nonlocal last_seq
        while alive:
            app.db.changed.clear()
            events = app.db.events(last_seq, limit=100)
            for event in events:
                last_seq = event["seq"]
                message = event["data"].get("message")
                if message and event["kind"] != "chat.call":
                    if json_mode:
                        emit(event, "event")
                    else:
                        jid = event["job_id"]
                        key = app.db.job(jid)["key"] if jid else "Monkey"
                        if tty:
                            rich(presentation.activity(event, key))
                        else:
                            emit(f"{event['created_at'][11:19]}  {key}" + (f" / {jid}" if jid else "") + f"  {message}")
            if session:
                session.app.invalidate()
            if len(events)==100:
                await asyncio.sleep(0)
                continue
            try:
                await asyncio.wait_for(app.db.changed.wait(),.05)
            except TimeoutError:
                pass

    c = app.db.config()
    if tty and session is None:
        session = PromptSession(history=History(str(app.db.root / "history")),
            completer=Commands(app), complete_while_typing=False,
            key_bindings=browser.bindings(lambda: session, emit, lambda: not multiline and form is None),
            reserve_space_for_menu=0, erase_when_done=True,
            bottom_toolbar=lambda: rail(app), refresh_interval=0.2,
            style=presentation.STYLE)
        session.app.output.set_title("🍌 Monkey · build, research & tickets")
    if tty:
        session.history = PrivateHistory(session.history,private_field)
        session.default_buffer.history = session.history
        session.key_bindings = browser.bindings(lambda: session, emit, lambda: not multiline and form is None)
    with patch_stdout(raw=False) if tty else contextlib.nullcontext():
        if tty:
            rich(presentation.banner(app))
        else:
            emit("🍌 Monkey · build, research & tickets" + (" — OFFLINE FIXTURES" if app.offline else ""))
            emit(f"Chat: local {c['chat_model']}\nDrafting: {c['provider']} / {c['model']}\n"
                 f"Jira: {'offline fixture' if app.offline else 'unconfigured' if not c['site'] else 'health unchecked'} | /help")
            emit(app.status())
        if not app.offline:
            diagnostic = asyncio.create_task(app.doctor(), name="monkey-diagnostics")
            app.background.add(diagnostic)
            diagnostic.add_done_callback(app.background.discard)
        feed = asyncio.create_task(activity(), name="monkey-event-feed")
        try:
            while True:
                try:
                    secret_entry = private_field()
                    selected = app.db.job(app.focus)["key"] if app.focus else None
                    prompt = "… > " if multiline else f"monkey[{selected}] > " if selected else "jira-monkey> "
                    if tty:
                        if form:
                            name, label, default = form["fields"][form["index"]]
                            prompt = FormattedText([("class:brand", "  🍌 " + label + "\n"),
                                ("class:muted", "  " + ("Enter keeps " + default + " · " if default else "") + "/abort cancels\n"),
                                ("class:accent", "  › ")])
                        else:
                            prompt = FormattedText([("class:accent", "  … ")]) if multiline else lambda: browser.render(session.default_buffer.text)
                    if tty or session is not None:
                        text = await session.prompt_async(prompt,is_password=secret_entry,
                            enable_history_search=not secret_entry,enable_open_in_editor=not secret_entry)
                    else:
                        if not json_mode:
                            print(prompt, end="", flush=True)
                        text = await asyncio.to_thread(sys.stdin.readline)
                        if text == "":
                            break
                        text = text.rstrip("\n")
                    if text == "\x00monkey:open-form":
                        continue
                    if tty and text.strip() and not is_secret(text) and not secret_entry:
                        rich(FormattedText([("class:accent", "  › "), ("class:body", safe(text))]))
                    if form:
                        if text.strip() in {"/abort", "/quit", "quit"}:
                            quit_requested = text.strip() != "/abort"
                            form = None
                            if quit_requested:
                                break
                            emit("Editing cancelled. Previously saved records are retained.")
                            continue
                        if is_secret(text) and not secret_entry:
                            emit({"error": "Keep credentials out of form fields"})
                            continue
                        name, label, default = form["fields"][form["index"]]
                        value = text.strip() or default
                        if not value and name not in {"note", "reads",*form.get('optional_fields',[])}:
                            emit({"error": "Please enter " + name})
                            continue
                        if not value and form["operation"] in {"return", "authorize", "signoff",'tool-run','tool-signoff','mission-authorize','mission-signoff'}:
                            emit({"error": "Please add your review or reason"})
                            continue
                        form["values"][name] = value
                        form["index"] += 1
                        if form["index"] == len(form["fields"]):
                            submitted, form = form, None
                            task = asyncio.create_task(submit_form(submitted),name='monkey-form')
                            app.background.add(task)
                            task.add_done_callback(app.background.discard)
                            await asyncio.sleep(0)
                        continue
                    if multiline:
                        if text == "/abort":
                            messages, multiline = [], False
                            emit("Multiline input discarded.")
                            continue
                        if text != ".":
                            messages.append(text)
                            continue
                        text, messages, multiline = "\n".join(messages), [], False
                    if text.strip() in {"quit", "/quit", "exit", "/exit"}:
                        break
                    if text.strip() in {"multiline", "/multiline"}:
                        multiline = True
                        emit("Multiline input: a single dot line sends; /abort discards.")
                        continue
                    if not text.strip():
                        continue
                    task = asyncio.create_task(command(text), name="monkey-command")
                    app.background.add(task)
                    task.add_done_callback(app.background.discard)
                    if text.split()[0].lstrip("/") not in EXACT:
                        last_chat = task
                    # Let deterministic commands commit before the next submitted
                    # line while network awaits always leave the prompt usable.
                    await asyncio.sleep(0)
                except KeyboardInterrupt:
                    if form:
                        form = None
                        emit("Editing cancelled. Previously saved records are retained.")
                    elif last_chat and not last_chat.done():
                        last_chat.cancel()
                    else:
                        emit("Input cleared. /pause and /cancel control work.")
                except EOFError:
                    break
        finally:
            alive = False
            feed.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await feed
            emit("See you next time. Your work is saved; foreground execution is stopping.")
    return 0
