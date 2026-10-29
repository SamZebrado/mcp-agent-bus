import contextlib
import io
import json
import os
import getpass
import warnings
import unittest
from unittest.mock import patch

from mcp_agent_bus_light import chat
from mcp_agent_bus_light.api import APIAnswer
from mcp_agent_bus_light.policy import APIBridgeError, prepare_request


def run_chat(inputs, args=(), answer=None):
    output = io.StringIO()
    with patch("sys.stdin.isatty", return_value=True), patch("sys.argv", ["chat", *args]), patch("builtins.input", side_effect=inputs), \
         patch.object(chat, "call_api", return_value=answer or APIAnswer("reply", "gpt-6-luna", 2, 0, 1, .000001)) as send, \
         contextlib.redirect_stdout(output):
        chat.main()
    return output.getvalue(), send


class ChatTests(unittest.TestCase):
    def test_temporary_hidden_key_does_not_persist_or_print(self):
        output = io.StringIO()
        synthetic = "fixed-fake-input-key"
        with patch.dict(os.environ, {"DUAL_LANE_API_KEY":"old-fake", "DUAL_LANE_ENABLE_API":"0"}), patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=["OPENAI", "/quit"]), patch.object(chat.getpass, "getpass", return_value=synthetic) as getter, patch.object(chat, "call_api") as send, contextlib.redirect_stdout(output):
            chat.main(["--ask-key"])
            self.assertEqual(os.environ["DUAL_LANE_API_KEY"], "old-fake")
            self.assertEqual(os.environ["DUAL_LANE_ENABLE_API"], "0")
        getter.assert_called_once()
        send.assert_not_called()
        self.assertNotIn(synthetic, output.getvalue())

    def test_temporary_key_cleanup_on_keyboard_interrupt(self):
        with patch.dict(os.environ, {}, clear=True), patch("builtins.input", return_value="OPENAI"), patch.object(chat.getpass, "getpass", return_value="fixed-fake-key"), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                with chat._prompted_credentials():
                    self.assertEqual(os.environ["DUAL_LANE_ENABLE_API"], "1")
                    raise KeyboardInterrupt()
            self.assertNotIn("DUAL_LANE_API_KEY", os.environ)
            self.assertNotIn("DUAL_LANE_ENABLE_API", os.environ)

    def test_non_echo_fallback_is_refused(self):
        def warning(prompt):
            warnings.warn("Synthetic no-TTY", getpass.GetPassWarning)
            raise AssertionError("Echo fallback must not execute")
        with patch("builtins.input", return_value="OPENAI"), patch.object(chat.getpass, "getpass", side_effect=warning), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(APIBridgeError, "Hidden key"):
                with chat._prompted_credentials():
                    self.fail("Must not enable")

    def test_preview_never_collects_key_and_unknown_provider_rejected(self):
        for args in (["--preview-only", "--ask-key"], ["--provider", "custom"]):
            with patch.object(chat.getpass, "getpass") as getter, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                chat.main(args)
            getter.assert_not_called()

    def test_cancel_does_not_send(self):
        text, send = run_chat(["hello", "no", "/quit"])
        send.assert_not_called()
        self.assertIn("Canceled", text)

    def test_preview_only_no_send(self):
        text, send = run_chat(["你好", "/quit"], ["--preview-only"])
        send.assert_not_called()
        self.assertIn("Preview only", text)

    def test_eof_and_ctrl_c_exit(self):
        for stop in (EOFError, KeyboardInterrupt):
            _, send = run_chat([stop])
            send.assert_not_called()

    def test_confirm_eof_does_not_send(self):
        text, send = run_chat(["hello", EOFError])
        send.assert_not_called()
        self.assertIn("Canceled", text)

    def test_exact_confirmation_binds_payload(self):
        p = prepare_request([{"role":"user", "content":"hello"}], max_cost_usd=.02)
        _, send = run_chat(["hello", "SEND " + p.sha256[:8], "/quit"])
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs["approved_request_sha256"], p.sha256)
        self.assertIs(send.call_args.kwargs["user_approved_api_send"], True)

    def test_clear_does_not_reset_budget(self):
        text, send = run_chat(["/clear", "/budget", "/quit"])
        send.assert_not_called()
        self.assertIn("NOT reset", text)
        self.assertIn('"limit_usd": 0.1', text)

    def test_returned_terminal_escapes_not_rendered(self):
        p = prepare_request([{"role":"user", "content":"hello"}], max_cost_usd=.02)
        answer = APIAnswer("\x1b]52;clipboard-secret\x07", "gpt-6-luna", 2, 0, 1, 0)
        text, _ = run_chat(["hello", "SEND " + p.sha256[:8], "/quit"], answer=answer)
        self.assertNotIn("\x1b", text)
        self.assertIn("\\u001b", text)

    def test_history_sent_only_after_new_confirmation(self):
        first = prepare_request([{"role":"user","content":"one"}], max_cost_usd=.02)
        second_messages = [{"role":"user","content":"one"},{"role":"assistant","content":"reply"},{"role":"user","content":"two"}]
        second = prepare_request(second_messages, max_cost_usd=.02)
        text, send = run_chat(["one", "SEND "+first.sha256[:8], "two", "SEND "+second.sha256[:8], "/quit"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args[0], second_messages)
        self.assertIn('"role": "assistant"', text)

    def test_failed_request_does_not_add_history_or_retry(self):
        first = prepare_request([{"role":"user","content":"one"}], max_cost_usd=.02)
        second = prepare_request([{"role":"user","content":"two"}], max_cost_usd=.02)
        output = io.StringIO()
        with patch("sys.stdin.isatty", return_value=True), patch("sys.argv", ["chat"]), patch("builtins.input", side_effect=["one","SEND "+first.sha256[:8],"two","SEND "+second.sha256[:8],"/quit"]), \
             patch.object(chat,"call_api",side_effect=[APIBridgeError("failed"), APIAnswer("ok","gpt-6-luna",1,0,1,0)]) as send, contextlib.redirect_stdout(output):
            chat.main()
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args.args[0], [{"role":"user","content":"two"}])

    def test_pipe_cannot_assert_human_approval(self):
        with patch("sys.argv", ["chat"]), patch("sys.stdin.isatty", return_value=False), patch.object(chat, "call_api") as send, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                chat.main()
            send.assert_not_called()
