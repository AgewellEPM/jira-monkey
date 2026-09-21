"""A compact terminal voice. Styling never interprets ticket text as terminal code."""
from __future__ import annotations

import re
import shutil
import textwrap

from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from .common import safe

STYLE = Style.from_dict({
    "brand": "#f6d365 bold", "accent": "#91d8c1 bold", "body": "#d8e0e8",
    "strong": "#f3f5f7 bold", "muted": "#94a3b3", "rule": "#384655",
    "eyebrow": "#94a3b3 bold", "nav": "#b7c4d1",
    "panel": "bg:#18212b #d8e0e8",
    "success": "#a0d6a7", "warning": "#f0cb7b", "error": "#f29b9b",
    "code": "#a7c9ed", "prompt": "#f6d365 bold",
    "selected": "bg:#3b3523 #ffe397 bold",
    "bottom-toolbar": "bg:#18212b #aab8c6",
    "completion-menu.completion": "bg:#202c38 #d8e0e8",
    "completion-menu.completion.current": "bg:#3b3523 #ffe397 bold",
})


def width():
    return max(24, min(110, shutil.get_terminal_size((80, 24)).columns - 2))


def fit(text, columns):
    text = safe(text).replace("\n", " ").replace("\t", " ")
    while text and get_cwidth(text) > columns:
        text = text[:-1]
    return text


def padded(text, columns):
    clipped = fit(text, columns)
    return clipped + ' ' * max(0, columns - get_cwidth(clipped))


def inline(text, default="body"):
    chunks = []
    for part in re.split(r"(\*\*[^*\n]+\*\*|`[^`\n]+`)", safe(text)):
        if part.startswith("**") and part.endswith("**"):
            chunks.append(("class:strong", part[2:-2]))
        elif part.startswith("`") and part.endswith("`"):
            chunks.append(("class:code", part[1:-1]))
        else:
            chunks.append(("class:" + default, part))
    return chunks


def paragraph(text, columns, indent="  ", style="body"):
    result = []
    for original in safe(text).expandtabs(4).splitlines() or [""]:
        if original.startswith("```"):
            continue
        heading = re.match(r"^#{1,6} (.+)", original)
        if heading:
            original = heading[1]
        lines = textwrap.wrap(original, max(10, columns - len(indent)),
                              replace_whitespace=False, drop_whitespace=True) or [""]
        for line in lines:
            result.append(("", indent))
            result.extend(inline(line, "strong" if heading else style))
            result.append(("", "\n"))
    return result


def banner(app):
    w = width()
    c=app.db.config()
    local_draft = all(m['provider']=='ollama' for m in c.get('model_routes',{}).get('draft',[{'provider':c['provider']}]))
    label = '  🍌 Monkey'
    descriptor = 'YOUR WORKSPACE' if w >= 56 else ''
    parts = [("", "\n"), ("class:brand", label),
             ("class:eyebrow", ' ' * max(2, w - get_cwidth(label) - len(descriptor)) + descriptor + '\n'),
             ("class:body", '  ' + fit('Tasks, tickets and the work behind them.', w - 2) + '\n'),
             ("class:rule", "  " + "─" * (w - 2) + "\n"),
             ("class:muted", '  CHAT  '), ("class:accent", 'local'),
             ("class:rule", '\n  ' if w < 40 else '   /   '), ("class:muted", 'DRAFT  '),
             ("class:accent" if local_draft else "class:warning", 'local' if local_draft else 'cloud')]
    if w >= 62:
        parts.extend([('class:rule', '   /   '), ('class:muted', 'SERVICES  '),
                      ('class:body', str(len(app.connectors.connections())) + ' configured')])
    parts.append(('', '\n'))
    if app.offline:
        parts.extend(paragraph("Offline demonstration · scripted fixtures", w, style="warning"))
    return FormattedText(parts)


def delivery(state):
    return {"NONE": "local only", "DRAFT": "not published", "APPROVED": "approved · not published",
            "STALE": "needs a fresh review", "POST_UNKNOWN": "delivery uncertain",
            "POSTING": "sending approved comment", "POSTED_UNVERIFIED": "posted · verification pending",
            "POSTED_VERIFIED": "posted & verified"}.get(state, state.lower().replace("_", " "))


def job_lines(job):
    state = ('PAUSED' if job.get('execution_state')=='PAUSED' else job.get('agent_state')) or job.get("execution_state") or ((job.get("stage") or "working") if job["work_state"] == "RUNNING" else job["work_state"])
    delivered = 'tool '+job['tool_delivery'].lower().replace('_',' ') if job.get('tool_delivery') else delivery(job['delivery_state'])
    return [f"**{job['key']}** · {state.lower().replace('_', ' ')} · {delivered}",
            job.get("title", ""), job.get("reason", "")]


def mission_lines(value):
    import json
    records = value['mission_records']
    plan = next((r for r in reversed(records) if r['kind']=='mission.plan'),None)
    lines = ['Mission · '+value['mission_state'],value['reason'],f"Agents {value['agent_count']}/{value['agent_limit']} · model calls {value['provider_calls']}/{value['provider_limit']}"]
    if plan:
        lines += [plan['understanding'],'Exact plan: '+plan['plan_hash'],'Enter authorizes the inspected plan, starts approved work, or signs its recorded outcome.']
        lines += ['Needs clarification: '+q for q in plan['questions']]
        for step in plan['steps']:
            request = step['request']
            lines += [str(step['index'])+'. '+step['agent']+' · '+step['purpose'],request['server']+' / '+request['tool'],
                json.dumps(request['arguments'],indent=2,ensure_ascii=False),'Request hash: '+request['plan_hash']]
        lines += ['Expected outcome: '+json.dumps(check,ensure_ascii=False) for check in plan['checks']]
    for row in records:
        if row['kind']=='mission.step':
            lines += ['Observed step '+str(row['step_index'])+' · '+row['agent_id'],'Call: '+row['result']['call_id']]
        elif row['kind']=='mission.result':
            lines += ['Exact result: '+row['result_hash'],'Recorded checks: '+json.dumps(row['checks'],ensure_ascii=False)]
        elif row['kind']=='mission.assessment':
            lines += ['Agent assessment · '+row['assessment']['decision'],row['assessment']['reason']]
    return lines


def agent_lines(value):
    lines=[value['kind'].title()+' · '+value['state'].lower().replace('_',' '),value['reason'],
        'Workspace: '+value['workspace'],f"Models {value['model_calls']}/{value['limits']['calls']} · tools {value['tool_calls']}/{value['limits']['tools']}"]
    plan=next((r for r in reversed(value['records']) if r['kind']=='agent.plan'),None)
    if plan:
        lines+=['','Plan',*[str(i+1)+'. '+step for i,step in enumerate(plan['steps'])],
            'Checks: '+'; '.join(plan['checks'])]
    for row in value['records']:
        if row['kind']=='agent.observation':
            result=row['result']
            lines += [row['at'][11:19]+' · '+row['action']+' · '+row['id']]
            if row['action']=='run_command': lines += ['Exit '+str(result['exit_code']),result['output']]
            elif result.get('path'): lines += [result['path']+' · '+str(result.get('after_sha256',result.get('sha256','')))]
            elif result.get('error'): lines += [result['error']]
        if row['kind']=='agent.reflection': lines+=['Rethinking: '+'; '.join(row['findings']),'Next: '+'; '.join(row['adjustments'])]
    result=value.get('result')
    if result:
        lines+=['',result['summary'],*['Checked: '+c for c in result['checks']]]
        lines += [f"[{s['title']}]({s['url']}) · captured {s['captured_at']} · {s['id']}" for s in result['sources']]
        lines += ['Exact result: '+result['result_hash'],'/agent-accept '+value['job_id']+' --hash '+result['result_hash']+' --note "Reviewed result"']
    lines+=['/steer '+value['job_id']+' TEXT · /agent-continue '+value['job_id'],'Signed activity: /trace '+value['job_id']]
    return lines


def content(value, fallback):
    if isinstance(value, dict) and 'command' in value and 'message' in value:
        return value['message'] + ('\nEvidence: /events ' + value['job_id'] if value.get('job_id') else '')
    if isinstance(value, dict) and 'dashboard_url' in value:
        return value['message'] + ('\n\n' + value['dashboard_url'] if value.get('dashboard_url') else '')
    if isinstance(value,dict) and value.get('general_work'): return '\n'.join(agent_lines(value))
    if isinstance(value,dict) and 'procedures' in value:
        lines=['Learned procedures · from accepted work']
        for row in value['procedures']:
            lines+=['Version '+str(row['version'])+' · '+str(row['accepted_examples'])+' accepted examples',*row['procedure'],'Source: '+', '.join(row['source_jobs'])]
        return '\n'.join(lines or ['No accepted procedures yet.'])
    if isinstance(value,dict) and 'model_routes' in value:
        lines=['**Model roles** · '+value['routing_policy']]
        for role,chain in value['model_routes'].items():
            lines.append(role+' · '+' → '.join(m['provider']+'/'+m['model'] for m in chain))
        if not value['model_routes']: lines.append('Using the configured local chat and worker defaults.')
        lines += [value['message'],value['measurement']]
        for row in value['metrics']:
            lines.append(row['role']+' / '+row['model']+' · '+str(row['verified_jobs'])+' verified jobs · '+str(row['transport_failures'])+' transport failures')
        return '\n'.join(lines)
    if isinstance(value,dict) and 'trace' in value:
        lines=['**Signed trace** · '+(value.get('job_id') or 'application'),
            'SHA-256 chain + Ed25519 signatures · '+str(value['entries'])+' entries checked',
            'Signer: '+value['signer_fingerprint'],'Head: '+value['head']]
        for row in value['trace'][-30:]:
            detail=row['data']
            target=detail.get('path',detail.get('url',detail.get('operation',detail.get('kind',''))))
            lines.append(str(row['seq'])+' · '+row['kind']+(' · '+str(target) if target else ''))
        for run in value['runs'][-3:]:
            lines.append('Signed run: '+run['run_id']+' · '+run['kind'])
        lines += ['Full private bundle: /audit-export'+(' '+value['job_id'] if value.get('job_id') else ''),
            'External service internals are unobserved; requests and returned evidence are recorded.']
        return '\n'.join(lines)
    if isinstance(value,dict) and 'services' in value:
        return '\n'.join([value.get('message',''),*[row['name']+' · '+row['state']+'\n  '+row['description'] for row in value['services']]])
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return "Nothing here yet. Describe a ticket or request to get started."
        if all(isinstance(row, dict) and "work_state" in row for row in value):
            return "\n\n".join("\n".join(job_lines(row)) for row in value)
        if all(isinstance(row, dict) and "seq" in row and "kind" in row for row in value):
            return "\n".join(f"{r['created_at'][11:19]}  {r['kind']} · {r['data'].get('message', '')}" for r in value)
        if all(isinstance(row, dict) and 'catalog_hash' in row for row in value):
            return '\n\n'.join(content(row,fallback) for row in value)
    if not isinstance(value, dict):
        return fallback(value)
    if "error" in value:
        return str(value["error"])
    if 'mission_records' in value:
        return '\n'.join(mission_lines(value))
    if 'catalog_hash' in value and 'tools' in value:
        return '**'+value['name']+'** · '+value['transport']+' · '+str(len(value['tools']))+' tools\n'+ '\n'.join(t['name']+' · '+t.get('description','')[:180] for t in value['tools'])+'\nSchemas: /tools --server '+value['name']+' (use --json for complete records)'
    if 'tool_plan' in value:
        import json
        p = value['tool_plan']
        return ('**Proposed tool request** · '+p['server']+' / '+p['tool']+'\n'+json.dumps(p.get('route',{}),indent=2)+'\n'+
            json.dumps(p['arguments'],indent=2,ensure_ascii=False)+'\nExact request: '+p['plan_hash']+'\nOpen the Tools tab and press Enter to review and call. No action has run.')
    if 'delivery' in value and 'records' in value and 'job_id' in value:
        import json
        lines = ['**Tool delivery** · '+value['delivery'].lower().replace('_',' ')]
        for r in value['records']:
            if r['kind']=='mcp.raw_result':
                lines += ['Result '+r['result_hash'],json.dumps(r['result'],indent=2,ensure_ascii=False)]
            elif r['kind']=='mcp.call':
                lines += ['Recorded call: '+r['id'],'Runtime: '+r.get('evidence',{}).get('runtime_path',r.get('runtime_path','retained in /tool-result JSON'))]
        return '\n'.join(lines)
    if value.get('runtime') == 'Kist DGM':
        return value['report']
    if "project_records" in value:
        records = value["project_records"]
        lines = ["Project work · " + value["execution_state"].lower().replace("_", " "), value["reason"],
                 f"Tools {value['tool_calls']}/{value['tool_limit']} · runs {value['runs']}/{value['run_limit']}"]
        plan = next((r for r in reversed(records) if r["kind"] == "project.plan"), None)
        if plan:
            p = plan["proposal"]
            lines += ["", "Plan: " + p["understanding"], "Exact plan hash: " + plan["plan_hash"]]
            lines += ["Needs clarification: " + q for q in p["questions"]]
            lines += ["Proposed edit: " + e["path"] + " · " + e["reason"] for e in p["edits"]]
        result = next((r for r in reversed(records) if r["kind"] == "project.execution"), None)
        if result:
            lines += ["", "Exact result hash: " + result["result_hash"], "Inspect the Tools and Sign-off tabs for recorded results."]
        return "\n".join(lines)
    if "schedule" in value and ("history" in value or "command" in value):
        s = value["schedule"]
        if not s:
            return "No dates assigned. Open Dates in the ticket browser to set a start and finish."
        return (value.get("message", "Planned sprint dates") + f"\nStart: {s.get('start_local', 'not assigned')}\n"
                f"Finish: {s.get('finish_local', 'not assigned')}\nTimezone: {s.get('timezone', 'not assigned')}\n"
                f"{s['status']} · schedule revision {s['revision']}\n{s.get('note', '')}")
    if "recorded_reason" in value or "recorded_evidence" in value:
        return fallback(value)
    if "jobs" in value and "worker" in value:
        title = f"Worker {value['worker']} · {value['queue']} queued · {value['needs_you']} need you"
        return title + ("\n\n" + "\n\n".join("\n".join(job_lines(j)) for j in value["jobs"]) if value["jobs"] else "\nReady for your first ticket or request.")
    if "candidate" in value and isinstance(value["candidate"], dict):
        c = value["candidate"]
        verdict = value["review"]["result"]["verdict"] if value.get("review") else "pending"
        return (f"**Draft {c['number']}** · review {verdict.lower()} · {delivery(value['delivery_state'])}\n\n" + c["text"] +
                f"\n\nEvidence: /show {value['job_id']}\nPayload hash: {c['payload_hash']}")
    if "confirmation" in value:
        return (f"**Ready to publish?**\n{value['site']} · {value['key']} · candidate {value['candidate']}\n"
                f"Visibility: {value['visibility'] or 'default audience'}\n\n{value['text']}\n\n"
                f"Payload hash: {value['payload_hash']}\nProperties: {value['properties']}\n\n"
                f"`{value['confirmation']}`\n{value['message']}")
    if "chat" in value and "drafting" in value:
        return (f"**Chat** · local Ollama\n{value['chat']['model']}\n\n**Drafting** · {value['drafting']['provider']}\n"
                f"{value['drafting']['model']}\n\n**Triage & review** · local Ollama\n{value['triage_review']['model']}")
    if "draft.generate" in value:
        return ("**Tickets, sprints & requests**\nDraft stories, tasks, bug reports, sprint plans, acceptance criteria and support replies.\n\n"
                "**Bring your work**\nDescribe it here, import ticket text from another system, or fetch a connected Jira ticket.\n\n"
                "**Stay in control**\nInspect, pause, redirect and review. Jira comments require your exact approval and confirmation.\n\n"
                "**Build and research**\n/build starts a coding workspace. /research captures public sources. /agent shows plans, tools, checks and reflection.\n\n"
                "**Connected tools**\n/connect sets up service APIs and MCP tools. External effects require the exact reviewed request.\n\n"
                +value['general_agent']['execution_limit'])
    if "calls" in value and "scopes" in value:
        return f"**{value['calls']} recorded model calls**\n" + "\n".join(f"{k} · {v}" for k, v in sorted(value["scopes"].items())) + "\nCost: unknown"
    if "work_state" in value and "job_id" in value:
        return "\n".join(job_lines(value)) + f"\n\nSelected · {value['job_id']}"
    if "job" in value and "source" in value:
        j, source = value["job"], value["source"]
        ticket = source["ticket"]
        lines = [f"**{j['key']}** · {ticket['source']} · {j['work_state'].lower().replace('_', ' ')}",
                 ticket["title"], "", "**Captured request**", ticket["body"], "",
                 f"Source: {ticket['instance']} · revision {ticket['revision']}",
                 f"Snapshot: {source['id']}\nHash: {source['content_hash']}",
                 f"Attempts: {j['attempt_count']}/{j['limits']['attempts']} · calls: {j['call_count']}/{j['limits']['calls']}",
                 f"Delivery: {delivery(j['delivery_state'])}",
                 f"Saved: {len(value['drafts'])} drafts · {len(value['reviews'])} reviews · {len(value['approvals'])} approvals",
                 "", f"`/draft {j['id']}`  read the exact candidate", f"`/events {j['id']}`  inspect the timeline"]
        return "\n".join(lines)
    if "job" in value and isinstance(value["job"], dict) and "work_state" in value["job"]:
        return "\n".join(job_lines(value["job"]))
    if "message" in value:
        text = value["message"]
        if value.get("recorded_status"):
            text += "\n\n" + content(value["recorded_status"], fallback)
        return text
    if "data" in value and isinstance(value["data"], dict) and "message" in value["data"]:
        return value["data"]["message"]
    return fallback(value)


def reply(value, fallback):
    error = isinstance(value, dict) and "error" in value
    parts = [("", "\n"), ("class:brand", "  🍌 Monkey"),
             ("class:error" if error else "class:muted", "   " + ("Needs attention" if error else "") + "\n")]
    parts.extend(paragraph(content(value, fallback), width(), indent='     ', style="error" if error else "body"))
    return FormattedText(parts)


def activity(event, key):
    kind = event["kind"]
    style, marker = ("error", "!") if "failed" in kind else ("warning", "○") if "pause" in kind or "unknown" in kind else ("success", "✓") if "completed" in kind or "captured" in kind or "boundary" in kind else ("accent", "›")
    parts = [("class:" + style, "  " + marker + " "), ("class:strong", fit(key, 24)),
             ("class:muted", "  " + event["created_at"][11:19] + "  ")]
    parts.extend(inline(safe(event["data"]["message"])))
    return FormattedText(parts)
