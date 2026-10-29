"""Offline admission checks; no network, credential files, or prompt logging."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING

API_URL = "https://api.openai.com/v1/responses"
MODELS = ("gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-luna")
# USD / 1M tokens: standard uncached, cached read, output. See the standalone README.
PRICES = {
    "gpt-6.1-sol": (2.00, 0.10, 10.00),
    "gpt-6.1-terra": (1.00, 0.05, 5.00),
    "gpt-6-luna": (0.10, 0.01, 0.50),
}
PRICE_REVIEW_DATE = date(2026, 10, 29)
PRICE_REVIEW_VALID_UNTIL = date(2026, 11, 28)
MAX_INPUT_BYTES = 96000
BUDGET_NOTE = (
    "Local admission estimate, not an OpenAI-enforced USD ceiling or proof of promotional-credit eligibility. "
    "Uses UTF-8 bytes + framing allowance, maximum output, 30m cache-write rates and 10% safety margin. "
    "No automatic retry. Each attempted request reserves its full estimate, even on error."
)
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._-]{20,}")


class APIBridgeError(Exception):
    """Sanitized error: never include a key, prompt, response body, or URL query."""


def positive_int(value: object, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise APIBridgeError(f"{label} must be an integer in [{minimum}, {maximum}].")
    return value


def money(value: object, label: str = "max_cost_usd", maximum: str = "10") -> Decimal:
    if type(value) not in (int, float, str, Decimal):
        raise APIBridgeError(f"{label} must be a positive finite USD amount.")
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not 0 < result <= Decimal(maximum):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise APIBridgeError(f"{label} must be a positive finite USD amount, at most {maximum}.") from None
    return result


@dataclass(frozen=True)
class PreparedRequest:
    body: bytes
    sha256: str
    model: str
    max_output_tokens: int
    input_bytes: int
    input_token_allowance: int
    reserved_usd: Decimal
    max_cost_usd: Decimal

    @property
    def body_sha256(self) -> str:
        """Transport identity excludes the approval cap; changing a cap is not a retry escape."""
        return hashlib.sha256(self.body).hexdigest()

    def preview(self) -> dict:
        return {
            "request_sha256": self.sha256,
            "endpoint": API_URL,
            "model": self.model,
            "input_utf8_bytes": self.input_bytes,
            "input_token_allowance": self.input_token_allowance,
            "max_output_tokens": self.max_output_tokens,
            "estimated_reservation_usd": float(self.reserved_usd),
            "max_cost_usd": float(self.max_cost_usd),
            "store": False,
            "service_tier": "default",
            "price_review_date": PRICE_REVIEW_DATE.isoformat(),
            "price_review_valid_until": PRICE_REVIEW_VALID_UNTIL.isoformat(),
            "pricing_fresh": PRICE_REVIEW_DATE <= date.today() <= PRICE_REVIEW_VALID_UNTIL,
            "billing_lane": "Independent OpenAI API organization; never Codex subscription",
            "note": BUDGET_NOTE,
        }


def prepare_request(messages: list[dict], model: str = "gpt-6.1-terra", max_output_tokens: int = 1536,
                    *, max_cost_usd: object) -> PreparedRequest:
    if not isinstance(model, str) or model not in MODELS:
        raise APIBridgeError("Unsupported model. Allowed: " + ", ".join(MODELS))
    positive_int(max_output_tokens, "max_output_tokens", 64, 4096)
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        raise APIBridgeError("Provide 1–12 messages.")
    clean = []
    total = 0
    for msg in messages:
        if not isinstance(msg, dict) or set(msg) != {"role", "content"}:
            raise APIBridgeError("Messages must contain exactly role and content; no extra fields.")
        if msg["role"] not in ("user", "assistant"):
            raise APIBridgeError("Only user and assistant text messages are supported.")
        content = msg["content"]
        if not isinstance(content, str) or not 1 <= len(content) <= 24000 or not content.strip():
            raise APIBridgeError("Each message must contain 1–24,000 characters of nonempty text.")
        if SECRET_PATTERN.search(content):
            raise APIBridgeError("Recognizable credential material blocked. Remove secrets before preview or send.")
        try:
            total += len(content.encode("utf-8"))
        except UnicodeError:
            raise APIBridgeError("Input contains invalid Unicode.") from None
        clean.append({"role": msg["role"], "content": content})
    if total > MAX_INPUT_BYTES:
        raise APIBridgeError("Combined text exceeds the 96,000-byte local input cap.")
    cap = money(max_cost_usd)
    # Deliberately pessimistic, offline estimate, NOT a provider/tokenizer guarantee.
    allowance = total + 256 * len(clean) + 1024
    regular, _, output = (Decimal(str(x)) for x in PRICES[model])
    reserve = ((allowance * regular * Decimal("1.25") + max_output_tokens * output)
               * Decimal("1.10") / 1_000_000).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)
    if reserve > cap:
        raise APIBridgeError(f"Estimated reservation ${reserve} exceeds max_cost_usd. Reduce text/output or explicitly approve a higher cap.")
    payload = {
        "model": model, "input": clean, "max_output_tokens": max_output_tokens,
        "store": False, "service_tier": "default", "prompt_cache_options": {"ttl": "30m"},
        "reasoning": {"effort": "low" if model == "gpt-6.1-sol" else "none"},
    }
    body = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(body + b"\nmax_cost_usd=" + format(cap.normalize(), "f").encode("ascii")).hexdigest()
    return PreparedRequest(body, digest, model, max_output_tokens, total, allowance, reserve, cap)


def verify_pricing_freshness() -> None:
    if not PRICE_REVIEW_DATE <= date.today() <= PRICE_REVIEW_VALID_UNTIL:
        raise APIBridgeError("Pricing review expired or system date is invalid; update verified rates before sending.")


class SessionBudget:
    """Thread-safe in-memory reservation cap; resets at process exit, not /clear."""
    def __init__(self, limit_usd: object = "0.10"):
        self.limit = money(limit_usd, "session_budget_usd")
        self.reserved = Decimal("0")
        self._lock = threading.Lock()
        self._uncertain_requests: set[str] = set()

    def reserve(self, amount: Decimal, request_sha256: str | None = None) -> None:
        with self._lock:
            if request_sha256 is not None and request_sha256 in self._uncertain_requests:
                raise APIBridgeError("Previous identical request has an uncertain outcome; retry blocked in this process.")
            if self.reserved + amount > self.limit:
                raise APIBridgeError("Process session budget exhausted; no request sent. /clear does not reset spending.")
            self.reserved += amount
            if request_sha256 is not None:
                self._uncertain_requests.add(request_sha256)

    def complete(self, request_sha256: str) -> None:
        """Only a validated response clears uncertainty; reservations are never refunded."""
        with self._lock:
            self._uncertain_requests.discard(request_sha256)

    def status(self) -> dict:
        with self._lock:
            return {"limit_usd": float(self.limit), "reserved_usd": float(self.reserved),
                    "remaining_usd": float(self.limit - self.reserved), "scope": "this process only"}
