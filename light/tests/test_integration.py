"""Additive-light guarantees; no real models, bus queue, files, or credentials."""
import contextlib
import io
import json
import os
import unittest
from decimal import Decimal
from unittest.mock import patch

from mcp_agent_bus_light import api, cli, mcp_server, policy
from test_api import FakeHTTP, RESPONSE


class LightIntegrationTests(unittest.TestCase):
    def test_capabilities_do_not_claim_real_harness_or_audit(self):
        c = cli.capabilities()
        self.assertEqual(c["default_backend"], "responses")
        self.assertFalse(c["harness_acp"]["implemented"])
        self.assertFalse(c["audit"]["enabled"])
        self.assertFalse(c["audit"]["implemented"])
        self.assertFalse(c["task_queue"]["used"])

    def test_help_does_not_import_provider_or_bus(self):
        original_import = __import__
        def guarded(name, *args, **kwargs):
            if name == "mcp_agent_bus" or name.startswith("mcp_agent_bus.") or name in ("api", "mcp_server", "harness_acp"):
                raise AssertionError("Unexpected runtime import on help")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded), patch("os.getenv", side_effect=AssertionError("credential lookup")), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as stop:
                cli.main(["--help"])
        self.assertEqual(stop.exception.code, 0)

    def test_harness_selection_fails_closed(self):
        with patch("subprocess.Popen", side_effect=AssertionError("process launch")), patch("os.getenv", side_effect=AssertionError("credential lookup")), contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as stop:
                cli.main(["--backend", "harness-acp", "serve"])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("HARNESS_UNAVAILABLE", output.getvalue())

    def test_harness_capability_query_is_read_only(self):
        with patch("os.getenv", side_effect=AssertionError("environment")), contextlib.redirect_stdout(io.StringIO()) as output:
            result = cli.main(["--backend", "harness-acp", "capabilities"])
        self.assertEqual(result, 0)
        self.assertFalse(json.loads(output.getvalue())["harness_acp"]["enabled"])

    def test_unknown_commands_do_not_fall_back_to_api(self):
        with patch.object(api, "_direct_open", side_effect=AssertionError("network")), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                cli.main(["queue"])
            with self.assertRaises(SystemExit):
                cli.main(["serve", "--audit"])

    def test_direct_mcp_identity_is_distinct_from_original_bus(self):
        result = mcp_server.process({"jsonrpc":"2.0", "id":1, "method":"initialize", "params":{}})
        self.assertEqual(result["result"]["serverInfo"]["name"], "mcp-agent-bus-light")
        result = mcp_server.process({"jsonrpc":"2.0", "id":2, "method":"tools/list"})
        self.assertEqual({x["name"] for x in result["result"]["tools"]}, {"api_preview", "api_second_opinion"})

    def test_unknown_outcome_blocks_same_fingerprint_without_retry(self):
        messages = [{"role":"user", "content":"Synthetic unknown outcome test"}]
        prepared = policy.prepare_request(messages, "gpt-6-luna", 64, max_cost_usd=.01)
        budget = policy.SessionBudget(".1")
        calls = []
        def fail(request, timeout):
            calls.append(request)
            raise TimeoutError("private detail")
        options = dict(max_cost_usd=.01, user_approved_api_send=True,
                       approved_request_sha256=prepared.sha256, budget=budget, opener=fail)
        with patch.dict(os.environ, {"DUAL_LANE_ENABLE_API":"1", "DUAL_LANE_API_KEY":"fixed-fake-key"}), patch.object(api, "verify_pricing_freshness"):
            with self.assertRaisesRegex(policy.APIBridgeError, "uncertain"):
                api.call_api(messages, "gpt-6-luna", 64, **options)
            self.assertEqual(len(calls), 1)
            reserved = budget.reserved
            with self.assertRaisesRegex(policy.APIBridgeError, "uncertain"):
                api.call_api(messages, "gpt-6-luna", 64, **options)
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.reserved, reserved)
        self.assertEqual(reserved, prepared.reserved_usd)

    def test_invalid_response_keeps_unknown_quarantine(self):
        messages = [{"role":"user", "content":"Synthetic malformed response"}]
        prepared = policy.prepare_request(messages, "gpt-6-luna", 64, max_cost_usd=.01)
        budget = policy.SessionBudget(".1")
        calls = []
        def malformed(req, timeout):
            calls.append(req)
            return FakeHTTP({"status":"failed", "output":[]})
        options = dict(max_cost_usd=.01, user_approved_api_send=True, approved_request_sha256=prepared.sha256, budget=budget, opener=malformed)
        with patch.dict(os.environ, {"DUAL_LANE_ENABLE_API":"1", "DUAL_LANE_API_KEY":"fixed-fake-key"}), patch.object(api, "verify_pricing_freshness"):
            with self.assertRaises(policy.APIBridgeError):
                api.call_api(messages, "gpt-6-luna", 64, **options)
            with self.assertRaisesRegex(policy.APIBridgeError, "uncertain"):
                api.call_api(messages, "gpt-6-luna", 64, **options)
        self.assertEqual(len(calls), 1)

    def test_complete_clears_unknown_but_never_refunds(self):
        budget = policy.SessionBudget(".01")
        budget.reserve(Decimal(".001"), "synthetic-hash")
        with self.assertRaises(policy.APIBridgeError):
            budget.reserve(Decimal(".001"), "synthetic-hash")
        budget.complete("synthetic-hash")
        self.assertEqual(budget.reserved, Decimal(".001"))
        budget.reserve(Decimal(".001"), "synthetic-hash")
        self.assertEqual(budget.reserved, Decimal(".002"))

    def test_changing_only_cap_does_not_bypass_unknown_transport_identity(self):
        messages = [{"role": "user", "content": "Synthetic cap-change retry"}]
        first = policy.prepare_request(messages, "gpt-6-luna", 64, max_cost_usd=.01)
        second = policy.prepare_request(messages, "gpt-6-luna", 64, max_cost_usd=.02)
        self.assertNotEqual(first.sha256, second.sha256)
        self.assertEqual(first.body_sha256, second.body_sha256)
        calls = []
        def timeout(req, timeout):
            calls.append(req)
            raise TimeoutError()
        budget = policy.SessionBudget(".1")
        with patch.dict(os.environ, {"DUAL_LANE_ENABLE_API":"1", "DUAL_LANE_API_KEY":"fixed-fake-key"}), patch.object(api, "verify_pricing_freshness"):
            for cap, prepared in ((.01, first), (.02, second)):
                with self.assertRaisesRegex(policy.APIBridgeError, "uncertain"):
                    api.call_api(messages, "gpt-6-luna", 64, max_cost_usd=cap,
                                 user_approved_api_send=True, approved_request_sha256=prepared.sha256,
                                 budget=budget, opener=timeout)
        self.assertEqual(len(calls), 1)
        self.assertEqual(budget.reserved, first.reserved_usd)

    def test_missing_response_identity_or_status_remains_unknown(self):
        messages = [{"role":"user", "content":"Synthetic partial response"}]
        prepared = policy.prepare_request(messages, "gpt-6-luna", 64, max_cost_usd=.01)
        for missing in ("id", "object", "status", "model"):
            response = {k:v for k,v in RESPONSE.items() if k != missing}
            budget = policy.SessionBudget(".1")
            calls = []
            def partial(req, timeout):
                calls.append(req)
                return FakeHTTP(response)
            with self.subTest(missing=missing), patch.dict(os.environ, {"DUAL_LANE_ENABLE_API":"1", "DUAL_LANE_API_KEY":"fixed-fake-key"}), patch.object(api, "verify_pricing_freshness"):
                for _ in range(2):
                    with self.assertRaises(policy.APIBridgeError):
                        api.call_api(messages, "gpt-6-luna", 64, max_cost_usd=.01,
                                     user_approved_api_send=True, approved_request_sha256=prepared.sha256,
                                     budget=budget, opener=partial)
                self.assertEqual(len(calls), 1)
