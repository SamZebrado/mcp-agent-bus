from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mcp_agent_bus.bus import AgentBus


class CliTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        return subprocess.run(
            [sys.executable, "-m", "mcp_agent_bus.cli", *args],
            text=True,
            capture_output=True,
            env=env,
            timeout=10,
        )

    def test_doctor_cli_does_not_create_missing_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"
            result = self._run("--data-dir", str(missing), "doctor")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "FAIL")
            self.assertFalse(missing.exists())

    def test_doctor_cli_passes_initialized_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bus = AgentBus(Path(tmp))
            bus.register_agent("planner", "planner")
            bus.close()
            result = self._run("--data-dir", tmp, "doctor")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["authoritative_store"], "sqlite")


if __name__ == "__main__":
    unittest.main()
