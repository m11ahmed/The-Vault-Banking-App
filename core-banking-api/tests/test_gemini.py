"""Gemini integration exercised through the real SDK and an offline HTTP transport."""
import json
import os
import tempfile
from unittest.mock import patch

import httpx
from openai import OpenAI as SDKClient

from app import assistant_service as service
from tests.support import BankingTestCase


def function_call(name, arguments, call_id="call_1", **extra):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments),
    }, **extra}


def completion(text=None, calls=None):
    message = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = calls
    return {"id": "gemini-test", "object": "chat.completion", "created": 1,
            "model": "gemini-3.5-flash-lite", "choices": [{"index": 0, "message": message,
            "finish_reason": "tool_calls" if calls else "stop"}]}


class GeminiTests(BankingTestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge_directory = tempfile.TemporaryDirectory(
            prefix="banking-gemini-test-", ignore_cleanup_errors=True
        )

    @classmethod
    def tearDownClass(cls):
        service._cached_knowledge_store.cache_clear()
        cls.knowledge_directory.cleanup()

    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {
            "AI_MODE": "gemini", "GEMINI_API_KEY": "fakeGeminiKey", "GEMINI_MODEL": "",
            "OPENAI_API_KEY": "must-not-use-this-openai-key", "OPENAI_MODEL": "wrong-provider",
            "CHROMA_DIR": self.knowledge_directory.name,
            "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false",
        })
        env.start()
        self.addCleanup(env.stop)
        self.requests = []
        self.client_options = []
        self.sdk_clients = []

    def ask(self, message="What is my balance?", account_id=None, **kwargs):
        return self.client.post("/assistant/chat", json={
            "message": message, "account_id": account_id, **kwargs,
        })

    def run_script(self, script, **kwargs):
        scripted = iter(script)

        def transport(request):
            self.requests.append((request, json.loads(request.content)))
            item = next(scripted)
            if isinstance(item, BaseException):
                raise item
            status, body = item if isinstance(item, tuple) else (200, item)
            return httpx.Response(status, json=body, request=request)

        def client_factory(**options):
            self.client_options.append(options)
            client = SDKClient(**options, http_client=httpx.Client(transport=httpx.MockTransport(transport)))
            self.sdk_clients.append(client)
            return client

        with patch("openai.OpenAI", side_effect=client_factory), patch.object(
            service, "_create_openai_client", side_effect=AssertionError("No OpenAI provider fallback")
        ):
            result = self.ask(**kwargs)
        for client in self.sdk_clients:
            self.assertTrue(client.is_closed())
        return result

    def test_status_requires_gemini_key_and_exposes_explicit_mode(self):
        response = self.client.get("/assistant/status")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mode"], "gemini")
        self.assertTrue(response.json()["provider_configured"])
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            response = self.client.get("/assistant/status")
        self.assertFalse(response.json()["provider_configured"])
        self.assertNotIn("fakeGeminiKey", response.text)

    def test_missing_gemini_key_never_uses_existing_openai_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch("openai.OpenAI") as constructor:
            response = self.ask()
        constructor.assert_not_called()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("GEMINI_API_KEY", response.json()["detail"])
        self.assertNotIn("must-not-use-this-openai-key", response.text)

    def test_real_sdk_serial_tool_roundtrip_preserves_signature_and_live_values(self):
        selected = self.account(125)["id"]
        response = self.run_script([
            completion(calls=[function_call("get_balance", {}, extra_content={
                "google": {"thought_signature": "opaque-test-signature"},
            })]),
            completion(calls=[function_call("get_recent_transactions", {"limit": 1}, "call_2")]),
            completion("Your balance is PKR 125.00; your latest transaction is a deposit."),
        ], account_id=selected, history=[{"role": "user", "content": "Hello"}])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["mode"], "gemini")
        self.assertEqual(response.json()["tools_used"], ["get_balance", "get_recent_transactions"])
        self.assertEqual(len(self.requests), 3)
        for request, body in self.requests:
            self.assertEqual(str(request.url), service.GEMINI_BASE_URL + "chat/completions")
            self.assertEqual(request.headers["authorization"], "Bearer fakeGeminiKey")
            self.assertEqual(body["model"], "gemini-3.5-flash-lite")
            self.assertEqual({item["function"]["name"] for item in body["tools"]}, {
                "get_balance", "get_recent_transactions", "search_banking_faq",
            })
        second_messages = self.requests[1][1]["messages"]
        self.assertEqual(second_messages[0]["role"], "system")
        self.assertEqual(second_messages[-2]["tool_calls"][0]["extra_content"]["google"]["thought_signature"], "opaque-test-signature")
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call_1")
        self.assertEqual(json.loads(second_messages[-1]["content"])["balance"], "125.00")
        last_result = json.loads(self.requests[2][1]["messages"][-1]["content"])
        self.assertEqual(last_result["transactions"][0]["amount"], "125.00")
        self.assertEqual(self.client_options[0]["timeout"], 20.0)
        self.assertEqual(self.client_options[0]["max_retries"], 0)

    def test_gemini_model_override_is_explicit_and_google_endpoint_is_fixed(self):
        with patch.dict(os.environ, {
            "GEMINI_MODEL": "configured-gemini-model", "OPENAI_BASE_URL": "https://wrong.invalid/",
        }):
            response = self.run_script([completion("Hello from Gemini.")], message="Hello")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.requests[0][1]["model"], "configured-gemini-model")
        self.assertEqual(self.requests[0][0].url.host, "generativelanguage.googleapis.com")

    def test_quota_error_is_actionable_429_without_retry_or_provider_fallback(self):
        response = self.run_script([(429, {"error": {
            "message": "Quota exhausted; private secret fakeGeminiKey", "type": "RESOURCE_EXHAUSTED",
        }})])
        self.assertEqual(response.status_code, 429, response.text)
        self.assertIn("quota", response.json()["detail"])
        self.assertIn("AI_MODE=demo", response.json()["detail"])
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("fakeGeminiKey", response.text)

    def test_upstream_failure_is_sanitized(self):
        response = self.run_script([(401, {"error": {
            "message": "bad credential fakeGeminiKey private-account-data", "type": "UNAUTHENTICATED",
        }})])
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("Gemini is unavailable", response.json()["detail"])
        self.assertNotIn("fakeGeminiKey", response.text)
        self.assertNotIn("private-account-data", response.text)
        self.assertEqual(len(self.requests), 1)

    def test_connect_error_retries_identical_request_once_before_success(self):
        selected = self.account(125)["id"]
        with patch.object(service, "_execute_read_tool", wraps=service._execute_read_tool) as execute:
            response = self.run_script([
                httpx.ConnectError("private transport data fakeGeminiKey"),
                completion(calls=[function_call("get_balance", {})]),
                completion("Your balance is PKR 125.00."),
            ], account_id=selected)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tools_used"], ["get_balance"])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[0][1], self.requests[1][1])
        self.assertEqual(self.client_options[0]["max_retries"], 0)
        execute.assert_called_once()
        self.assertEqual(self.balance(selected), 125)

    def test_connection_retry_after_tool_keeps_output_signature_and_executes_once(self):
        selected = self.account(125)["id"]
        with patch.object(service, "_execute_read_tool", wraps=service._execute_read_tool) as execute:
            response = self.run_script([
                completion(calls=[function_call("get_balance", {}, extra_content={
                    "google": {"thought_signature": "opaque-retry-signature"},
                })]),
                httpx.ConnectError("private transport data fakeGeminiKey"),
                completion("Your balance is PKR 125.00."),
            ], account_id=selected)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tools_used"], ["get_balance"])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[1][1], self.requests[2][1])
        replay = self.requests[2][1]["messages"]
        self.assertEqual(replay[-2]["tool_calls"][0]["extra_content"]["google"]["thought_signature"],
                         "opaque-retry-signature")
        self.assertEqual(replay[-1]["role"], "tool")
        self.assertEqual(replay[-1]["tool_call_id"], "call_1")
        self.assertEqual(json.loads(replay[-1]["content"])["balance"], "125.00")
        execute.assert_called_once()
        self.assertEqual(self.balance(selected), 125)

    def test_persistent_connect_error_stops_after_one_retry_and_is_sanitized(self):
        response = self.run_script([
            httpx.ConnectError("private transport data fakeGeminiKey"),
            httpx.ConnectError("private transport data fakeGeminiKey"),
            completion("A third attempt must never happen."),
        ])
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("connection to Gemini failed", response.json()["detail"])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.requests[0][1], self.requests[1][1])
        self.assertNotIn("fakeGeminiKey", response.text)
        self.assertNotIn("private transport data", response.text)

    def test_connect_error_retry_budget_is_shared_across_model_rounds(self):
        selected = self.account(125)["id"]
        with patch.object(service, "_execute_read_tool", wraps=service._execute_read_tool) as execute:
            response = self.run_script([
                httpx.ConnectError("private transport data fakeGeminiKey"),
                completion(calls=[function_call("get_balance", {})]),
                httpx.ConnectError("private transport data fakeGeminiKey"),
                completion("A second retry must never happen."),
            ], account_id=selected)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("connection to Gemini failed", response.json()["detail"])
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[0][1], self.requests[1][1])
        self.assertEqual(self.requests[2][1]["messages"][-1]["role"], "tool")
        execute.assert_called_once()
        self.assertEqual(self.balance(selected), 125)

    def test_read_timeout_is_not_retried_and_has_distinct_sanitized_response(self):
        response = self.run_script([
            httpx.ReadTimeout("private transport data fakeGeminiKey"),
            completion("A timed-out request must never be repeated."),
        ])
        self.assertEqual(response.status_code, 504, response.text)
        self.assertIn("took too long", response.json()["detail"])
        self.assertEqual(len(self.requests), 1)
        self.assertNotIn("fakeGeminiKey", response.text)
        self.assertNotIn("private transport data", response.text)

    def test_read_and_write_errors_are_not_retried(self):
        for error_type in (httpx.ReadError, httpx.WriteError):
            with self.subTest(error=error_type.__name__):
                self.requests.clear()
                response = self.run_script([
                    error_type("private transport data fakeGeminiKey"),
                    completion("A possibly submitted request must never be repeated."),
                ])
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("connection to Gemini failed", response.json()["detail"])
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn("fakeGeminiKey", response.text)
                self.assertNotIn("private transport data", response.text)

    def test_http_access_model_and_server_errors_are_not_retried(self):
        for status in (403, 404, 503):
            with self.subTest(status=status):
                self.requests.clear()
                response = self.run_script([
                    (status, {"error": {"message": "private provider data fakeGeminiKey"}}),
                    completion("An HTTP failure must never be repeated."),
                ])
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("Gemini is unavailable", response.json()["detail"])
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn("fakeGeminiKey", response.text)
                self.assertNotIn("private provider data", response.text)

    def test_account_override_and_unknown_write_tool_are_refused(self):
        selected = self.account(100)["id"]
        other = self.account(999)["id"]
        response = self.run_script([
            completion(calls=[function_call("get_balance", {"account_id": other}),
                              function_call("withdraw", {"amount": 95}, "call_2")]),
            completion("Those tool requests were refused."),
        ], account_id=selected)
        self.assertEqual(response.status_code, 200, response.text)
        results = [json.loads(item["content"]) for item in self.requests[1][1]["messages"] if item["role"] == "tool"]
        self.assertEqual(len(results), 2)
        self.assertTrue(all("error" in item for item in results))
        self.assertNotIn("999", json.dumps(results))
        self.assertEqual(response.json()["tools_used"], [])
        self.assertEqual((self.balance(selected), self.balance(other)), (100, 999))

    def test_faq_tool_returns_chroma_citations(self):
        response = self.run_script([
            completion(calls=[function_call("search_banking_faq", {"query": "fees"})]),
            completion("The fictional demo does not charge fees. [demo-fees]"),
        ], message="What are the fees?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["sources"][0]["id"], "demo-fees")
        self.assertEqual(response.json()["tools_used"], ["search_banking_faq"])

    def test_model_round_limit_is_enforced(self):
        response = self.run_script([
            completion(calls=[function_call("get_balance", {}, f"call_{number}")])
            for number in range(service.MAX_MODEL_ROUNDS)
        ])
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("response limit", response.json()["detail"])
        self.assertEqual(len(self.requests), service.MAX_MODEL_ROUNDS)

    def test_tool_call_limit_is_enforced(self):
        response = self.run_script([completion(calls=[
            function_call("get_balance", {}, f"call_{number}")
            for number in range(service.MAX_TOOL_CALLS + 1)
        ])])
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("tool-call limit", response.json()["detail"])
        self.assertEqual(len(self.requests), 1)
