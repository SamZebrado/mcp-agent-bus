"""Synthetic input/privacy regressions; never inspect user history or credentials."""
import contextlib
import getpass
import io
import os
import sys
import types
import unittest
import warnings
from unittest.mock import patch
from mcp_agent_bus_light import chat
from mcp_agent_bus_light.policy import APIBridgeError

FAKE = 'unit-test-not-a-valid-secret'


class InputPrivacyTests(unittest.TestCase):
    def test_provider_confirmation_is_hidden_and_bad_paste_is_not_reprinted(self):
        output = io.StringIO()
        with patch('sys.stdin.isatty', return_value=True), \
                patch.object(chat.getpass, 'getpass', return_value=FAKE) as read, \
                patch('builtins.input', side_effect=AssertionError('visible input')), \
                patch.object(chat, 'call_api') as send, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                self.assertRaises(SystemExit):
            chat.main(['--ask-key'])
        self.assertEqual(read.call_count, 1)
        self.assertNotIn(FAKE, output.getvalue())
        self.assertIn('Provider selection canceled', output.getvalue())
        self.assertIn('https://api.openai.com/v1/responses', output.getvalue())
        send.assert_not_called()

    def test_control_c_at_provider_or_key_has_fixed_text_no_send_or_environment_change(self):
        for reads in ([KeyboardInterrupt], ['OPENAI', KeyboardInterrupt], [EOFError], ['OPENAI', EOFError]):
            output = io.StringIO()
            with patch.dict(os.environ, {'DUAL_LANE_ENABLE_API':'0', 'DUAL_LANE_API_KEY':'synthetic-old'}, clear=True), \
                    patch('sys.stdin.isatty', return_value=True), \
                    patch.object(chat.getpass, 'getpass', side_effect=reads), \
                    patch('builtins.input', side_effect=AssertionError('visible input')), \
                    patch.object(chat, 'call_api') as send, \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                before = dict(os.environ)
                chat.main(['--ask-key'])
                self.assertEqual(dict(os.environ), before)
            self.assertIn('Canceled; credentials not saved.', output.getvalue())
            self.assertNotIn('Traceback', output.getvalue())
            send.assert_not_called()

    def test_hidden_input_warning_stops_before_echo_fallback(self):
        def warning(_):
            warnings.warn('Synthetic missing console', getpass.GetPassWarning)
            self.fail('Visible fallback reached')
        with patch.object(chat.getpass, 'getpass', side_effect=warning), \
                self.assertRaisesRegex(APIBridgeError, 'refusing echo fallback'):
            chat._hidden_input('Hidden confirmation: ')

    def test_windows_queued_paste_is_drained_on_return_and_interrupt(self):
        for result in (FAKE, KeyboardInterrupt()):
            api = types.SimpleNamespace(kbhit=unittest.mock.Mock(side_effect=[True, True, False]),
                                        getwch=unittest.mock.Mock(side_effect=['x', '\n']))
            options = {'side_effect':result} if isinstance(result, BaseException) else {'return_value':result}
            with patch.object(chat.os, 'name', 'nt'), patch.dict(sys.modules, {'msvcrt':api}), \
                    patch.object(chat.getpass, 'getpass', **options):
                if isinstance(result, BaseException):
                    with self.assertRaises(KeyboardInterrupt):chat._hidden_input('Hidden: ')
                else:self.assertEqual(chat._hidden_input('Hidden: '), FAKE)
            self.assertEqual(api.getwch.call_count, 2)

    def test_terminal_failures_are_sanitized(self):
        for error in (OSError(FAKE), UnicodeError(FAKE)):
            with patch.object(chat.getpass, 'getpass', side_effect=error), self.assertRaises(APIBridgeError) as got:
                chat._hidden_input('Hidden: ')
            self.assertNotIn(FAKE, str(got.exception))
        api = types.SimpleNamespace(kbhit=unittest.mock.Mock(side_effect=OSError(FAKE)))
        with patch.object(chat.os, 'name', 'nt'), patch.dict(sys.modules, {'msvcrt':api}), \
                patch.object(chat.getpass, 'getpass', return_value=FAKE), self.assertRaises(APIBridgeError) as got:
            chat._hidden_input('Hidden: ')
        self.assertNotIn(FAKE, str(got.exception))

    def test_getpass_does_not_use_imported_readline_history(self):
        # The bridge never calls history APIs, even if a caller has imported a
        # readline-shaped module. Real tty behavior is a separate PTY test.
        api = types.SimpleNamespace(add_history=unittest.mock.Mock(side_effect=AssertionError('history')),
                                    write_history_file=unittest.mock.Mock(side_effect=AssertionError('disk history')))
        with patch.dict(sys.modules, {'readline':api}), patch.object(chat.getpass, 'getpass', return_value='OPENAI'):
            self.assertEqual(chat._hidden_input('Hidden: '), 'OPENAI')
        api.add_history.assert_not_called();api.write_history_file.assert_not_called()
