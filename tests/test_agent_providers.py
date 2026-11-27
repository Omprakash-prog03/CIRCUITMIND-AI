import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from circuitmind.agent_providers import (
    AgentProviderError,
    ProviderConfig,
    generate_agent_plan,
    generate_evidence_summary,
    probe_agent_provider,
    provider_configuration_status,
)


class FakeProviderHandler(BaseHTTPRequestHandler):
    authorization = None
    completion_content = '{"summary":"Validate the saved circuit.","calls":[{"tool":"circuit.validate","arguments":{}}]}'
    last_request = None

    def do_GET(self):
        type(self).authorization = self.headers.get("Authorization")
        if self.path == "/api/tags":
            payload = {"models": [{"name": "local-circuit-model:latest"}]}
        elif self.path == "/v1/models":
            payload = {"data": [{"id": "mock/openai-compatible"}]}
        else:
            self.send_error(404)
            return
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        type(self).authorization = self.headers.get("Authorization")
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        type(self).last_request = json.loads(body)
        if self.path == "/api/chat":
            payload = {"message": {"content": type(self).completion_content}}
        elif self.path == "/v1/chat/completions":
            payload = {"choices": [{"message": {"content": type(self).completion_content}}]}
        else:
            self.send_error(404)
            return
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):
        pass


class AgentProviderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeProviderHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_unconfigured_status_does_not_probe(self):
        config = ProviderConfig(None, None, None, None)
        status = provider_configuration_status(config)
        self.assertEqual(status["status"], "not-configured")
        self.assertFalse(status["configured"])

    def test_ollama_probe_only_lists_local_models_and_does_not_send_prompts(self):
        config = ProviderConfig("ollama", self.base, "local-circuit-model:latest", "must-not-be-sent")
        result = probe_agent_provider(config)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["models"], ["local-circuit-model:latest"])
        self.assertTrue(result["selected_model_available"])
        self.assertEqual(result["generation_capability"], "not-probed")
        self.assertNotIn("must-not-be-sent", json.dumps(result))
        self.assertIsNone(FakeProviderHandler.authorization)

    def test_openai_compatible_probe_uses_optional_key_without_returning_it(self):
        config = ProviderConfig("openai-compatible", self.base + "/v1", None, "test-secret")
        result = probe_agent_provider(config)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["models"], ["mock/openai-compatible"])
        self.assertEqual(FakeProviderHandler.authorization, "Bearer test-secret")
        self.assertNotIn("test-secret", json.dumps(result))

    def test_ollama_generation_is_bounded_and_sends_only_user_prompt_and_tool_policy(self):
        FakeProviderHandler.completion_content = '{"summary":"Check saved topology.","calls":[{"tool":"circuit.validate","arguments":{}}]}'
        result = generate_agent_plan("Please check my saved circuit.",
                                     ProviderConfig("ollama", self.base, "local-model", None))
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(result["model"], "local-model")
        self.assertEqual(len(result["completion_sha256"]), 64)
        request = FakeProviderHandler.last_request
        self.assertEqual(request["model"], "local-model")
        self.assertEqual(request["messages"][1]["content"], "Please check my saved circuit.")
        self.assertIn("Do not include mutations", request["messages"][0]["content"])
        self.assertEqual(request["stream"], False)

    def test_openai_compatible_generation_keeps_key_in_header_only(self):
        FakeProviderHandler.completion_content = '{"summary":"Read project metadata.","calls":[{"tool":"project.summary","arguments":{}}]}'
        result = generate_agent_plan("Summarize the project.",
                                     ProviderConfig("openai-compatible", self.base + "/v1", "mock-model", "secret-key"))
        self.assertEqual(result["provider"], "openai-compatible")
        self.assertEqual(FakeProviderHandler.authorization, "Bearer secret-key")
        self.assertNotIn("secret-key", json.dumps(result))
        self.assertEqual(FakeProviderHandler.last_request["response_format"], {"type": "json_object"})

    def test_evidence_summary_sends_only_bounded_execution_facts_to_local_model(self):
        evidence = {"execution_status": "passed", "topology": {"valid": True},
                    "available_evidence_keys": ["execution_status", "topology.valid"]}
        FakeProviderHandler.completion_content = json.dumps({
            "summary": "The declared topology check passed.",
            "evidence_keys": ["execution_status", "topology.valid"]})
        result = generate_evidence_summary(evidence, ProviderConfig("ollama", self.base, "local-model", None))
        self.assertEqual(result["provider"], "ollama")
        self.assertEqual(len(result["evidence_sha256"]), 64)
        sent = json.loads(FakeProviderHandler.last_request["messages"][1]["content"])
        self.assertEqual(sent, evidence)
        self.assertIn("Do not add electrical", FakeProviderHandler.last_request["messages"][0]["content"])

    def test_generation_rejects_missing_model_and_oversized_prompt_before_network(self):
        config = ProviderConfig("ollama", self.base, None, None)
        with self.assertRaises(AgentProviderError):
            generate_agent_plan("summarize", config)
        with self.assertRaisesRegex(ValueError, "2000 characters"):
            generate_agent_plan("x" * 2_001, ProviderConfig("ollama", self.base, "local", None))

    def test_non_loopback_credentials_and_invalid_ports_are_rejected(self):
        for url in ("https://127.0.0.1:11434", "http://example.com:11434",
                    "http://user:pass@localhost:11434", "http://127.0.0.1:99999"):
            with self.subTest(url=url):
                result = probe_agent_provider(ProviderConfig("ollama", url, None, None))
                self.assertEqual(result["status"], "invalid-configuration")
                self.assertFalse(result["configured"])


if __name__ == "__main__":
    unittest.main()
