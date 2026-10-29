"""Disabled Harness backend and a TEST-ONLY, in-memory ACP contract.

This is not an ACP transport, a Harness runner, or an agent/model loop. It does
not read credentials, launch processes, access files, or perform network I/O.
The mock contract uses synchronous injected peers and is not thread-safe. It
does not authorize API use: authorization and budgets belong to the outer
policy layer. Production callers MUST use require_harness_backend(), which
always fails closed. Passing mock tests proves no real ACP/DSH compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Protocol


class HarnessUnavailable(RuntimeError):
    """The real backend has not been enabled or implemented."""


def require_harness_backend() -> None:
    """Fail before any credentials, dependencies, or transports are accessed."""
    raise HarnessUnavailable(
        "HARNESS_UNAVAILABLE: Harness/ACP execution is disabled; only a "
        "test-only in-memory contract exists."
    )


class AcpContractError(RuntimeError):
    """Sanitized mock-contract failure; never includes peer exception text."""


class AcpPermissionDenied(AcpContractError):
    """No client-side action is permitted by this mock contract."""


class AcpPeer(Protocol):
    """Injected in-memory test peer, NOT a wire transport interface guarantee."""

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]: ...

    def notify(self, method: str, params: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class MockPromptResult:
    text: str
    stop_reason: str
    mocked: bool = True


class MockAcpContract:
    """Minimal one-session text contract for deterministic unit tests only.

    A peer may call handle_update/handle_request/cancel during request().
    Unknown outcomes poison the session; there is no retry or recovery loop.
    close() closes only the local mock peer and does NOT confirm remote work
    stopped. Usage updates are ignored, never treated as billing evidence.
    """

    _STOP_REASONS = {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}

    def __init__(
        self, peer: AcpPeer, *, max_output_bytes: int = 65536, max_segments: int = 256
    ) -> None:
        for value in (max_output_bytes, max_segments):
            if type(value) is not int or value <= 0:
                raise ValueError("Mock output limits must be positive integers.")
        self._peer = peer
        self._state = "new"
        self._session_id: str | None = None
        self._max_output_bytes = max_output_bytes
        self._max_segments = max_segments
        self._segments: list[str] = []
        self._output_bytes = 0

    @property
    def state(self) -> str:
        return self._state

    def _require_state(self, *states: str) -> None:
        if self._state not in states:
            raise AcpContractError("ACP_MOCK_INVALID_STATE")

    def _unknown(self) -> AcpContractError:
        self._state = "unknown"
        self._segments.clear()
        self._output_bytes = 0
        return AcpContractError("ACP_MOCK_OUTCOME_UNKNOWN; automatic retry is disabled.")

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._peer.request(method, params)
            if not isinstance(result, dict) or self._state == "unknown":
                raise ValueError("invalid mock result")
            return result
        except Exception:
            raise self._unknown() from None

    def initialize(self) -> None:
        self._require_state("new")
        response = self._request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {},
            "clientInfo": {"name": "agent-bus-mock-contract", "version": "0"},
        })
        if type(response.get("protocolVersion")) is not int or response["protocolVersion"] != 1:
            raise self._unknown()
        if response.get("authMethods", []) != []:
            raise self._unknown()
        self._state = "ready"

    def new_session(self, cwd: str) -> str:
        self._require_state("ready")
        if not isinstance(cwd, str) or not (
            PurePosixPath(cwd).is_absolute() or PureWindowsPath(cwd).is_absolute()
        ):
            raise AcpContractError("ACP_MOCK_ABSOLUTE_WORKSPACE_REQUIRED")
        response = self._request("session/new", {"cwd": cwd, "mcpServers": []})
        session_id = response.get("sessionId")
        if not isinstance(session_id, str) or not session_id or len(session_id) > 256:
            raise self._unknown()
        self._session_id = session_id
        self._state = "idle"
        return session_id

    def prompt(self, text: str) -> MockPromptResult:
        self._require_state("idle")
        if not isinstance(text, str) or not text.strip():
            raise AcpContractError("ACP_MOCK_TEXT_REQUIRED")
        self._segments = []
        self._output_bytes = 0
        self._state = "prompting"
        response = self._request("session/prompt", {
            "sessionId": self._session_id,
            "prompt": [{"type": "text", "text": text}],
        })
        if self._state not in {"prompting", "cancel_requested"}:
            raise self._unknown()
        reason = response.get("stopReason")
        if not isinstance(reason, str) or reason not in self._STOP_REASONS:
            raise self._unknown()
        if self._state == "cancel_requested" and reason != "cancelled":
            raise self._unknown()
        self._state = "idle"
        return MockPromptResult("".join(self._segments), reason)

    def handle_update(self, params: dict[str, Any]) -> None:
        """Accept only matching-session text chunks and non-authoritative usage."""
        self._require_state("prompting", "cancel_requested")
        try:
            if params.get("sessionId") != self._session_id:
                raise ValueError("session mismatch")
            update = params["update"]
            if update.get("sessionUpdate") == "usage_update":
                return
            if update.get("sessionUpdate") != "agent_message_chunk":
                raise ValueError("unsupported mock update")
            content = update["content"]
            text = content["text"]
            if content.get("type") != "text" or not isinstance(text, str):
                raise ValueError("non-text mock update")
            size = len(text.encode("utf-8"))
            if len(self._segments) >= self._max_segments:
                raise ValueError("segment limit")
            if self._output_bytes + size > self._max_output_bytes:
                raise ValueError("output byte limit")
            self._segments.append(text)
            self._output_bytes += size
        except Exception:
            raise self._unknown() from None

    def handle_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Deny permissions and every filesystem/terminal/unknown request."""
        if method == "session/request_permission":
            return {"outcome": {"outcome": "cancelled"}}
        raise AcpPermissionDenied("ACP_MOCK_REQUEST_DENIED")

    def cancel(self) -> None:
        self._require_state("prompting", "cancel_requested")
        if self._state == "cancel_requested":
            return
        self._state = "cancel_requested"
        try:
            self._peer.notify("session/cancel", {"sessionId": self._session_id})
        except Exception:
            raise self._unknown() from None

    def close(self) -> None:
        """Close the local mock peer only; never assert remote cancellation."""
        if self._state == "closed":
            return
        try:
            self._peer.close()
        except Exception:
            raise self._unknown() from None
        self._segments.clear()
        self._output_bytes = 0
        self._state = "closed"
