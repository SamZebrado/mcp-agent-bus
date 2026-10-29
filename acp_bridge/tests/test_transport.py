"""Offline transport tests: asyncio buffers only, no sockets or subprocesses."""
import asyncio
import json
import unittest
from unittest.mock import patch

from agent_bus_acp.bounded_transport import BoundedTransport
from agent_bus_acp.config import BridgeError


def wire(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


class FakeWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = 0
        self.failure = None
        self.hang_close = False

    def write(self, data):
        if self.failure:
            raise RuntimeError(self.failure)
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.closed += 1

    async def wait_closed(self):
        if self.hang_close:
            await asyncio.Future()


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def make(self, data=b"", eof=True, reader_limit=262144, **limits):
        reader = asyncio.StreamReader(limit=reader_limit)
        reader.feed_data(data)
        if eof:
            reader.feed_eof()
        writer = FakeWriter()
        poisoned = []
        transport = BoundedTransport(reader, writer, on_poison=poisoned.append, **limits)
        return transport, writer, poisoned

    async def request(self, transport, method="initialize", request_id=1):
        await transport.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}})

    async def assert_failure(self, data, code="ACP_INVALID_FRAME", **limits):
        transport, writer, poisoned = self.make(data, **limits)
        with self.assertRaisesRegex(BridgeError, "^" + code + "$"):
            await transport.receive()
        self.assertEqual(poisoned, [code])
        self.assertEqual(writer.data, b"")
        return transport

    async def test_normal_response_and_eof(self):
        message = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}
        transport, _, poisoned = self.make(wire(message))
        await self.request(transport)
        self.assertEqual(await transport.receive(), message)
        self.assertIsNone(await transport.receive())
        self.assertEqual(transport.pending, {})
        self.assertEqual(poisoned, [])

    async def test_permission_fs_terminal_unknown_fixed_denials_no_echo(self):
        marker = "DO_NOT_ECHO_fake_secret_content"
        messages = [{"jsonrpc": "2.0", "id": i, "method": method,
                     "params": {"secret": marker}} for i, method in enumerate(
                         ("session/request_permission", "fs/read_text_file", "terminal/create", marker))]
        transport, writer, poisoned = self.make(b"".join(map(wire, messages)))
        self.assertIsNone(await transport.receive())
        replies = [json.loads(line) for line in writer.data.splitlines()]
        self.assertEqual(replies[0]["result"], {"outcome": {"outcome": "cancelled"}})
        self.assertTrue(all(x["error"] == {"code": -32601, "message": "Client capability disabled"} for x in replies[1:]))
        self.assertNotIn(marker.encode(), writer.data)
        self.assertEqual(transport.denied_requests, 4)
        self.assertEqual(poisoned, [])

    async def test_oversize_reader_error_never_reassembled(self):
        await self.assert_failure(b"x" * 129, "ACP_FRAME_LIMIT", reader_limit=128)

    async def test_complete_oversize_frame(self):
        await self.assert_failure(wire({"jsonrpc": "2.0", "method": "ignored", "params": {"x": "a" * 200}}),
                                  "ACP_FRAME_LIMIT", max_frame_bytes=128)

    async def test_truncated_frame(self):
        await self.assert_failure(b'{"jsonrpc":"2.0"}', "ACP_TRUNCATED_FRAME")

    async def test_invalid_json_utf8_and_empty_line(self):
        for data in (b"not-json\n", b"\xff\n", b"\n", b"[]\n", b"null\n"):
            with self.subTest(data=data):
                await self.assert_failure(data)

    async def test_duplicate_keys_nan_infinity_and_surrogate(self):
        for data in (
            b'{"jsonrpc":"2.0","method":"x","method":"y"}\n',
            b'{"jsonrpc":"2.0","method":"x","params":{"x":NaN}}\n',
            b'{"jsonrpc":"2.0","method":"x","params":{"x":Infinity}}\n',
            b'{"jsonrpc":"2.0","method":"x","params":{"x":1e999}}\n',
            b'{"jsonrpc":"2.0","method":"x","params":{"x":"\\ud800"}}\n',
        ):
            with self.subTest(data=data):
                await self.assert_failure(data)

    async def test_meta_rejected_at_any_depth(self):
        for params in ({"_meta": {}}, {"nested": [{"_meta": {"session_id": "forged"}}]}):
            await self.assert_failure(wire({"jsonrpc": "2.0", "method": "session/update", "params": params}))

    async def test_invalid_envelopes(self):
        for value in (
            {"jsonrpc": "1.0", "method": "x"},
            {"jsonrpc": "2.0", "method": "x", "result": {}},
            {"jsonrpc": "2.0", "method": "x", "params": []},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}},
            {"jsonrpc": "2.0", "id": 1, "result": None},
            {"jsonrpc": "2.0", "id": 1, "error": {"code": True, "message": "x"}},
        ):
            with self.subTest(value=value):
                await self.assert_failure(wire(value))

    async def test_ids_reject_bool_null_float_huge_and_empty(self):
        for request_id in (True, None, 1.0, 1 << 60, "", "x" * 129):
            await self.assert_failure(wire({"jsonrpc": "2.0", "id": request_id, "result": {}}))

    async def test_unknown_response_is_fatal(self):
        await self.assert_failure(wire({"jsonrpc": "2.0", "id": 99, "result": {}}), "ACP_UNEXPECTED_RESPONSE")

    async def test_duplicate_response_is_fatal(self):
        transport, _, poisoned = self.make(wire({"jsonrpc": "2.0", "id": 1, "result": {}}) * 2)
        await self.request(transport)
        await transport.receive()
        with self.assertRaisesRegex(BridgeError, "ACP_UNEXPECTED_RESPONSE"):
            await transport.receive()
        self.assertEqual(poisoned, ["ACP_UNEXPECTED_RESPONSE"])

    async def test_string_and_integer_ids_are_distinct(self):
        transport, _, _ = self.make(wire({"jsonrpc": "2.0", "id": "1", "result": {}}))
        await self.request(transport, request_id=1)
        with self.assertRaisesRegex(BridgeError, "ACP_UNEXPECTED_RESPONSE"):
            await transport.receive()

    async def test_no_outgoing_id_reuse(self):
        transport, _, _ = self.make(wire({"jsonrpc": "2.0", "id": 1, "result": {}}))
        await self.request(transport)
        await transport.receive()
        with self.assertRaisesRegex(BridgeError, "ACP_DUPLICATE_REQUEST_ID"):
            await self.request(transport)

    async def test_prompt_wire_window_ignores_idle_metadata_and_late_text(self):
        text = {"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": "s", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "a"}}}}
        metadata = {"jsonrpc": "2.0", "method": "session/update", "params": {"update": {"sessionUpdate": "usage_update"}}}
        transport, _, _ = self.make(wire(text) + wire({"jsonrpc": "2.0", "id": 1, "result": {}}), eof=False)
        await self.request(transport)
        await transport.receive()  # Ignore idle text, return initialization response.
        self.assertEqual(transport.ignored_updates, 1)
        await self.request(transport, "session/prompt", 2)
        transport.reader.feed_data(wire(metadata) + wire(text) + wire({"jsonrpc": "2.0", "id": 2, "result": {"stopReason": "end_turn"}}) + wire(text))
        transport.reader.feed_eof()
        self.assertEqual(await transport.receive(), text)
        self.assertTrue(transport.prompt_active)
        self.assertEqual((await transport.receive())["result"]["stopReason"], "end_turn")
        self.assertFalse(transport.prompt_active)
        self.assertIsNone(await transport.receive())
        self.assertEqual(transport.ignored_updates, 3)

    async def test_only_one_prompt_active(self):
        transport, _, _ = self.make()
        await self.request(transport, "session/prompt", 1)
        with self.assertRaisesRegex(BridgeError, "ACP_PROMPT_ALREADY_ACTIVE"):
            await self.request(transport, "session/prompt", 2)

    async def test_cancel_is_notification_not_confirmation(self):
        transport, _, _ = self.make()
        await self.request(transport, "session/prompt", 1)
        await transport.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s"}})
        self.assertTrue(transport.prompt_active)
        self.assertEqual(transport.pending, {1: "session/prompt"})

    async def test_unknown_notifications_ignored_but_counted(self):
        data = wire({"jsonrpc": "2.0", "method": "unknown", "params": {}})
        transport, _, _ = self.make(data * 2)
        self.assertIsNone(await transport.receive())
        self.assertEqual(transport.ignored_notifications, 2)
        self.assertEqual(transport.frames, 2)
        self.assertEqual(transport.total_bytes, len(data) * 2)

    async def test_frame_limit_applies_to_ignored_notifications(self):
        data = wire({"jsonrpc": "2.0", "method": "unknown"})
        await self.assert_failure(data * 2, "ACP_FRAME_COUNT_LIMIT", max_frames=1)

    async def test_total_limit_applies_to_ignored_notifications(self):
        data = wire({"jsonrpc": "2.0", "method": "unknown"})
        await self.assert_failure(data * 2, "ACP_TOTAL_BYTES_LIMIT", max_total_bytes=len(data))

    async def test_denial_wire_counts_toward_limit(self):
        transport, writer, poisoned = self.make(wire({"jsonrpc": "2.0", "id": 1, "method": "fs/read_text_file"}), max_frames=1)
        with self.assertRaisesRegex(BridgeError, "ACP_FRAME_COUNT_LIMIT"):
            await transport.receive()
        self.assertEqual(writer.data, b"")
        self.assertEqual(poisoned, ["ACP_FRAME_COUNT_LIMIT"])

    async def test_outgoing_limit_before_write(self):
        transport, writer, _ = self.make(max_frame_bytes=20)
        with self.assertRaisesRegex(BridgeError, "ACP_FRAME_LIMIT"):
            await self.request(transport)
        self.assertEqual(writer.data, b"")

    async def test_upstream_error_body_sanitized(self):
        transport, _, _ = self.make(wire({"jsonrpc": "2.0", "id": 1, "error": {
            "code": -32000, "message": "private upstream details", "data": {"secret": "private"}}}))
        await self.request(transport)
        self.assertEqual((await transport.receive())["error"], {"code": -32000, "message": "ACP_REMOTE_ERROR"})

    async def test_unexpected_eof_with_pending(self):
        transport, _, poisoned = self.make()
        await self.request(transport)
        with self.assertRaisesRegex(BridgeError, "ACP_UNEXPECTED_EOF"):
            await transport.receive()
        self.assertEqual(poisoned, ["ACP_UNEXPECTED_EOF"])

    async def test_write_exception_no_echo_and_no_retry(self):
        transport, writer, poisoned = self.make()
        writer.failure = "private fake secret"
        for _ in range(2):
            with self.assertRaisesRegex(BridgeError, "^ACP_WRITE_FAILED$"):
                await self.request(transport)
        self.assertEqual(poisoned, ["ACP_WRITE_FAILED"])
        self.assertEqual(writer.data, b"")

    async def test_poison_callback_failure_is_not_exposed(self):
        transport, _, _ = self.make(b"invalid\n")
        def failing(_code):
            raise RuntimeError("private fake secret")
        transport._on_poison = failing
        with self.assertRaisesRegex(BridgeError, "^ACP_INVALID_FRAME$"):
            await transport.receive()

    async def test_close_is_idempotent(self):
        transport, writer, poisoned = self.make()
        await transport.close()
        await transport.close()
        self.assertEqual(writer.closed, 1)
        self.assertEqual(poisoned, [])
        with self.assertRaisesRegex(BridgeError, "TRANSPORT_CLOSED"):
            await self.request(transport)

    async def test_close_pending_marks_unknown(self):
        transport, _, poisoned = self.make()
        await self.request(transport)
        await transport.close()
        self.assertEqual(poisoned, ["ACP_CLOSED_WITH_PENDING"])

    async def test_close_timeout_is_bounded(self):
        transport, writer, poisoned = self.make()
        writer.hang_close = True
        with patch("agent_bus_acp.bounded_transport.CLOSE_TIMEOUT_SECONDS", 0.01):
            with self.assertRaisesRegex(BridgeError, "ACP_CLOSE_FAILED"):
                await transport.close()
        self.assertEqual(poisoned, ["ACP_CLOSE_FAILED"])

    async def test_invalid_config(self):
        for limits in ({"max_frames": True}, {"max_frame_bytes": 0}, {"max_total_bytes": -1}):
            with self.assertRaisesRegex(BridgeError, "INVALID_TRANSPORT_CONFIG"):
                self.make(**limits)

    async def test_duplicate_nested_key_is_rejected(self):
        await self.assert_failure(b'{"jsonrpc":"2.0","method":"x","params":{"a":1,"a":2}}\n')

    async def test_outgoing_metadata_and_response_rejected_without_write(self):
        for value, code in (
            ({"jsonrpc": "2.0", "id": 1, "result": {}}, "ACP_INVALID_OUTGOING"),
            ({"jsonrpc": "2.0", "method": "x", "params": {"_meta": {}}}, "ACP_INVALID_FRAME"),
            ({"jsonrpc": "2.0", "method": "session/prompt", "params": {}}, "ACP_INVALID_OUTGOING"),
        ):
            transport, writer, _ = self.make()
            with self.assertRaisesRegex(BridgeError, "^" + code + "$"):
                await transport.send(value)
            self.assertEqual(writer.data, b"")

    async def test_read_exception_has_fixed_code(self):
        transport, _, poisoned = self.make(eof=False)
        transport.reader.set_exception(RuntimeError("private fake secret"))
        with self.assertRaisesRegex(BridgeError, "^ACP_READ_FAILED$"):
            await transport.receive()
        self.assertEqual(poisoned, ["ACP_READ_FAILED"])

    async def test_frame_limit_includes_delimiter_exact_boundary(self):
        data = wire({"jsonrpc": "2.0", "method": "unknown"})
        transport, _, _ = self.make(data, max_frame_bytes=len(data))
        self.assertIsNone(await transport.receive())
        await self.assert_failure(data, "ACP_FRAME_LIMIT", max_frame_bytes=len(data) - 1)

    async def test_remote_error_clears_prompt_wire_window(self):
        transport, _, _ = self.make(wire({"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "private"}}))
        await self.request(transport, "session/prompt", 1)
        await transport.receive()
        self.assertFalse(transport.prompt_active)

    async def test_drain_failure_after_write_poisoned_without_retry(self):
        transport, writer, poisoned = self.make()
        async def fail():
            raise RuntimeError("private fake secret")
        writer.drain = fail
        with self.assertRaisesRegex(BridgeError, "ACP_WRITE_FAILED"):
            await self.request(transport)
        first_bytes = bytes(writer.data)
        with self.assertRaisesRegex(BridgeError, "ACP_WRITE_FAILED"):
            await self.request(transport, request_id=2)
        self.assertEqual(bytes(writer.data), first_bytes)
        self.assertEqual(poisoned, ["ACP_WRITE_FAILED"])

    async def test_cancelled_write_marks_unknown(self):
        transport, writer, poisoned = self.make()
        entered = asyncio.Event()
        async def wait():
            entered.set()
            await asyncio.Future()
        writer.drain = wait
        task = asyncio.create_task(self.request(transport))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(poisoned, ["ACP_WRITE_INTERRUPTED"])

    async def test_active_text_requires_minimal_shape_before_sdk(self):
        for session_id, content in (
            (None, {"type": "text", "text": "a"}),
            ("", {"type": "text", "text": "a"}),
            ("s" * 257, {"type": "text", "text": "a"}),
            ("s", None), ("s", {"type": "image", "text": "a"}),
            ("s", {"type": "text"}), ("s", {"type": "text", "text": 42}),
        ):
            update = {"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": session_id, "update": {"sessionUpdate": "agent_message_chunk", "content": content}}}
            transport, _, poisoned = self.make(wire(update))
            await self.request(transport, "session/prompt", 1)
            with self.assertRaisesRegex(BridgeError, "ACP_INVALID_UPDATE"):
                await transport.receive()
            self.assertEqual(poisoned, ["ACP_INVALID_UPDATE"])

    async def test_prompt_duplicate_or_unknown_late_response_poisoned(self):
        for late_id in (1, 99):
            response = {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "end_turn"}}
            late = {"jsonrpc": "2.0", "id": late_id, "result": {}}
            transport, _, poisoned = self.make(wire(response) + wire(late))
            await self.request(transport, "session/prompt", 1)
            await transport.receive()
            self.assertFalse(transport.prompt_active)
            with self.assertRaisesRegex(BridgeError, "ACP_UNEXPECTED_RESPONSE"):
                await transport.receive()
            self.assertEqual(poisoned, ["ACP_UNEXPECTED_RESPONSE"])


if __name__ == "__main__":
    unittest.main()
