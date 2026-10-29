"""API-only terminal chat. Every complete outgoing history requires human confirmation."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
import getpass
import json
import os
import sys
import warnings
from .api import APIBridgeError, MODELS, call_api
from .policy import SessionBudget, money, prepare_request


@contextmanager
def _prompted_credentials():
    """Local non-echo input, process lifetime only; never changes host credentials."""
    print("Provider: OpenAI only; destination https://api.openai.com/v1/responses")
    print("Key input is hidden and not saved by this application. Process memory is not zeroized.")
    print("Approved text goes to OpenAI; store:false is not zero retention. Host/terminal may retain text.")
    if input("Type OPENAI to select this provider and enter a temporary key: ") != "OPENAI":
        raise APIBridgeError("Provider selection canceled; nothing sent.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            key = getpass.getpass("OpenAI API key (hidden; this process only): ")
    except getpass.GetPassWarning:
        raise APIBridgeError("Hidden key input unavailable; refusing echo fallback.") from None
    if not key or key != key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise APIBridgeError("Invalid key input; nothing sent.")
    names = ("DUAL_LANE_API_KEY", "DUAL_LANE_ENABLE_API")
    old = {name: os.environ.get(name) for name in names}
    try:
        os.environ["DUAL_LANE_API_KEY"] = key
        os.environ["DUAL_LANE_ENABLE_API"] = "1"
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Independent Responses API chat. No Codex auth changes; no files read.")
    p.add_argument("--model", choices=MODELS, default="gpt-6.1-terra")
    p.add_argument("--max-output-tokens", type=int, default=1536)
    p.add_argument("--max-cost-usd", default="0.02", help="Per-request local reservation cap (not a provider billing limit)")
    p.add_argument("--session-budget-usd", default="0.10", help="Cumulative local reservation cap for this process")
    p.add_argument("--preview-only", action="store_true", help="Never send: inspect prepared requests offline")
    p.add_argument("--provider", choices=("openai",), default="openai", help="Only the official fixed OpenAI endpoint is supported")
    p.add_argument("--ask-key", action="store_true", help="Enter a temporary key with non-echo local input; never save it")
    args = p.parse_args(argv)
    if not args.preview_only and not sys.stdin.isatty():
        p.error("Live chat requires an interactive terminal; use --preview-only for piped input.")
    if args.ask_key and args.preview_only:
        p.error("Preview mode never collects a key; omit --ask-key.")
    try:
        cap = money(args.max_cost_usd)
        budget = SessionBudget(args.session_budget_usd)
    except APIBridgeError as exc:
        p.error(str(exc))
    try:
        with _prompted_credentials() if args.ask_key else nullcontext():
            _chat_loop(args, cap, budget)
    except (EOFError, KeyboardInterrupt):
        print("\nCanceled; credentials not saved.")
    except APIBridgeError as exc:
        p.error(str(exc))


def _chat_loop(args, cap, budget):
    history = []
    print("Independent API chat: /quit, /clear, /budget. No transcript files are created.")
    print("Every send includes the displayed recent history, uses API organization credits, and may use purchased credits.")
    print("store:false is NOT zero retention. Local caps are estimates, not an OpenAI billing guarantee.")
    while True:
        try:
            user = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user in ("/quit", "/exit"):
            break
        if user == "/clear":
            history.clear()
            print("History cleared; spending reservations are NOT reset.")
            continue
        if user == "/budget":
            print(json.dumps(budget.status()))
            continue
        if not user:
            continue
        candidate = history[-10:] + [{"role": "user", "content": user}]
        try:
            preview = prepare_request(candidate, args.model, args.max_output_tokens, max_cost_usd=cap)
            # JSON escaping prevents embedded terminal control sequences in input/history.
            print("Exact outgoing text (JSON escaped): " + json.dumps(candidate, ensure_ascii=True))
            print(json.dumps(preview.preview(), ensure_ascii=True))
            if args.preview_only:
                print("Preview only; nothing sent. History unchanged.")
                continue
            try:
                confirmation = input("Approve this exact text/model/cap by typing SEND " + preview.sha256[:8] + ": ")
            except (EOFError, KeyboardInterrupt):
                print("\nCanceled; nothing sent.")
                break
            if confirmation != "SEND " + preview.sha256[:8]:
                print("Canceled; nothing sent. History unchanged.")
                continue
            answer = call_api(candidate, args.model, args.max_output_tokens,
                max_cost_usd=cap, user_approved_api_send=True, approved_request_sha256=preview.sha256, budget=budget)
        except APIBridgeError as exc:
            print(f"API error: {exc}")
            continue
        # Never render terminal escapes from untrusted API output.
        print("api> " + json.dumps(answer.text, ensure_ascii=True))
        print(json.dumps({"usage": answer.as_dict()["usage"], "process_budget": budget.status(), "status": answer.status}))
        history = (candidate + [{"role": "assistant", "content": answer.text}])[-10:]


if __name__ == "__main__":
    main()
