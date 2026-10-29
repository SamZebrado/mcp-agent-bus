import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from mcp_agent_bus_light.api import APIBridgeError, _cost, _extract_text, call_api
from mcp_agent_bus_light import mcp_server
from mcp_agent_bus_light.policy import prepare_request


def approved_call(messages, model="gpt-6.1-terra", max_output_tokens=1536, **kwargs):
    prepared = prepare_request(messages, model, max_output_tokens, max_cost_usd=1)
    with patch.dict(os.environ, {"DUAL_LANE_ENABLE_API": "1"}), patch("mcp_agent_bus_light.api.verify_pricing_freshness"):
        return call_api(messages, model, max_output_tokens, max_cost_usd=1,
                        user_approved_api_send=True, approved_request_sha256=prepared.sha256, **kwargs)


class FakeHTTP:
    def __init__(self, obj):
        self.body = io.BytesIO(json.dumps(obj).encode())
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def read(self, size): return self.body.read(size)


RESPONSE = {
    "id": "resp_test_fixture", "object": "response", "status": "completed",
    "model": "gpt-6-luna", "service_tier": "default",
    "output": [{"type": "message", "content": [{"type": "output_text", "text": "Hello!"}]}],
    "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200}, "output_tokens": 50},
}


class APITests(unittest.TestCase):
    def test_model_and_api_usage(self):
        seen = {}
        def opener(req, timeout):
            seen["url"] = req.full_url
            seen["payload"] = json.loads(req.data)
            seen["auth"] = req.get_header("Authorization")
            return FakeHTTP({**RESPONSE, "model": seen["payload"]["model"]})
        with patch.dict(os.environ, {"DUAL_LANE_API_KEY": "sk-test-no-real-key"}):
            ans = approved_call([{"role": "user", "content": "Hello"}], model="gpt-6.1-sol", opener=opener)
        self.assertEqual(ans.text, "Hello!")
        self.assertEqual(ans.estimated_usd, round((800*2 + 200*.1 + 50*10)/1e6, 8))
        self.assertTrue(seen["payload"]["store"] is False)
        self.assertEqual(seen["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(seen["auth"], "Bearer sk-test-no-real-key")

    def test_missing_key(self):
        with patch.dict(os.environ, {"DUAL_LANE_API_KEY": ""}):
            with self.assertRaisesRegex(APIBridgeError, "missing"):
                approved_call([{"role": "user", "content": "hello"}])

    def test_prevents_invalid_models(self):
        with self.assertRaisesRegex(APIBridgeError, "Unsupported"):
            call_api([{"role": "user", "content": "hello"}], model="user-model")

    def test_no_approval_no_request(self):
        msg = {"jsonrpc":"2.0","id":1,"method":"tools/call", "params":{
            "name":"api_second_opinion", "arguments":{"question":"hello", "user_approved_api_send":False}}}
        result = mcp_server.process(msg)
        self.assertTrue(result["result"]["isError"])
        self.assertIn("approval", result["result"]["content"][0]["text"])

    def test_rpc_initialize_and_tools(self):
        init = mcp_server.process({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "mcp-agent-bus-light")
        listing = mcp_server.process({"jsonrpc":"2.0","id":2,"method":"tools/list"})
        self.assertEqual(listing["result"]["tools"][0]["name"], "api_second_opinion")

    def test_sanitizes_api_errors(self):
        def opener(req, timeout):
            raise HTTPError(req.full_url, 401, "secret should not leak", {}, io.BytesIO(b"secret"))
        with patch.dict(os.environ, {"DUAL_LANE_API_KEY": "sk-test"}):
            with self.assertRaises(APIBridgeError) as ex:
                approved_call([{"role": "user", "content": "hello"}], opener=opener)
        self.assertNotIn("secret", str(ex.exception))

    def test_malformed_output(self):
        with self.assertRaises(APIBridgeError):
            _extract_text({"output": []})

    def test_calculation_bad_usage(self):
        self.assertIsNone(_cost("gpt-6.1-sol", {"input_tokens": 10, "output_tokens": 20,
                                                      "input_tokens_details": {"cached_tokens": 11}}))


if __name__ == "__main__": unittest.main()
