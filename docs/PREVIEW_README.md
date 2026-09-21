# Jira Monkey terminal preview

Version 0.1.0-preview.1 · 13 September 2026 · macOS, Python 3.9+

An installable terminal companion for the Jira Monkey research in FortuneWheel.
It reads a Jira Cloud ticket, asks a local Ollama model to triage it, sends an
eligible text task to your selected Claude, OpenAI or DeepSeek model, asks Ollama
to review the response, and retains the result in a retry queue, human-review
inbox or comment outbox. The default final policy is human review.

**This preview drafts proposed text resolutions. It does not edit a repository,
run tests, create a PR, or establish that a bug was fixed.** Work requiring those
actions goes to human review. Ollama's judgment is advisory and can be wrong.
There is no automatic Jira status transition or unattended queue daemon.

## Install and open

From an extracted source preview:

```sh
python3 install.py
~/bin/jira-monkey
```

The installer copies this reviewed source into `~/.local/share/jira-monkey`, adds
`~/bin/jira-monkey`, and creates `~/Applications/Jira Monkey.app`. Opening that
app opens the REPL in Terminal. No dependency, model, browser or background
service is downloaded or started. An existing installation is never overwritten.
If `~/bin` is already on PATH, the command is simply `jira-monkey`.

The terminal app is a local preview launcher, not a signed/notarized public macOS
release. Python must already be installed; the installer captures its path.

## Try it without credentials

```sh
jira-monkey demo
python3 -m unittest discover -s tests -v
```

The demonstration runs in a temporary directory and uses deterministic fixtures.
It exercises retry followed by human review, makes zero network requests and
removes its state on exit. It is not evidence of live model quality or Jira access.

## Configure real services

Start your existing local Ollama service separately and choose an installed text
model. Specify the exact cloud model available to your account; no provider or
model fallback is performed.

```sh
jira-monkey setup --provider openai --model YOUR_MODEL_ID \
  --ollama-model YOUR_INSTALLED_OLLAMA_MODEL \
  --site https://YOUR-SITE.atlassian.net --email you@example.com
jira-monkey doctor
jira-monkey fetch PROJECT-123
```

Choose `--provider claude`, `openai`, or `deepseek`. Make the corresponding
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `DEEPSEEK_API_KEY` available in the
process environment. Jira Cloud uses `JIRA_API_TOKEN` and the configured account
email. Use your normal secret manager to inject credentials. Never paste secrets
into ticket text, REPL commands or configuration JSON. API credentials are
separate from Claude Code or ChatGPT application subscriptions.

The selected cloud provider receives the ticket title and description, local
triage, and review feedback when you run a job. Ticket content and model outputs
are retained locally in `~/.jira-monkey` with private permissions. This is not
an entirely offline workflow. The Ollama endpoint is fixed to local loopback;
this does not attest the implementation of an operator-configured Ollama model.

`fetch` captures summary, description and Jira's `updated` revision through Jira
Cloud REST v3. It does not ingest attachments, comments, custom fields or linked
issues. Supported rich text is flattened; opaque rich content is rejected rather
than silently omitted. Jira Data Center and OAuth installations are not supported
in this preview. A captured snapshot may instead be imported:

```json
{"source":"jira","instance":"https://YOUR-SITE.atlassian.net","key":"PROJECT-123","revision":"the captured updated value","title":"Ticket title","body":"Ticket description"}
```

```sh
jira-monkey import /path/to/ticket.json
jira-monkey run
jira-monkey status
jira-monkey show JOB_ID
```

`run` processes one attempt of one queued ticket. Each job captures the provider,
model, review policy and attempt budget at ingestion. Later configuration changes
apply to new jobs. The same snapshot is deduplicated. Each attempt is saved before
model requests; retries retain previous review feedback and consume the original
budget. At most three attempts are permitted. Missing context, incomplete model
responses, failures, refusals and exhausted retries require human attention.

## Review and send back to Jira

```sh
jira-monkey draft JOB_ID
jira-monkey approve JOB_ID --note 'Reviewed the proposed response'
jira-monkey publish JOB_ID --confirm PROJECT-123
```

`approve` prepares a comment; `publish` authorizes and sends that exact comment.
To return a proposal for another attempt, use `retry JOB_ID --note 'feedback'`.
To end it without posting, use `reject JOB_ID --note 'reason'`.

For teams that want Ollama-passed proposals in the outbox immediately, configure
`--review-policy queue` before ingestion. This skips human approval of a passed
text proposal; publishing still requires the explicit `publish` command. No model
can choose destinations or issue keys. A comment records the captured revision,
chosen model, response, Ollama review, human decision and stable delivery marker.
Every comment says execution and acceptance are unverified. Issue status stays
unchanged; `published` means only that Jira acknowledged a comment.

Immediately before posting, the adapter re-reads the ticket and compares the
entire captured snapshot. A changed ticket requires ingestion and review of its
new revision. Jira's comment endpoint is not an atomic compare-and-set against
the issue revision: a concurrent edit can occur after that check. The comment
therefore retains the reviewed revision instead of claiming current acceptance.

The exact outgoing body is saved before POST. If delivery is interrupted or the
acknowledgement is lost, the job remains `delivery_unknown` (or `publishing` after
a hard crash). `reconcile JOB_ID` scans visible comments for one exact body. It
never automatically repeats a POST. Missing, edited, hidden, duplicated or
unavailable comments leave the outcome uncertain. This is duplicate prevention
within one local installation, not a distributed exactly-once guarantee.

An interrupted model stage requires `recover JOB_ID --note 'what happened'`.
Recovery moves it to human review and retains all attempts. It grants no success,
does not refund budgets, and does not affect FortuneWheel's worker recovery.

## Relationship to the native Jira Monkey app

The September 11 Swift workbench in FortuneWheel remains a separate **source
investigation** profile: captured ticket → Ollama requirement proposal → explicit
operator start → bounded Claude investigation reports. The terminal companion's
cloud adapters generate text, and do not replace its host action broker.

The September 13 audit found the installed FortuneWheel worker refusing status
with `the worker store differs from its immutable origin`. This preview does not
reset, migrate, forge or bypass that history. The original native investigation
worker still needs recovery before another investigation can start. Its historical
completed case was `executedUnverified`, not accepted; the ten-case local compiler
evaluation was 5/10 semantically acceptable, despite schema checks passing.

The complete intended code-fixing product still needs an authorized implementation
handoff, source/worktree allocation, target-specific tests and receipts, and a
live Jira/provider qualification. None is implied by a text review passing.

## Validation and boundaries

The included tests exercise the three provider wire formats using fake transports,
Ollama completion validation, retry/human/outbox routing, state persistence,
snapshot freshness, comment ambiguity and packaging. Fixture success does not
qualify account access, live cloud behavior, model quality or production Jira.
No real Jira ticket has been posted by this development session.

Network calls have a 120-second total deadline and a bounded response size;
redirects and automatic retries are disabled. Cloud output is capped at 4,096
tokens, each local response at 2,048. These limits do not establish a dollar
budget: provider prices and billed input/reasoning vary. No shell, source-edit,
desktop or browser tool is exposed to these model calls. State is protected from
other local users by permissions, not from a malicious process running as you.

## Protocol references

- [Ollama chat](https://docs.ollama.com/api/chat) and [structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Claude Messages](https://platform.claude.com/docs/en/api/messages/create)
- [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [Jira Cloud issues](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issues/) and [comments](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/)
