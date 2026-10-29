"""Restricted, bounded message transport for the official ACP SDK.

This is framing and policy, not an agent loop or an ACP RPC implementation.
Use an asyncio StreamReader configured with ``limit <= max_frame_bytes``;
``readuntil`` overruns are fatal and are never reassembled. All wire traffic
(including fixed denials) counts toward the shared byte/frame limits.
"""
from __future__ import annotations

import asyncio
import json
from typing import Callable

from .config import BridgeError


CLOSE_TIMEOUT_SECONDS = 1.0
MAX_SAFE_INTEGER = (1 << 53) - 1


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonfinite number")


def _valid_id(value):
    return ((type(value) is int and abs(value) <= MAX_SAFE_INTEGER)
            or (type(value) is str and 0 < len(value) <= 128))


class BoundedTransport:
    """Single-consumer ACP Transport with fixed reverse-request denials.

    ``on_poison(code)`` is synchronous and called at most once. It must not
    log protocol content. Limits and counters include both wire directions.
    No request is retried. A poisoned transport cannot be used again.
    """

    def __init__(self, reader, writer, *, on_poison: Callable[[str], None],
                 max_frame_bytes=262144, max_total_bytes=2097152, max_frames=4096):
        limits = (max_frame_bytes, max_total_bytes, max_frames)
        if any(type(value) is not int or value < 1 for value in limits) or not callable(on_poison):
            raise BridgeError("INVALID_TRANSPORT_CONFIG")
        self.reader = reader
        self.writer = writer
        self.max_frame_bytes = max_frame_bytes
        self.max_total_bytes = max_total_bytes
        self.max_frames = max_frames
        self.total_bytes = 0
        self.frames = 0
        self.denied_requests = 0
        self.ignored_updates = 0
        self.ignored_notifications = 0
        self.pending = {}
        self.prompt_active = False
        self._sent_ids = set()
        self._on_poison = on_poison
        self._poison_code = None
        self._closed = False
        self._write_lock = asyncio.Lock()

    def _poison(self, code):
        if self._poison_code is None:
            self._poison_code = code
            self.prompt_active = False
            try:
                self._on_poison(code)
            except Exception:
                pass

    def _fail(self, code):
        self._poison(code)
        raise BridgeError(self._poison_code) from None

    def _check_open(self):
        if self._poison_code is not None:
            raise BridgeError(self._poison_code) from None
        if self._closed:
            raise BridgeError("TRANSPORT_CLOSED")

    def _charge(self, length):
        if length > self.max_frame_bytes:
            self._fail("ACP_FRAME_LIMIT")
        if self.frames + 1 > self.max_frames:
            self._fail("ACP_FRAME_COUNT_LIMIT")
        if self.total_bytes + length > self.max_total_bytes:
            self._fail("ACP_TOTAL_BYTES_LIMIT")
        self.frames += 1
        self.total_bytes += length

    def _decode(self, raw):
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                               parse_constant=_constant)
            if type(value) is not dict or value.get("jsonrpc") != "2.0":
                raise ValueError()
            # Reject metadata before SDK kwargs adaptation. Validate strings to
            # reject escaped lone surrogates, including in ignored payloads.
            stack = [value]
            while stack:
                item = stack.pop()
                if type(item) is dict:
                    if "_meta" in item:
                        raise ValueError()
                    stack.extend(item.keys())
                    stack.extend(item.values())
                elif type(item) is list:
                    stack.extend(item)
                elif type(item) is str:
                    item.encode("utf-8")
                elif type(item) is float:
                    # json.loads can overflow finite exponent syntax to inf.
                    if not (-float("inf") < item < float("inf")):
                        raise ValueError()
            if "id" in value and not _valid_id(value["id"]):
                raise ValueError()
            if "method" in value:
                if set(value) - {"jsonrpc", "id", "method", "params"}:
                    raise ValueError()
                if type(value["method"]) is not str or not 1 <= len(value["method"]) <= 128:
                    raise ValueError()
                if "params" in value and type(value["params"]) is not dict:
                    raise ValueError()
            else:
                if "id" not in value or set(value) - {"jsonrpc", "id", "result", "error"}:
                    raise ValueError()
                if ("result" in value) == ("error" in value):
                    raise ValueError()
                if "result" in value and type(value["result"]) is not dict:
                    raise ValueError()
                if "error" in value:
                    error = value["error"]
                    if (type(error) is not dict or set(error) - {"code", "message", "data"}
                            or type(error.get("code")) is not int
                            or abs(error["code"]) > MAX_SAFE_INTEGER
                            or type(error.get("message")) is not str):
                        raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self._fail("ACP_INVALID_FRAME")

    def _encode(self, message):
        try:
            raw = (json.dumps(message, separators=(",", ":"), ensure_ascii=False,
                              allow_nan=False) + "\n").encode("utf-8")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self._fail("ACP_INVALID_FRAME")
        normalized = self._decode(raw)
        return raw, normalized

    async def _write_locked(self, raw):
        self._check_open()
        self._charge(len(raw))
        try:
            self.writer.write(raw)
            await self.writer.drain()
        except asyncio.CancelledError:
            self._poison("ACP_WRITE_INTERRUPTED")
            raise
        except Exception:
            self._fail("ACP_WRITE_FAILED")

    async def send(self, message):
        """Send an SDK request/notification; never accept caller-made responses."""
        self._check_open()
        raw, frame = self._encode(message)
        if "method" not in frame:
            self._fail("ACP_INVALID_OUTGOING")
        async with self._write_lock:
            self._check_open()
            if "id" in frame:
                request_id = frame["id"]
                if request_id in self._sent_ids:
                    self._fail("ACP_DUPLICATE_REQUEST_ID")
                if frame["method"] == "session/prompt" and self.prompt_active:
                    self._fail("ACP_PROMPT_ALREADY_ACTIVE")
                self._sent_ids.add(request_id)
                self.pending[request_id] = frame["method"]
                if frame["method"] == "session/prompt":
                    self.prompt_active = True
            elif frame["method"] == "session/prompt":
                self._fail("ACP_INVALID_OUTGOING")
            await self._write_locked(raw)

    async def _deny(self, frame):
        self.denied_requests += 1
        if frame["method"] == "session/request_permission":
            response = {"jsonrpc": "2.0", "id": frame["id"],
                        "result": {"outcome": {"outcome": "cancelled"}}}
        else:
            response = {"jsonrpc": "2.0", "id": frame["id"],
                        "error": {"code": -32601, "message": "Client capability disabled"}}
        raw, _ = self._encode(response)
        async with self._write_lock:
            await self._write_locked(raw)

    async def receive(self):
        """Return one response/text update, internally denying reverse requests."""
        self._check_open()
        while True:
            try:
                raw = await self.reader.readuntil(b"\n")
            except asyncio.LimitOverrunError:
                self._fail("ACP_FRAME_LIMIT")
            except asyncio.IncompleteReadError as exc:
                if exc.partial:
                    self._fail("ACP_TRUNCATED_FRAME")
                if self.pending:
                    self._fail("ACP_UNEXPECTED_EOF")
                return None
            except asyncio.CancelledError:
                raise
            except Exception:
                self._fail("ACP_READ_FAILED")
            self._check_open()
            self._charge(len(raw))
            frame = self._decode(raw)
            if "method" not in frame:
                request_id = frame["id"]
                if request_id not in self.pending:
                    self._fail("ACP_UNEXPECTED_RESPONSE")
                method = self.pending.pop(request_id)
                if method == "session/prompt":
                    self.prompt_active = False
                if "error" in frame:
                    # Do not allow remote diagnostic bodies into SDK exceptions.
                    frame["error"] = {"code": frame["error"]["code"], "message": "ACP_REMOTE_ERROR"}
                return frame
            if "id" in frame:
                await self._deny(frame)
                continue
            if frame["method"] == "session/update":
                params = frame.get("params", {})
                update = params.get("update")
                if (self.prompt_active and type(update) is dict
                        and update.get("sessionUpdate") == "agent_message_chunk"):
                    # SDK notification validation errors are logged/swallowed.
                    # Check the indispensable shape here so malformed text
                    # cannot silently disappear before the runner sees it.
                    session_id = params.get("sessionId")
                    content = update.get("content")
                    if (type(session_id) is not str or not 1 <= len(session_id) <= 256
                            or type(content) is not dict or content.get("type") != "text"
                            or type(content.get("text")) is not str):
                        self._fail("ACP_INVALID_UPDATE")
                    return frame
                self.ignored_updates += 1
            else:
                self.ignored_notifications += 1

    async def close(self):
        """Close the writer once; process reaping belongs to the runner."""
        if self._closed:
            return
        self._closed = True
        self.prompt_active = False
        if self.pending:
            self._poison("ACP_CLOSED_WITH_PENDING")
        try:
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), CLOSE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            self._poison("ACP_CLOSE_INTERRUPTED")
            raise
        except Exception:
            self._fail("ACP_CLOSE_FAILED")
