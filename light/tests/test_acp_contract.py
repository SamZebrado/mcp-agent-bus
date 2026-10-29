"""Pure mock contract tests: no Harness, ACP wire, real model, or API calls."""

from __future__ import annotations

import unittest
from unittest import mock

from mcp_agent_bus_light.backends.harness_acp import (
    AcpContractError,
    AcpPermissionDenied,
    HarnessUnavailable,
    MockAcpContract,
    require_harness_backend,
)


class MemoryPeer:
    def __init__(self) -> None:
        self.requests = []
        self.notifications = []
        self.closed = False
        self.on_prompt = None
        self.stop_reason = "end_turn"

    def request(self, method, params):
        self.requests.append((method, params))
        if method == "initialize":
            return {"protocolVersion": 1, "authMethods": []}
        if method == "session/new":
            return {"sessionId": "mock-session"}
        if method == "session/prompt":
            if self.on_prompt is not None:
                self.on_prompt()
            return {"stopReason": self.stop_reason}
        raise AssertionError("Unexpected method in memory peer")

    def notify(self, method, params):
        self.notifications.append((method, params))

    def close(self):
        self.closed = True


def chunk(text, session="mock-session"):
    return {"sessionId": session, "update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text},
    }}


class MockAcpContractTests(unittest.TestCase):
    def ready(self, **limits):
        peer = MemoryPeer()
        contract = MockAcpContract(peer, **limits)
        contract.initialize()
        contract.new_session("/mock/workspace")
        return peer, contract

    def test_real_backend_is_always_unavailable_without_side_effects(self):
        with mock.patch("subprocess.Popen", side_effect=AssertionError("spawn")), \
             mock.patch("socket.socket", side_effect=AssertionError("network")), \
             mock.patch("os.getenv", side_effect=AssertionError("environment")), \
             mock.patch("builtins.open", side_effect=AssertionError("file")):
            with self.assertRaisesRegex(HarnessUnavailable, "HARNESS_UNAVAILABLE"):
                require_harness_backend()

    def test_lifecycle_and_segmented_text_are_mock_only(self):
        peer, contract = self.ready()
        peer.on_prompt = lambda: [contract.handle_update(chunk(t)) for t in ("hello", " 世界")]
        result = contract.prompt("synthetic prompt")
        self.assertEqual(result.text, "hello 世界")
        self.assertTrue(result.mocked)
        self.assertEqual(result.stop_reason, "end_turn")
        self.assertEqual(contract.state, "idle")
        self.assertEqual([m for m, _ in peer.requests], ["initialize", "session/new", "session/prompt"])
        self.assertEqual(peer.requests[0][1]["clientCapabilities"], {})
        self.assertEqual(peer.requests[1][1]["mcpServers"], [])
        contract.close()
        contract.close()
        self.assertTrue(peer.closed)
        self.assertEqual(contract.state, "closed")

    def test_permission_filesystem_terminal_and_unknown_requests_deny(self):
        contract = MockAcpContract(MemoryPeer())
        self.assertEqual(contract.handle_request("session/request_permission", {}),
                         {"outcome": {"outcome": "cancelled"}})
        for method in ("fs/read_text_file", "fs/write_text_file", "terminal/create", "terminal/output",
                       "terminal/kill", "terminal/release", "terminal/wait_for_exit", "unknown"):
            with self.subTest(method=method), self.assertRaises(AcpPermissionDenied):
                contract.handle_request(method, {})

    def test_cancel_requires_terminal_confirmation_and_is_not_retried(self):
        peer, contract = self.ready()
        peer.stop_reason = "cancelled"
        def during_prompt():
            contract.handle_update(chunk("partial"))
            contract.cancel()
            contract.cancel()
            self.assertEqual(contract.state, "cancel_requested")
        peer.on_prompt = during_prompt
        self.assertEqual(contract.prompt("synthetic").stop_reason, "cancelled")
        self.assertEqual(peer.notifications, [("session/cancel", {"sessionId": "mock-session"})])

    def test_unconfirmed_cancel_leaves_unknown_not_success(self):
        peer, contract = self.ready()
        peer.on_prompt = contract.cancel
        with self.assertRaises(AcpContractError):
            contract.prompt("synthetic")
        self.assertEqual(contract.state, "unknown")
        self.assert_no_retry(peer, contract)

    def test_failed_cancel_notification_is_unknown_and_not_retried(self):
        peer, contract = self.ready()
        peer.notify = mock.Mock(side_effect=RuntimeError("private peer detail"))
        peer.on_prompt = contract.cancel
        with self.assertRaises(AcpContractError) as caught:
            contract.prompt("synthetic")
        self.assertNotIn("private peer detail", str(caught.exception))
        self.assertEqual(contract.state, "unknown")
        self.assertEqual(peer.notify.call_count, 1)
        self.assert_no_retry(peer, contract)

    def test_close_during_prompt_cannot_resurrect_idle_session(self):
        peer, contract = self.ready()
        peer.on_prompt = contract.close
        with self.assertRaises(AcpContractError):
            contract.prompt("synthetic")
        self.assertTrue(peer.closed)
        self.assertEqual(contract.state, "unknown")
        self.assert_no_retry(peer, contract)

    def assert_no_retry(self, peer, contract):
        count = len(peer.requests)
        with self.assertRaises(AcpContractError):
            contract.prompt("do not replay")
        self.assertEqual(len(peer.requests), count)

    def test_peer_failure_redacted_unknown_and_no_retry(self):
        peer, contract = self.ready()
        def fail():
            raise RuntimeError("private peer detail")
        peer.on_prompt = fail
        with self.assertRaises(AcpContractError) as caught:
            contract.prompt("synthetic")
        self.assertNotIn("private peer detail", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(contract.state, "unknown")
        self.assert_no_retry(peer, contract)

    def test_unknown_terminal_reason_is_not_silently_successful(self):
        peer, contract = self.ready()
        peer.stop_reason = "unrecognized"
        with self.assertRaises(AcpContractError):
            contract.prompt("synthetic")
        self.assertEqual(contract.state, "unknown")
        self.assert_no_retry(peer, contract)

    def test_total_utf8_bytes_are_bounded_not_character_count(self):
        peer, contract = self.ready(max_output_bytes=6)
        peer.on_prompt = lambda: [contract.handle_update(chunk(t)) for t in ("你", "好")]
        self.assertEqual(contract.prompt("synthetic").text, "你好")
        peer.on_prompt = lambda: [contract.handle_update(chunk(t)) for t in ("你", "好", "!")]
        with self.assertRaises(AcpContractError):
            contract.prompt("synthetic")
        self.assertEqual(contract.state, "unknown")
        self.assert_no_retry(peer, contract)

    def test_segment_count_is_bounded_including_empty_segments(self):
        peer, contract = self.ready(max_segments=2)
        peer.on_prompt = lambda: [contract.handle_update(chunk(t)) for t in ("", "", "")]
        with self.assertRaises(AcpContractError):
            contract.prompt("synthetic")
        self.assertEqual(contract.state, "unknown")

    def test_cross_session_or_non_text_update_fails_closed(self):
        for update in (chunk("wrong", "other-session"),
                       {"sessionId": "mock-session", "update": {"sessionUpdate": "unknown"}},
                       {"sessionId": "mock-session", "update": {"sessionUpdate": "agent_message_chunk",
                                                                "content": {"type": "image", "text": "x"}}}):
            peer, contract = self.ready()
            peer.on_prompt = lambda: contract.handle_update(update)
            with self.subTest(update=update), self.assertRaises(AcpContractError):
                contract.prompt("synthetic")
            self.assertEqual(contract.state, "unknown")

    def test_usage_is_not_a_budget_or_billing_confirmation(self):
        peer, contract = self.ready()
        peer.on_prompt = lambda: contract.handle_update({
            "sessionId": "mock-session", "update": {"sessionUpdate": "usage_update", "cost": 0},
        })
        self.assertEqual(contract.prompt("synthetic").text, "")

    def test_invalid_state_and_empty_prompt_make_no_request(self):
        peer = MemoryPeer()
        contract = MockAcpContract(peer)
        with self.assertRaises(AcpContractError):
            contract.prompt("synthetic")
        self.assertEqual(peer.requests, [])
        contract.initialize()
        contract.new_session(r"C:\mock workspace")
        count = len(peer.requests)
        with self.assertRaises(AcpContractError):
            contract.prompt(" ")
        with self.assertRaises(AcpContractError):
            contract.cancel()
        self.assertEqual(len(peer.requests), count)

    def test_invalid_workspace_rejected_before_new_session_request(self):
        peer = MemoryPeer()
        contract = MockAcpContract(peer)
        contract.initialize()
        with self.assertRaises(AcpContractError):
            contract.new_session("relative")
        self.assertEqual(len(peer.requests), 1)

    def test_bad_protocol_and_auth_requirement_fail_without_credentials(self):
        for response in ({"protocolVersion": 2}, {"protocolVersion": True},
                         {"protocolVersion": 1, "authMethods": ["not supported"]}):
            peer = MemoryPeer()
            peer.request = mock.Mock(return_value=response)
            contract = MockAcpContract(peer)
            with self.subTest(response=response), self.assertRaises(AcpContractError):
                contract.initialize()
            self.assertEqual(contract.state, "unknown")
            self.assertEqual(peer.request.call_count, 1)

    def test_local_close_failure_does_not_claim_closed(self):
        peer, contract = self.ready()
        peer.close = mock.Mock(side_effect=RuntimeError("private peer detail"))
        with self.assertRaises(AcpContractError) as caught:
            contract.close()
        self.assertNotIn("private peer detail", str(caught.exception))
        self.assertEqual(contract.state, "unknown")

    def test_limits_require_positive_integers(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MockAcpContract(MemoryPeer(), max_output_bytes=value)


if __name__ == "__main__":
    unittest.main()
