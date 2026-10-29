"""Run synthetic local subprocess/SDK tests; no real agent/model/API credentials."""
from pathlib import Path
import sys
import unittest

def network_guard(event, args):
    if event == "socket.connect" and len(args) > 1:
        address = args[1]
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError("External network is forbidden in offline ACP tests")

sys.addaudithook(network_guard)
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(root/'tests')))
raise SystemExit(not result.wasSuccessful())
