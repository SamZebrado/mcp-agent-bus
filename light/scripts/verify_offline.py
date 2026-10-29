"""Run only light tests with Python socket creation disabled; no API expense."""
import pathlib
import socket
import sys
import unittest
from unittest.mock import patch

root = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests"))
with patch.object(socket, "socket", side_effect=AssertionError("Network forbidden in light offline tests")):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py"))
sys.exit(not result.wasSuccessful())
