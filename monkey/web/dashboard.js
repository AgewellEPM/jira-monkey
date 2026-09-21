'use strict';

const $ = (id) => document.getElementById(id);
const state = {view: 'overview', offset: 0, query: '', day: '', calendarMode: 'completed',
  month: new Date(new Date().getFullYear(), new Date().getMonth(), 1), cursor: 0,
  snapshot: null, events: [], messages: new Map(), detail: null, detailTab: 'overview',
  detailRequest: 0, selected: null, connected: false, sending: false, uncertain: null,
  review: null, history: null};
const titles = {
  overview: ['A CLEARER PICTURE', 'Your work, in one place.', 'Give Monkey the work. Keep the whole picture.', 'On your desk'],
  current: ['KEEP THINGS MOVING', 'A little work in progress.', 'Every open request, and where it stands.', 'Current tickets'],
  review: ['YOUR TURN', 'A second look. Then onward.', 'Review drafts, provide missing information, and sign off on actual outcomes.', 'Needs your attention'],
  calendar: ['LOOK BACK. PLAN AHEAD.', 'Good work has a history.', 'Browse completed work and planned sprint dates.', 'Tickets on these dates'],
  activity: ['STRAIGHT FROM THE JOURNAL', 'Here’s what happened.', 'The recorded actions behind your work, with their timestamps.', 'Activity'],
  connections: ['BRING YOUR WORK TOGETHER', 'A place for all your tools.', 'Your service connections, managed by Monkey.', 'Connections']
};
let token = new URLSearchParams(location.hash.slice(1)).get('key');
try {
  if (token) sessionStorage.setItem('monkey.dashboard.key', token);
  else token = sessionStorage.getItem('monkey.dashboard.key');
} catch (_) { /* The current private link still works with storage disabled. */ }
if (location.hash) history.replaceState(null, '', location.pathname);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function human(value) { return String(value || '').toLowerCase().replaceAll('_', ' '); }
function dateLabel(value, options) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString(undefined, options);
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {$('toast').hidden = true;}, 6500);
}
async function api(path, body) {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 12000);
  try {
    const response = await fetch(path, {method: body === undefined ? 'GET' : 'POST',
      headers: {Authorization: 'Bearer ' + (token || ''), ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
      body: body === undefined ? undefined : JSON.stringify(body), signal: controller.signal,
      cache: 'no-store', credentials: 'omit', redirect: 'error'});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || 'Monkey could not complete this request.');
    return value;
  } finally { clearTimeout(deadline); }
}
function connection(online, message) {
  state.connected = online;
  $('connection-dot').classList.toggle('offline', !online);
  $('connection-label').textContent = online ? 'Monkey is connected' : 'Connection unavailable';
  $('connection-error').hidden = online;
  $('connection-error').textContent = message || '';
  $('chat-availability').textContent = online ? 'Same session as your terminal' : 'Waiting for your Monkey session';
  $('send-message').disabled = !online || state.sending;
}
function chooseView(view) {
  if (!titles[view]) return;
  state.view = view; state.offset = 0; state.selected = null; state.detail = null; state.history = null;
  document.querySelectorAll('.nav-item').forEach(node => {
    const selected = node.dataset.view === view;
    node.classList.toggle('active', selected);
    if (selected) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current');
  });
  const [eyebrow, title, description, list] = titles[view];
  $('page-eyebrow').textContent = eyebrow; $('page-title').textContent = title;
  $('page-description').textContent = description; $('list-title').textContent = list;
  $('breadcrumb').textContent = document.querySelector('.nav-item.active').childNodes[1].textContent;
  panels(); refresh();
}
function panels() {
  $('detail-panel').hidden = !state.selected;
  $('tickets-panel').hidden = Boolean(state.selected) || ['activity', 'connections'].includes(state.view);
  $('calendar-panel').hidden = Boolean(state.selected) || state.view !== 'calendar';
  $('connections-panel').hidden = Boolean(state.selected) || state.view !== 'connections';
  $('activity-panel').hidden = Boolean(state.selected) || !['overview', 'activity'].includes(state.view);
  $('activity-list').classList.toggle('expanded', state.view === 'activity');
}
function jobState(job) { return human(job.agent_state || job.execution_state || (job.work_state === 'RUNNING' ? job.stage : job.work_state)); }
function delivery(value) {
  return ({NONE: 'Local only', DRAFT: 'Not published', APPROVED: 'Approved · not published',
    STALE: 'Needs fresh review', POSTING: 'Posting', POST_UNKNOWN: 'Delivery uncertain',
    POSTED_UNVERIFIED: 'Posted · unverified', POSTED_VERIFIED: 'Posted · verified'})[value] || human(value);
}
function renderTickets(data) {
  const fragment = document.createDocumentFragment();
  data.jobs.forEach(job => {
    const row = element('button', 'ticket-row'); row.type = 'button';
    row.setAttribute('aria-label', `Inspect ${job.key}: ${job.title}`);
    row.append(element('span', 'ticket-icon', job.group === 'completed' ? '✓' : '☷'));
    const text = element('div'); text.append(element('strong', '', job.title));
    const meta = element('div', 'ticket-meta');
    meta.append(element('span', '', job.key), element('span', '', human(job.source)), element('span', '', jobState(job)));
    text.append(meta); row.append(text);
    const status = element('div', 'ticket-state');
    status.append(element('span', 'pill' + (job.group === 'completed' ? ' success' : job.needs_you ? ' warning' : ''),
      job.group === 'completed' ? 'Completed / closed' : job.needs_you ? 'Needs you' : jobState(job)));
    status.append(element('small', '', delivery(job.delivery_state))); row.append(status);
    row.addEventListener('click', () => openDetail(job.job_id)); fragment.append(row);
  });
  $('ticket-list').replaceChildren(fragment);
  $('empty-state').hidden = data.jobs.length > 0;
  $('empty-title').textContent = state.query ? 'No matching tickets.' : state.view === 'review' ? 'All clear for now.' : state.view === 'calendar' ? 'Nothing on these dates.' : 'A fresh start.';
  $('empty-copy').textContent = state.query ? 'Try a different title, source, or ticket key.' : state.view === 'review' ? 'Tickets that need your review or input will appear here.' : state.view === 'calendar' ? 'Signed-off work and closed requests are kept in the calendar.' : 'Describe a ticket, a sprint, or a request. Monkey will help you move it forward.';
  $('empty-start').hidden = Boolean(state.query) || ['review', 'calendar'].includes(state.view);
  $('list-count').textContent = data.total ? `${data.offset + 1}–${Math.min(data.offset + data.jobs.length, data.total)} of ${data.total} tickets` : 'No tickets in this view';
  $('previous-page').disabled = state.offset === 0;
  $('next-page').disabled = state.offset + 50 >= data.total;
}
function renderActivity() {
  const rows = state.history || state.events;
  const fragment = document.createDocumentFragment();
  [...rows].reverse().forEach(event => {
    const item = element('li', 'activity-item');
    item.append(element('span', 'event-mark', event.kind.includes('failed') || event.kind.includes('error') ? '!' : '↳'));
    const body = element('div');
    body.append(element('p', '', event.data.message || human(event.kind.replaceAll('.', ' '))));
    body.append(element('small', '', (event.job_id || 'Workspace') + ' · #' + event.seq));
    item.append(body, element('time', '', dateLabel(event.created_at, {hour: '2-digit', minute: '2-digit', second: '2-digit'})));
    fragment.append(item);
  });
  $('activity-list').replaceChildren(fragment);
  $('activity-empty').hidden = rows.length > 0;
  $('event-count').textContent = state.history ? 'HISTORY · CLICK FOR LIVE' : 'LIVE ACTIVITY';
  $('event-count').title = 'Return to latest events';
  $('activity-more').hidden = !rows.length || rows[0].seq <= 1;
}
function renderServices(data) {
  const fragment = document.createDocumentFragment();
  data.services.forEach(service => {
    const card = element('article', 'service-card');
    card.append(element('strong', '', service.name), element('span', 'pill quiet', human(service.state)),
      element('p', '', service.description)); fragment.append(card);
  });
  $('services-grid').replaceChildren(fragment);
}
function localDay(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}
function renderCalendar(data) {
  const year = state.month.getFullYear(), month = state.month.getMonth();
  $('calendar-title').textContent = state.month.toLocaleDateString(undefined, {month: 'long', year: 'numeric'});
  const fragment = document.createDocumentFragment();
  ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].forEach(name => fragment.append(element('span', '', name)));
  const offset = (new Date(year, month, 1).getDay() + 6) % 7;
  for (let i = 0; i < offset; i++) fragment.append(element('span', 'calendar-day blank'));
  for (let i = 1; i <= new Date(year, month + 1, 0).getDate(); i++) {
    const day = localDay(new Date(year, month, i));
    const button = element('button', 'calendar-day' + (day === state.day ? ' selected' : '') + (day === localDay(new Date()) ? ' today' : ''));
    button.append(element('span', '', i));
    const count = data.calendar_days[day] || 0;
    if (count) button.append(element('small', '', `${count} ${count === 1 ? 'ticket' : 'tickets'}`));
    button.setAttribute('aria-label', `${day}: ${count} ${state.calendarMode} tickets`);
    button.setAttribute('aria-pressed', String(state.day === day));
    button.addEventListener('click', () => {state.day = day; state.offset = 0; refresh();});
    fragment.append(button);
  }
  $('calendar-grid').replaceChildren(fragment);
  $('selected-day').textContent = state.day ? `${state.calendarMode === 'planned' ? 'Planned' : 'Completed'} on ${state.day}` : 'All dates';
}
function renderMessages(rows) {
  const conversation = $('conversation');
  const atBottom = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 70;
  let changed = false;
  for (const row of rows) {
    const previous = state.messages.get(row.id);
    if (previous && previous.status === row.status && previous.reply === row.reply) continue;
    changed = true; state.messages.set(row.id, row);
    if (previous && row.status === 'ERROR') toast(row.reply);
  }
  if (!changed) return;
  const fragment = document.createDocumentFragment();
  const all = [...state.messages.values()].slice(-80);
  for (const row of all) {
    const user = element('article', 'chat-message operator');
    const head = element('div', 'message-heading', 'You'); head.append(element('time', '', dateLabel(row.accepted_at, {hour: '2-digit', minute: '2-digit'})));
    user.append(head, element('div', 'message-body', row.text)); fragment.append(user);
    const reply = element('article', 'chat-message'); reply.append(element('div', 'message-heading', '🍌 Monkey'));
    reply.append(element('div', row.status === 'ACCEPTED' ? 'message-body pending' : 'message-body' + (['ERROR', 'INTERRUPTED'].includes(row.status) ? ' error' : ''),
      row.status === 'ACCEPTED' ? 'Request accepted. Waiting for a reply…' : row.reply || 'No reply was retained.'));
    if (row.publication_preview) {
      const review = element('button', 'button', 'Review exact publication');
      review.addEventListener('click', () => publicationDialog(row.publication_preview)); reply.append(review);
    }
    fragment.append(reply);
  }
  $('chat-welcome').hidden = all.length > 0;
  $('messages').replaceChildren(fragment);
  if (atBottom) conversation.scrollTop = conversation.scrollHeight;
}
function render(data) {
  state.snapshot = data;
  for (const key of ['current', 'review', 'completed', 'published']) $('count-' + key).textContent = data.counts[key];
  $('nav-current').textContent = data.counts.current; $('nav-review').textContent = data.counts.review;
  $('version').textContent = data.offline ? `DEMO FIXTURES · ${data.version}` : `v${data.version} · foreground`;
  $('worker-state').textContent = `Worker ${data.worker}`;
  const activity = Object.values(data.provider_activity || {})[0];
  const builder = data.build_environment;
  $('worker-detail').textContent = builder?.busy ? `Builder ${human(builder.phase)} · ${builder.elapsed_seconds}s` : activity ? (activity.state || 'Waiting for provider') : `${data.queue} queued · ${data.needs_you} need you`;
  $('updated-at').textContent = 'Updated ' + dateLabel(data.observed_at, {hour: '2-digit', minute: '2-digit', second: '2-digit'});
  $('route-label').textContent = `Chat: local · Draft default: ${data.models.draft_provider}`;
  $('chat-focus').textContent = state.selected ? (state.detail?.job.key || 'Selected ticket') : (data.jobs.find(job => job.job_id === data.focus)?.key || 'Workspace');
  if (data.offline) {
    $('connection-error').hidden = false;
    $('connection-error').textContent = 'Offline demonstration. These tickets and model responses are scripted fixtures; no real service action is being performed.';
  }
  const known = new Set(state.events.map(event => event.seq));
  state.events.push(...data.events.filter(event => !known.has(event.seq)));
  state.events = state.events.sort((a, b) => a.seq - b.seq).slice(-150);
  state.cursor = data.cursor;
  renderTickets(data); renderActivity(); renderServices(data); renderCalendar(data); renderMessages(data.messages);
}
let polling = false, nextPoll = null, refreshAgain = false;
async function refresh() {
  if (polling) {refreshAgain = true; return;}
  clearTimeout(nextPoll); polling = true;
  let delay = 750;
  try {
    const query = new URLSearchParams({view: state.view, offset: state.offset, q: state.query,
      day: state.day, calendar_mode: state.calendarMode, month: localDay(state.month).slice(0, 7), after: state.cursor});
    const data = await api('/api/state?' + query);
    connection(true); render(data);
    if (data.more_events) delay = 50;
  } catch (error) {
    connection(false, `${error.message} Your saved work remains in Monkey. Keep its terminal session open, then use /dashboard for the current private link.`);
    delay = 3000;
  } finally {
    polling = false;
    if (refreshAgain) {refreshAgain = false; delay = 30;}
    nextPoll = setTimeout(refresh, delay);
  }
}
async function submit(payload) {
  const row = await api('/api/requests', payload);
  renderMessages([row]); refresh(); return row;
}
function requestBase(kind, jobId, version) {
  return {id: crypto.randomUUID(), kind, job_id: jobId || null, expected_version: version ?? null};
}
async function sendMessage(text) {
  if (!text.trim() || state.sending) return;
  state.sending = true; $('send-message').disabled = true;
  const jobId = state.selected || state.snapshot?.focus || null;
  const payload = state.uncertain && state.uncertain.text === text && state.uncertain.job_id === jobId ? state.uncertain :
    {...requestBase('message', jobId), text};
  state.uncertain = payload;
  try {
    await submit(payload); state.uncertain = null;
    if ($('message').value === text) $('message').value = '';
    $('conversation').scrollTop = $('conversation').scrollHeight;
  } catch (error) {toast(error.message + ' Check activity before sending new work; resending the same message keeps its request ID.');}
  finally {state.sending = false; $('send-message').disabled = !state.connected;}
}
function startTicket() {
  state.selected = null; state.detail = null; panels();
  $('message').value = '/request '; $('message').focus();
  if (innerWidth <= 800) $('chat-form').scrollIntoView({block: 'center', behavior: 'auto'});
}
async function openDetail(jobId, tab = 'overview') {
  const request = ++state.detailRequest;
  state.selected = jobId; state.detailTab = tab; state.detail = null;
  $('detail-key').textContent = state.snapshot?.jobs.find(job => job.job_id === jobId)?.key || jobId;
  $('detail-title').textContent = 'Reading this ticket…'; $('detail-state').textContent = '';
  $('detail-evidence').textContent = 'Reading retained evidence…';
  $('detail-actions').inert = true; panels();
  document.querySelectorAll('#detail-tabs button').forEach(button => {
    button.classList.toggle('active', button.dataset.tab === tab);
    button.setAttribute('aria-selected', String(button.dataset.tab === tab));
  });
  try {
    const detail = await api(`/api/jobs/${encodeURIComponent(jobId)}?tab=${tab}`);
    if (request !== state.detailRequest) return;
    state.detail = detail;
    $('detail-key').textContent = detail.job.key; $('detail-title').textContent = detail.job.title;
    $('detail-state').textContent = `${jobState(detail.job)} · ${delivery(detail.job.delivery_state)} · revision ${detail.version}`;
    $('detail-evidence').textContent = detail.text;
    $('chat-focus').textContent = detail.job.key;
    $('detail-actions').inert = false;
    document.querySelectorAll('#detail-actions [data-action]').forEach(button => {
      const op = button.dataset.action, j = detail.job;
      button.hidden = op === 'approve' ? !detail.draft : op === 'publish_preview' ? j.source !== 'jira' || j.delivery_state !== 'APPROVED' :
        op === 'reconcile' ? !['POST_UNKNOWN', 'POSTED_UNVERIFIED'].includes(j.delivery_state) :
        op === 'resume' ? j.work_state !== 'PAUSED' && j.execution_state !== 'PAUSED' :
        op === 'pause' ? j.work_state !== 'RUNNING' : false;
    });
  } catch (error) {if (request === state.detailRequest) $('detail-evidence').textContent = error.message;}
}
async function action(operation, args = {}, detail = state.detail) {
  if (!detail) throw new Error('Inspect this ticket first.');
  const payload = {...requestBase('action', detail.job.job_id, detail.version), operation, arguments: args};
  const row = await submit(payload); toast('Request accepted. Monkey will report the recorded result.');
  return row;
}
function resetDialog() {
  $('review-note').value = ''; $('review-error').textContent = ''; $('review-check').checked = false;
  $('review-submit').disabled = false; $('date-fields').hidden = true; $('note-label').hidden = false;
  $('review-check-label').hidden = false; $('review-content').hidden = false;
  $('review-eyebrow').textContent = 'REVIEW BEFORE CONTINUING';
}
function reviewDialog(operation) {
  if (!state.detail) return;
  resetDialog();
  const detail = state.detail;
  state.review = {operation, detail};
  $('review-title').textContent = ({approve: 'Review this draft', retry: 'Give Monkey a new direction', cancel: 'Cancel this work?', schedule: 'Make time for this ticket'})[operation] || 'Review this action';
  $('review-description').textContent = detail.job.key + ' · ' + detail.job.title;
  $('review-content').textContent = operation === 'approve' ? `${detail.draft?.text || ''}\n\nCandidate: ${detail.draft?.number}\nPayload SHA-256: ${detail.draft?.payload_hash}\nTarget: ${detail.destination?.instance} / ${detail.job.key}\nVisibility: ${detail.draft?.payload?.visibility || 'Default audience'}\nApproval does not publish a comment.` :
    operation === 'cancel' ? 'Monkey will stop this job at its supported boundary. Already completed work and uncertain remote outcomes remain recorded.' : detail.job.reason;
  $('review-submit').textContent = ({approve: 'Approve exact draft', retry: 'Request revision', cancel: 'Cancel work', schedule: 'Save dates'})[operation] || 'Continue';
  $('review-check-label').hidden = operation !== 'approve';
  $('note-label').hidden = operation === 'cancel';
  if (operation === 'schedule') {
    $('date-fields').hidden = false;
    $('start-date').value = detail.job.schedule?.start_local?.slice(0, 16) || '';
    $('finish-date').value = detail.job.schedule?.finish_local?.slice(0, 16) || '';
    $('timezone').value = detail.job.schedule?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  }
  $('review-dialog').showModal();
}
function publicationDialog(preview) {
  resetDialog(); state.review = {operation: 'confirm', preview,
    detail: {job: {job_id: preview.job_id}, version: preview.job_version}};
  $('review-eyebrow').textContent = 'EXACT PUBLICATION'; $('review-title').textContent = 'Publish this comment?';
  $('review-description').textContent = `${preview.site} · ${preview.key} · candidate ${preview.candidate}`;
  $('review-content').textContent = `${preview.text}\n\nVisibility: ${preview.visibility || 'Default audience'}\nPayload SHA-256: ${preview.payload_hash}\nProperties: ${JSON.stringify(preview.properties)}\n\n${preview.message}`;
  $('note-label').hidden = true; $('review-submit').textContent = 'Publish exact comment'; $('review-dialog').showModal();
}

document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => chooseView(button.dataset.view)));
document.querySelectorAll('[data-message]').forEach(button => button.addEventListener('click', () => sendMessage(button.dataset.message)));
document.querySelectorAll('[data-prefill]').forEach(button => button.addEventListener('click', startTicket));
['new-ticket', 'empty-start'].forEach(id => $(id).addEventListener('click', startTicket));
$('chat-form').addEventListener('submit', event => {event.preventDefault(); sendMessage($('message').value);});
$('message').addEventListener('keydown', event => {if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {event.preventDefault(); sendMessage($('message').value);}});
$('search').addEventListener('input', () => {clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => {state.query = $('search').value; state.offset = 0; refresh();}, 180);});
$('previous-page').addEventListener('click', () => {state.offset = Math.max(0, state.offset - 50); refresh();});
$('next-page').addEventListener('click', () => {state.offset += 50; refresh();});
$('previous-month').addEventListener('click', () => {state.month.setMonth(state.month.getMonth() - 1); refresh();});
$('next-month').addEventListener('click', () => {state.month.setMonth(state.month.getMonth() + 1); refresh();});
$('calendar-mode').addEventListener('change', () => {state.calendarMode = $('calendar-mode').value; state.offset = 0; refresh();});
$('all-dates').addEventListener('click', () => {state.day = ''; state.offset = 0; refresh();});
$('close-detail').addEventListener('click', () => {state.selected = null; state.detail = null; state.detailRequest++; panels(); refresh();});
document.querySelectorAll('[data-tab]').forEach(button => button.addEventListener('click', () => openDetail(state.selected, button.dataset.tab)));
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', async () => {
  const op = button.dataset.action;
  if (['approve', 'retry', 'cancel', 'schedule'].includes(op)) return reviewDialog(op);
  try {await action(op, op === 'pause' ? {after: 'review'} : op === 'publish_preview' ? {draft_hash: state.detail?.draft?.payload_hash} : {});}
  catch (error) {toast(error.message);}
}));
$('close-dialog').addEventListener('click', () => $('review-dialog').close());
$('review-form').addEventListener('submit', async event => {
  event.preventDefault(); const review = state.review; if (!review) return;
  try {
    if (['approve', 'confirm'].includes(review.operation) && !$('review-check').checked) throw new Error('Review the exact candidate and target before continuing.');
    const note = $('review-note').value.trim();
    if (['approve', 'retry'].includes(review.operation) && !note) throw new Error('Add your review or direction.');
    let args = review.operation === 'approve' ? {note, draft_hash: review.detail.draft.payload_hash} : review.operation === 'retry' ? {note} : {};
    if (review.operation === 'schedule') args = {start: $('start-date').value, finish: $('finish-date').value, timezone: $('timezone').value, note};
    if (review.operation === 'confirm') {const fields = review.preview.confirmation.split(' '); args = {token: fields[1], key: fields[2]};}
    $('review-submit').disabled = true;
    await action(review.operation, args, review.detail); $('review-dialog').close();
  } catch (error) {$('review-error').textContent = error.message;}
  finally {$('review-submit').disabled = false;}
});
$('event-count').addEventListener('click', () => {state.history = null; renderActivity();});
$('activity-more').addEventListener('click', async () => {
  const before = (state.history || state.events)[0]?.seq;
  if (!before) return;
  try {const value = await api('/api/history?before=' + before); state.history = value.events; renderActivity();}
  catch (error) {toast(error.message);}
});
$('today').textContent = new Date().toLocaleDateString(undefined, {weekday: 'short', month: 'short', day: 'numeric'});
document.addEventListener('visibilitychange', () => {if (!document.hidden) refresh();});
refresh();
