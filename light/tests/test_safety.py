import io
import json
import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from urllib.request import Request
from urllib.error import HTTPError

from mcp_agent_bus_light import api, mcp_server, policy
from mcp_agent_bus_light.configure import render_config
from test_api import FakeHTTP, RESPONSE

MESSAGES = [{"role": "user", "content": "Hello, bridge."}]
ENV = {"DUAL_LANE_API_KEY": "sk-fake-only", "DUAL_LANE_ENABLE_API": "1"}


def send(messages=None, **kwargs):
    messages = MESSAGES if messages is None else messages
    model = kwargs.pop("model", "gpt-6-luna")
    cap = kwargs.pop("max_cost_usd", .01)
    output = kwargs.pop("max_output_tokens", 64)
    prepared = policy.prepare_request(messages, model, output, max_cost_usd=cap)
    options = {"max_cost_usd": cap, "user_approved_api_send": True,
               "approved_request_sha256": prepared.sha256, "opener": lambda req, timeout: FakeHTTP(RESPONSE)}
    options.update(kwargs)
    return api.call_api(messages, model, output, **options)


def rpc(name, args):
    return {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": name, "arguments": args}}


class PolicyTests(unittest.TestCase):
    def test_budget_rejects_bool_nan_infinite_zero_negative(self):
        for value in (True, False, float("nan"), float("inf"), -1, 0, None, [], {}, "NaN", "1e9999"):
            with self.subTest(value=value), self.assertRaises(policy.APIBridgeError):
                policy.prepare_request(MESSAGES, max_cost_usd=value)

    def test_low_budget_rejected_before_network(self):
        with patch.object(api, "_direct_open") as opener, self.assertRaisesRegex(policy.APIBridgeError, "exceeds"):
            api.call_api(MESSAGES, max_cost_usd=.0000001)
        opener.assert_not_called()

    def test_cjk_byte_budget_and_under_long_context(self):
        p = policy.prepare_request([{"role": "user", "content": "中" * 2000}], max_cost_usd=.1)
        self.assertEqual(p.input_bytes, 6000)
        self.assertGreater(p.input_token_allowance, p.input_bytes)
        self.assertLess(p.input_token_allowance, 272000)

    def test_aggregate_input_cap(self):
        with self.assertRaisesRegex(policy.APIBridgeError, "Combined"):
            policy.prepare_request([{"role": "user", "content": "中" * 20000}] * 2, max_cost_usd=1)

    def test_extra_message_fields_are_not_forwarded(self):
        for extra in ({"image_url": "local"}, {"tools": []}, {"prompt_cache_breakpoint": {"ttl": "12h"}}):
            with self.subTest(extra=extra), self.assertRaisesRegex(policy.APIBridgeError, "extra"):
                policy.prepare_request([{**MESSAGES[0], **extra}], max_cost_usd=1)

    def test_invalid_messages(self):
        cases = (None, {}, [], [None], [{"role":"system", "content":"a"}], [{"role":"user", "content": " "}], [{"role":"user", "content":"\ud800"}], [{"role": [], "content": "x"}])
        for messages in cases:
            with self.subTest(messages=messages), self.assertRaises(policy.APIBridgeError):
                policy.prepare_request(messages, max_cost_usd=1)

    def test_credential_patterns(self):
        for secret in ("sk-" + "a" * 24, "-----BEGIN PRIVATE KEY-----", "Bearer " + "a" * 24):
            with self.subTest(), self.assertRaisesRegex(policy.APIBridgeError, "credential"):
                policy.prepare_request([{"role":"user", "content":secret}], max_cost_usd=1)

    def test_payload_has_only_expected_fields(self):
        p = policy.prepare_request(MESSAGES, "gpt-6-luna", 64, max_cost_usd=.01)
        body = json.loads(p.body)
        self.assertEqual(set(body), {"model", "input", "max_output_tokens", "store", "service_tier", "prompt_cache_options", "reasoning"})
        self.assertFalse(body["store"])
        self.assertEqual(body["service_tier"], "default")
        self.assertEqual(body["prompt_cache_options"], {"ttl": "30m"})
        self.assertNotIn("previous_response_id", body)

    def test_hash_binds_content_model_output_and_budget(self):
        baseline = policy.prepare_request(MESSAGES, max_cost_usd=.1)
        variants = [policy.prepare_request([{ "role":"user", "content":"Changed"}], max_cost_usd=.1),
                    policy.prepare_request(MESSAGES, "gpt-6-luna", max_cost_usd=.1),
                    policy.prepare_request(MESSAGES, max_output_tokens=64, max_cost_usd=.1),
                    policy.prepare_request(MESSAGES, max_cost_usd=.2)]
        self.assertTrue(all(p.sha256 != baseline.sha256 for p in variants))
        self.assertEqual(baseline.sha256, policy.prepare_request(MESSAGES, max_cost_usd="0.100").sha256)

    def test_budget_reservation_is_not_refunded(self):
        budget = policy.SessionBudget(".001")
        budget.reserve(Decimal(".0008"))
        with self.assertRaisesRegex(policy.APIBridgeError, "exhausted"):
            budget.reserve(Decimal(".0008"))
        self.assertEqual(budget.reserved, Decimal(".0008"))

    def test_stale_prices_fail_closed(self):
        with patch.object(policy, "date") as mocked:
            mocked.today.return_value = date(2027, 1, 1)
            with self.assertRaisesRegex(policy.APIBridgeError, "expired"):
                policy.verify_pricing_freshness()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.fresh = patch.object(api, "verify_pricing_freshness")
        self.fresh.start()
        self.addCleanup(self.fresh.stop)

    def test_approval_and_hash_gate(self):
        with patch.dict(os.environ, ENV), patch.object(api, "_direct_open") as opener:
            for kwargs in ({"user_approved_api_send": False}, {"user_approved_api_send": 1}, {"approved_request_sha256": "0" * 64}):
                with self.subTest(kwargs=kwargs), self.assertRaises(policy.APIBridgeError):
                    send(opener=None, **kwargs)
            opener.assert_not_called()

    def test_enable_gate_and_dedicated_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY":"generic-ignored"}, clear=True):
            with self.assertRaisesRegex(policy.APIBridgeError, "disabled"):
                send()
            with patch.dict(os.environ, {"DUAL_LANE_ENABLE_API":"1"}):
                with self.assertRaisesRegex(policy.APIBridgeError, "missing"):
                    send()

    def test_bad_keys_are_sanitized(self):
        for key in (" leaked-key ", "secret\rheader", "nonascii中"):
            with patch.dict(os.environ, {**ENV, "DUAL_LANE_API_KEY":key}), self.assertRaisesRegex(policy.APIBridgeError, "invalid") as error:
                send()
            self.assertNotIn(key, str(error.exception))

    def test_active_key_cannot_be_sent(self):
        with patch.dict(os.environ, ENV), self.assertRaisesRegex(policy.APIBridgeError, "credential"):
            send([{"role":"user", "content":"Include sk-fake-only here"}])

    def test_endpoint_ignores_ambient_overrides(self):
        seen = []
        def opener(req, timeout):
            seen.append(req)
            return FakeHTTP(RESPONSE)
        with patch.dict(os.environ, {**ENV,"OPENAI_BASE_URL":"https://invalid.example", "HTTP_PROXY":"http://invalid.example"}):
            send(opener=opener)
        self.assertEqual(seen[0].full_url, policy.API_URL)
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer sk-fake-only")

    def test_redirect_never_forwards(self):
        request = Request(policy.API_URL, headers={"Authorization":"Bearer fake"})
        for target in ("https://evil.example", policy.API_URL):
            with self.assertRaisesRegex(policy.APIBridgeError, "Redirect"):
                api._NoRedirect().redirect_request(request, None, 307, "redirect", {}, target)

    def test_errors_no_retry_no_body_leak_and_keep_reservation(self):
        count = 0
        def opener(req, timeout):
            nonlocal count
            count += 1
            raise HTTPError(req.full_url, 429, "PRIVATE", {}, io.BytesIO(b"PRIVATE"))
        budget = policy.SessionBudget(".1")
        with patch.dict(os.environ, ENV), self.assertRaises(policy.APIBridgeError) as error:
            send(opener=opener, budget=budget)
        self.assertEqual(count, 1)
        self.assertGreater(budget.reserved, 0)
        self.assertNotIn("PRIVATE", str(error.exception))

    def test_timeout_is_sanitized(self):
        def opener(req, timeout):
            raise TimeoutError("SECRET")
        with patch.dict(os.environ, ENV), self.assertRaisesRegex(policy.APIBridgeError, "uncertain") as error:
            send(opener=opener)
        self.assertNotIn("SECRET", str(error.exception))

    def test_response_size_cap(self):
        class Huge(FakeHTTP):
            def read(self, size): return b"x" * size
        with patch.dict(os.environ, ENV), self.assertRaisesRegex(policy.APIBridgeError, "size cap"):
            send(opener=lambda req, timeout: Huge({}))

    def test_malformed_usage_is_not_fabricated(self):
        with patch.dict(os.environ, ENV):
            answer = send(opener=lambda req, timeout: FakeHTTP({**RESPONSE, "usage": []}))
        self.assertIsNone(answer.estimated_usd)
        self.assertIsNone(answer.input_tokens)

    def test_bool_fraction_and_missing_cached_usage_rejected(self):
        for usage in ({"input_tokens": True, "output_tokens":1, "input_tokens_details":{"cached_tokens":0}},
                      {"input_tokens":1.5, "output_tokens":1, "input_tokens_details":{"cached_tokens":0}},
                      {"input_tokens":1, "output_tokens":1}):
            self.assertIsNone(api._cost("gpt-6-luna", usage))

    def test_incomplete_text_preserved_and_labeled(self):
        with patch.dict(os.environ, ENV):
            answer = send(opener=lambda req, timeout: FakeHTTP({**RESPONSE, "status":"incomplete"}))
        self.assertEqual(answer.status, "incomplete")

    def test_unexpected_model_has_no_cost_claim(self):
        with patch.dict(os.environ, ENV):
            answer = send(opener=lambda req, timeout: FakeHTTP({**RESPONSE, "model":"unexpected"}))
        self.assertEqual(answer.model, "unexpected")
        self.assertIsNone(answer.estimated_usd)

    def test_malformed_json_shape_and_failed_status(self):
        for response in ([], {"output":None}, {"output":[{"type":"message", "content":None}]}, {**RESPONSE,"status":"failed"}):
            with patch.dict(os.environ, ENV), self.subTest(), self.assertRaises(policy.APIBridgeError):
                send(opener=lambda req, timeout: FakeHTTP(response))


class UnicodeSafetyTests(unittest.TestCase):
    def test_invalid_fingerprint_is_sanitized(self):
        with patch.dict(os.environ, ENV), self.assertRaisesRegex(policy.APIBridgeError, "fingerprint"):
            send(approved_request_sha256="中")

    def test_upstream_surrogate_rejected(self):
        for obj in ({**RESPONSE, "model":"\ud800"}, {**RESPONSE, "output":[{"type":"message","content":[{"type":"output_text","text":"\ud800"}]}]}):
            with patch.dict(os.environ, ENV), patch.object(api, "verify_pricing_freshness"), self.subTest(), self.assertRaisesRegex(policy.APIBridgeError, "Unicode"):
                send(opener=lambda req, timeout: FakeHTTP(obj))

    def test_bad_id_does_not_stop_subsequent_ping(self):
        incoming = b'{"jsonrpc":"2.0","id":"\\ud800","method":"ping"}\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
        out = io.BytesIO()
        mcp_server.serve(io.BytesIO(incoming), out)
        rows = [json.loads(row) for row in out.getvalue().splitlines()]
        self.assertEqual(rows[0]["error"]["code"], -32600)
        self.assertEqual(rows[1]["id"], 2)


class MCPTests(unittest.TestCase):
    def test_preview_no_key_no_network(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(api, "_direct_open") as opener:
            result = mcp_server.process(rpc("api_preview", {"question":"你好", "max_cost_usd":.02}))
        self.assertFalse(result["result"]["isError"])
        self.assertEqual(len(result["result"]["structuredContent"]["request_sha256"]), 64)
        opener.assert_not_called()

    def test_unexpected_argument_not_forwarded(self):
        result = mcp_server.process(rpc("api_preview", {"question":"hello", "max_cost_usd":.02, "files":["private"]}))
        self.assertTrue(result["result"]["isError"])

    def test_invalid_rpc_and_params(self):
        for message in ([], None, {"id":1,"method":"ping"}, {"jsonrpc":"2.0","id":True,"method":"ping"}, {"jsonrpc":"2.0","id":1,"method":"tools/call","params":[]}):
            with self.subTest(message=message):
                self.assertIn("error", mcp_server.process(message))

    def test_notification_no_response(self):
        self.assertIsNone(mcp_server.process({"jsonrpc":"2.0","method":"notifications/initialized"}))

    def test_protocol_negotiation(self):
        for requested in ("2024-11-05", "2025-03-26", "future"):
            result = mcp_server.process({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":requested}})
            self.assertEqual(result["result"]["protocolVersion"], requested if requested != "future" else mcp_server.PROTOCOL)
            self.assertIn("instructions", result["result"])

    def test_binary_stdio_utf8_and_recover_after_bad_line(self):
        incoming = b"bad\n" + json.dumps(rpc("api_preview", {"question":"中文é", "max_cost_usd":.02}),ensure_ascii=False).encode() + b"\n"
        output = io.BytesIO()
        mcp_server.serve(io.BytesIO(incoming), output)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertFalse(lines[1]["result"]["isError"])

    def test_oversize_line_drained_and_next_request_works(self):
        output = io.BytesIO()
        mcp_server.serve(io.BytesIO(b"x" * (mcp_server.MAX_LINE_BYTES + 10) + b'\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n'), output)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["id"], 2)

    def test_nan_and_deep_json_rejected_without_crash(self):
        for line in (b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}\n', b"[" * 1500 + b"]" * 1500 + b"\n"):
            out = io.BytesIO()
            mcp_server.serve(io.BytesIO(line), out)
            self.assertIn(json.loads(out.getvalue())["error"]["code"], (-32700, -32600))

    def test_boundary_question_context_not_broken_by_wrapper(self):
        result = mcp_server.process(rpc("api_preview", {"question":"q"*18000,"context":"c"*6000,"model":"gpt-6-luna","max_cost_usd":.1}))
        self.assertFalse(result["result"]["isError"])


class PlatformTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info < (3, 11), "stdlib tomllib is Python 3.11+; generation remains stdlib 3.10")
    def test_windows_paths_spaces_and_unicode(self):
        import tomllib
        text = render_config("C:\\Users\\A B\\项目\\bridge", "windows")
        parsed = tomllib.loads(text)["mcp_servers"]["agent_bus_light"]
        self.assertEqual(parsed["command"], "py")
        self.assertEqual(parsed["args"], ["-3","-m","mcp_agent_bus_light.mcp_server"])
        self.assertEqual(parsed["cwd"], "C:/Users/A B/项目/bridge")

    @unittest.skipIf(sys.version_info < (3, 11), "stdlib tomllib is Python 3.11+; generation remains stdlib 3.10")
    def test_macos_absolute_python_and_quotes(self):
        import tomllib
        parsed = tomllib.loads(render_config('/Users/A B/bridge"name', "macos", "/opt/homebrew/bin/python3"))["mcp_servers"]["agent_bus_light"]
        self.assertEqual(parsed["cwd"], '/Users/A B/bridge"name')
        self.assertEqual(parsed["command"], "/opt/homebrew/bin/python3")
        self.assertEqual(parsed["default_tools_approval_mode"], "prompt")
        self.assertNotIn("OPENAI_API_KEY", parsed["env_vars"])

    def test_relative_and_control_path_rejected(self):
        for path, platform in (("relative", "linux"), ("C:relative", "windows"), ("/bad\npath", "macos")):
            with self.subTest(), self.assertRaises(ValueError):
                render_config(path, platform)
