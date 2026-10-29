"""Cleanup fail-closed regressions: fake SDK/process, real private temp ledger."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_bus_acp.config import BridgeError, Profile
from agent_bus_acp.runner import Runner


class FakeWriter:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1

    async def wait_closed(self):
        pass


class FakeProcess:
    """No OS process exists. An unreaped fixture immediately simulates timeouts."""
    def __init__(self, unreaped=False):
        self.stdin = FakeWriter()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode = None
        self.unreaped = unreaped
        self.terminate_calls = 0
        self.kill_calls = 0

    def terminate(self):
        self.terminate_calls += 1
        if not self.unreaped:
            self.returncode = 0

    def kill(self):
        self.kill_calls += 1
        if not self.unreaped:
            self.returncode = -9

    async def wait(self):
        if self.unreaped:
            raise asyncio.TimeoutError()
        return self.returncode


class FakeConnection:
    def __init__(self, transport, poison_on_close):
        self.transport = transport
        self.poison_on_close = poison_on_close

    async def initialize(self, **kwargs):
        return SimpleNamespace(protocol_version=1,
                               agent_capabilities=SimpleNamespace(session_capabilities=None))

    async def new_session(self, **kwargs):
        return SimpleNamespace(session_id="fake-cleanup-session")

    async def prompt(self, **kwargs):
        return SimpleNamespace(stop_reason="end_turn")

    async def cancel(self, **kwargs):
        pass

    async def close(self):
        if self.poison_on_close:
            # Inject the same callback a protocol failure during cleanup uses.
            self.transport._poison("TEST_CLEANUP_POISON")


class FakeSdk:
    def __init__(self, poison_on_close):
        self.poison_on_close = poison_on_close

    def connect_to_agent(self, handler, transport):
        return FakeConnection(transport, self.poison_on_close)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="acp-cleanup-regression-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = Profile.from_dict({
            "name": "fake", "argv": [sys.executable], "cwd": str(self.root), "max_calls": 5,
        })

    def runner(self, profile=None):
        return Runner(profile or self.profile, self.root / "state", True)

    def arguments(self, runner, text="Synthetic cleanup test only."):
        return {"profile": runner.profile.name, "text": text, "user_approved_agent_run": True,
                "approved_request_sha256": runner.preview(runner.profile.name, text)["request_sha256"]}

    def sdk_result(self, poison_on_close=False):
        return (FakeSdk(poison_on_close), lambda: None,
                lambda **kwargs: None, lambda **kwargs: None)

    async def test_cleanup_poison_is_unknown_and_quarantined_across_runners(self):
        runner = self.runner()
        process = FakeProcess()
        with patch("agent_bus_acp.runner.load_sdk", return_value=self.sdk_result(True)), \
                patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
            with self.assertRaisesRegex(BridgeError, "ACP_CLEANUP_UNCERTAIN"):
                await runner.run(**self.arguments(runner))
            self.assertEqual(runner.status()["state"], "unknown")
            self.assertTrue(runner.status()["local_child_reaped"])
            self.assertFalse(runner.status()["provider_stop_confirmed"])
            self.assertIsNone(runner.active)
            restarted = self.runner()
            with self.assertRaisesRegex(BridgeError, "PREVIOUS_OUTCOME_UNKNOWN_OR_INFLIGHT"):
                await restarted.run(**self.arguments(restarted))
            self.assertEqual(spawn.call_count, 1)

    async def test_unreaped_child_disables_runner_and_persists_global_block(self):
        runner = self.runner()
        process = FakeProcess(unreaped=True)
        with patch("agent_bus_acp.runner.load_sdk", return_value=self.sdk_result()), \
                patch("asyncio.create_subprocess_exec", return_value=process) as spawn:
            with self.assertRaisesRegex(BridgeError, "ACP_CLEANUP_UNCERTAIN"):
                await runner.run(**self.arguments(runner))
            self.assertFalse(runner.enabled)
            self.assertEqual(runner.status()["state"], "unknown")
            self.assertFalse(runner.status()["local_child_reaped"])
            self.assertFalse(runner.status()["provider_stop_confirmed"])
            self.assertEqual(process.terminate_calls, 1)
            self.assertEqual(process.kill_calls, 1)
            with self.assertRaisesRegex(BridgeError, "AGENT_PROCESS_DISABLED"):
                await runner.run(**self.arguments(runner, "Different synthetic request."))

            # Different request AND execution identity: prove this is a durable
            # global cleanup block, not just the original request fingerprint.
            other = Profile.from_dict({"name": "another", "argv": [sys.executable, "-I"],
                                       "cwd": str(self.root), "max_calls": 9})
            restarted = self.runner(other)
            self.assertTrue(restarted.enabled)
            with self.assertRaisesRegex(BridgeError, "PRIOR_CHILD_CLEANUP_UNCONFIRMED"):
                await restarted.run(**self.arguments(restarted, "Different synthetic request."))
            self.assertEqual(spawn.call_count, 1)


if __name__ == "__main__":
    unittest.main()
