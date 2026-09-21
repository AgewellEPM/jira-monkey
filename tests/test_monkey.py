import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jira_monkey as m
import install

CONFIG = {"provider": "openai", "model": "test-model", "ollama_model": "local-model",
          "site": "https://test.atlassian.net", "email": "test@example.com",
          "review_policy": "human", "max_attempts": 3}
TICKET = {"source": "jira", "instance": CONFIG["site"], "key": "TEST-7", "revision": "revision-one",
          "title": "Explain the keyboard shortcut", "body": "Draft text for the release notes."}


class FakeModels(m.DemoModels):
    def __init__(self, decision="pass", human=False, fail_at=None):
        self.decision, self.human, self.fail_at = decision, human, fail_at
        self.calls = []
    def triage(self, c, ticket):
        self.calls.append("triage")
        if self.fail_at == "triage":
            raise m.Refused("triage failed")
        result = super().triage(c, ticket)
        result["needs_human"] = self.human
        return result
    def work(self, c, data):
        self.calls.append(("work", copy.deepcopy(data)))
        if self.fail_at == "work":
            raise m.Refused("work failed")
        return super().work(c, data)
    def review(self, c, ticket, attempt):
        self.calls.append("review")
        if self.fail_at == "review":
            raise KeyboardInterrupt()
        return {"decision": self.decision, "reason": "Fixture evaluation", "feedback": ["Clarify shortcut"]}


class FakeHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeJira:
    c = CONFIG
    def __init__(self, snapshot=None, fail=False):
        self.snapshot = copy.deepcopy(snapshot or TICKET)
        self.posts = []
        self.fail = fail
    def fetch(self, key):
        return self.snapshot
    def comment(self, key, text):
        self.posts.append((key, text))
        if self.fail:
            raise m.Refused("Acknowledgement lost")
        return {"id": "10001"}
    def comments(self, key):
        return [{"id": "10001", "body": {"type": "doc", "content": [{"type": "paragraph",
                "content": [{"type": "text", "text": text}]}]}} for _, text in self.posts]


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.store = m.Store(self.temp.name)
        self.store.write("config.json", CONFIG)
        self.job = self.store.add(TICKET)
    def run_job(self, model=None):
        return m.run_job(self.store, self.job, model or FakeModels())
    def command(self, *args):
        return m.dispatch(m.parser().parse_args(["--state", self.temp.name, *args]))
    def ready(self):
        self.run_job()
        self.command("approve", self.job["id"], "--note", "Reviewed text")
        self.job = self.store.job(self.job["id"])
    def test_default_pass_requires_human_and_keeps_original(self):
        model = FakeModels()
        self.run_job(model)
        self.assertEqual(self.job["state"], "human_review")
        self.assertFalse(self.job["accepted"])
        self.assertEqual(model.calls[1][1]["ticket"], TICKET)
        self.assertEqual(model.calls[-1], "review")
    def test_queue_policy_pass_goes_to_outbox_without_post(self):
        self.job["config"]["review_policy"] = "queue"
        self.run_job()
        self.assertEqual(self.job["state"], "ready_to_publish")
        self.assertNotIn("delivery", self.job)
    def test_retry_carries_feedback_and_eventually_exhausts(self):
        model = FakeModels(decision="retry")
        self.run_job(model)
        self.assertEqual(self.job["state"], "queued")
        self.run_job(model)
        self.assertEqual(model.calls[4][1]["previous_review"]["feedback"], ["Clarify shortcut"])
        self.run_job(model)
        self.assertEqual(self.job["state"], "human_review")
        with self.assertRaises(m.Refused):
            self.command("retry", self.job["id"], "--note", "Again")
        self.assertEqual(len(self.store.job(self.job["id"])["attempts"]), 3)
    def test_human_triage_never_calls_cloud(self):
        model = FakeModels(human=True)
        self.run_job(model)
        self.assertEqual(model.calls, ["triage"])
        with self.assertRaises(m.Refused):
            self.command("approve", self.job["id"], "--note", "Override")
    def test_failures_are_retained_without_automatic_retry(self):
        for stage in ("triage", "work", "review"):
            with self.subTest(stage=stage):
                job = copy.deepcopy(self.job)
                with self.assertRaises((m.Refused, KeyboardInterrupt)):
                    m.run_job(self.store, job, FakeModels(fail_at=stage))
                restored = self.store.job(job["id"])
                self.assertEqual(restored["state"], "human_review")
                self.assertEqual(len(restored["attempts"]), 1)
                self.assertIn("error", restored["attempts"][0])
    def test_duplicate_snapshot_does_not_reset_budget(self):
        self.run_job()
        duplicate = self.store.add(TICKET)
        self.assertEqual(duplicate["id"], self.job["id"])
        self.assertEqual(len(duplicate["attempts"]), 1)
    def test_config_changes_do_not_retarget_existing_job(self):
        self.store.write("config.json", {**CONFIG, "provider": "deepseek", "model": "different"})
        self.assertEqual(self.store.job(self.job["id"])["config"], CONFIG)
    def test_lock_prevents_concurrent_commands(self):
        with self.store.lock():
            with self.assertRaises(m.Refused):
                self.command("status")
    def test_recovery_keeps_attempt_and_requires_note(self):
        self.job["attempts"].append({"number": 1})
        self.store.save(self.job, "working", "Simulated crash")
        with self.assertRaises(m.Refused):
            self.command("run")
        result = self.command("recover", self.job["id"], "--note", "Process exited")
        self.assertEqual(result["state"], "human_review")
        self.assertEqual(result["attempts"], 1)
    def test_publish_requires_review_and_exact_key(self):
        jira = FakeJira()
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, jira, TICKET["key"])
        self.ready()
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, jira, "OTHER-1")
        self.assertEqual(jira.posts, [])
    def test_stale_snapshot_blocks_post(self):
        self.ready()
        jira = FakeJira({**TICKET, "revision": "new-revision"})
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, jira, TICKET["key"])
        self.assertEqual(jira.posts, [])
    def test_different_site_blocks_post(self):
        self.ready()
        jira = FakeJira()
        jira.c = {**CONFIG, "site": "https://wrong.atlassian.net"}
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, jira, TICKET["key"])
        self.assertEqual(jira.posts, [])
    def test_exact_comment_is_saved_before_post_and_not_repeated(self):
        self.ready()
        jira = FakeJira()
        original = jira.comment
        def observed(key, body):
            saved = self.store.job(self.job["id"])
            self.assertEqual(saved["state"], "publishing")
            self.assertEqual(saved["delivery"]["body"], body)
            return original(key, body)
        jira.comment = observed
        m.publish(self.store, self.job, jira, TICKET["key"])
        self.assertEqual(self.job["state"], "published")
        self.assertIn("UNVERIFIED", jira.posts[0][1])
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, jira, TICKET["key"])
        self.assertEqual(len(jira.posts), 1)
    def test_lost_ack_reconciles_without_another_post(self):
        self.ready()
        jira = FakeJira(fail=True)
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, jira, TICKET["key"])
        self.assertEqual(self.job["state"], "delivery_unknown")
        m.reconcile(self.store, self.job, jira)
        self.assertEqual(self.job["state"], "published")
        self.assertEqual(len(jira.posts), 1)
    def test_unobserved_delivery_cannot_be_retried_or_approved(self):
        self.ready()
        jira = FakeJira(fail=True)
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, jira, TICKET["key"])
        jira.posts.clear()
        with self.assertRaises(m.Refused):
            m.reconcile(self.store, self.job, jira)
        for verb in ("approve", "retry", "recover"):
            with self.assertRaises(m.Refused):
                self.command(verb, self.job["id"], "--note", "No evidence")
    def test_fixture_cannot_publish(self):
        self.ready()
        self.job["fixture"] = True
        with self.assertRaises(m.Refused):
            m.publish(self.store, self.job, FakeJira(), TICKET["key"])
    def test_reject_has_no_network_and_is_terminal(self):
        self.run_job()
        result = self.command("reject", self.job["id"], "--note", "Not useful")
        self.assertEqual(result["state"], "rejected")


class ProtocolTests(unittest.TestCase):
    def test_provider_contracts_and_credentials_are_separate(self):
        responses = {
            "claude": {"id": "c", "model": "test-model", "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Proposed response"}]},
            "openai": {"id": "o", "model": "test-model", "status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": "Proposed response"}]}]},
            "deepseek": {"id": "d", "model": "test-model", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": "Proposed response"}}]}}
        for provider, response in responses.items():
            with self.subTest(provider=provider):
                http = FakeHTTP(response)
                c = {**CONFIG, "provider": provider}
                result = m.Models(http, {m.PROVIDERS[provider]: "fixture-secret"}).work(c, {"ticket": TICKET})
                self.assertEqual(result["text"], "Proposed response")
                self.assertFalse(result["verified_execution"])
                url, body, headers = http.calls[0][0]
                self.assertTrue(url.startswith("https://api."))
                self.assertEqual(body["model"], "test-model")
                self.assertNotIn("tools", body)
                self.assertNotIn("fixture-secret", json.dumps(result))
    def test_truncated_and_tool_responses_cannot_pass(self):
        invalid = {"claude": {"stop_reason": "max_tokens"},
                   "openai": {"status": "incomplete"},
                   "deepseek": {"choices": [{"finish_reason": "length"}]}}
        for provider, response in invalid.items():
            with self.subTest(provider=provider), self.assertRaises(m.Refused):
                m.Models(FakeHTTP(response), {m.PROVIDERS[provider]: "x"}).work({**CONFIG, "provider": provider}, {})
    def test_ollama_schema_and_complete_response_required(self):
        value = {"decision": "retry", "reason": "Missing detail", "feedback": ["Add detail"]}
        http = FakeHTTP({"done": True, "done_reason": "stop", "message": {"content": json.dumps(value)}})
        result = m.Models(http, {}).review(CONFIG, TICKET, {})
        self.assertEqual(result, value)
        self.assertEqual(http.calls[0][0][0], "http://127.0.0.1:11434/api/chat")
        self.assertFalse(http.calls[0][0][1]["stream"])
        with self.assertRaises(m.Refused):
            m.Models(FakeHTTP({"done": True, "done_reason": "length"}), {}).review(CONFIG, TICKET, {})
    def test_missing_key_fails_before_network(self):
        http = FakeHTTP()
        with self.assertRaises(m.Refused):
            m.Models(http, {}).preflight(CONFIG)
        self.assertEqual(http.calls, [])
    def test_prompt_cannot_add_routing_fields(self):
        with self.assertRaises(m.Refused):
            m.validate_shape({"decision": "pass", "reason": "x", "feedback": [], "post": True}, m.REVIEW)
    def test_duplicates_and_control_sequences_rejected(self):
        for data in (b'{"decision":"pass","decision":"retry"}', b'{"x":NaN}'):
            with self.assertRaises(m.Refused):
                m.decode(data)
        with self.assertRaises(m.Refused):
            m.clean_text("\x1b]52;secret")
    def test_redirect_cannot_forward_credentials(self):
        with self.assertRaises(m.Refused):
            m.NoRedirect().redirect_request(None, None, 302, None, None, "https://elsewhere.test")
    def test_jira_fetch_and_adf_comment_contract(self):
        http = FakeHTTP({"key": "TEST-7", "fields": {"summary": TICKET["title"], "updated": TICKET["revision"],
            "description": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": TICKET["body"]}]}]}}}, {"id": "1"})
        jira = m.Jira(CONFIG, http, {"JIRA_API_TOKEN": "fixture"})
        self.assertEqual(jira.fetch("TEST-7"), TICKET)
        jira.comment("TEST-7", "Draft")
        self.assertEqual(http.calls[1][0][1]["body"]["version"], 1)
        with self.assertRaises(m.Refused):
            jira.fetch("../elsewhere")
    def test_jira_opaque_rich_content_is_not_silently_lost(self):
        with self.assertRaises(m.Refused):
            m.adf_text({"type": "mediaSingle", "content": []})
    def test_comment_pagination_reaches_later_pages(self):
        http = FakeHTTP({"startAt": 0, "total": 2, "comments": [{"id": "1"}]},
                        {"startAt": 1, "total": 2, "comments": [{"id": "2"}]})
        self.assertEqual([r["id"] for r in m.Jira(CONFIG, http, {"JIRA_API_TOKEN": "x"}).comments("TEST-7")], ["1", "2"])
    def test_origin_rejects_credential_and_path_tricks(self):
        for site in ("http://jira.test", "https://user:password@jira.test", "https://jira.test/path", "https://jira.test?x=y"):
            with self.subTest(site=site), self.assertRaises(m.Refused):
                m.origin(site)


class PackagingTests(unittest.TestCase):
    def test_installed_command_and_repl_work_from_another_directory(self):
        with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as home:
            installed = install.install(home)
            command = installed["command"]
            result = subprocess.run([command, "demo"], cwd="/private/tmp", capture_output=True, text=True, timeout=15, check=True)
            self.assertTrue(json.loads(result.stdout)["fixture"])
            self.assertEqual(json.loads(result.stdout)["network_calls"], 0)
            result = subprocess.run([command], input="demo\nquit\n", cwd="/private/tmp", capture_output=True, text=True, timeout=15, check=True)
            self.assertIn("jira-monkey>", result.stdout)
            self.assertTrue(Path(installed["app"], "Contents/MacOS/jira-monkey").is_file())
            with self.assertRaises(RuntimeError):
                install.install(home)
    def test_demo_does_not_create_default_state(self):
        with patch.object(m.HTTP, "request", side_effect=AssertionError("Network forbidden")):
            result = m.demonstration()
        self.assertEqual(result["states"], ["queued", "queued", "human_review"])
        self.assertEqual(result["attempts"], 2)


if __name__ == "__main__":
    unittest.main()
