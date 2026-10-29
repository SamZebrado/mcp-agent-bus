"""Static operator profiles and offline approval binding. No agent imports or launch."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re


class BridgeError(Exception):
    """Fixed/sanitized error category, without upstream data or credentials."""


SECRET = re.compile(r"sk-[A-Za-z0-9_-]{16,}|-----BEGIN .*PRIVATE KEY-----|Bearer\s+[A-Za-z0-9._-]{20,}")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise BridgeError("INVALID_LIMIT")
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class Profile:
    name: str
    argv: tuple[str, ...]
    cwd: str
    pass_env: tuple[str, ...] = ()
    timeout_seconds: int = 30
    max_output_bytes: int = 65536
    max_calls: int = 1

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) - {"name", "argv", "cwd", "pass_env", "timeout_seconds", "max_output_bytes", "max_calls"}:
            raise BridgeError("INVALID_PROFILE")
        name = data.get("name")
        argv = data.get("argv")
        cwd = data.get("cwd")
        if not isinstance(name, str) or re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", name) is None:
            raise BridgeError("INVALID_PROFILE_NAME")
        if not isinstance(argv, list) or not 1 <= len(argv) <= 24:
            raise BridgeError("INVALID_ARGV")
        if any(not isinstance(x, str) or not x or len(x) > 4096 or any(ord(c) < 32 for c in x) or SECRET.search(x) for x in argv):
            raise BridgeError("INVALID_ARGV")
        if not Path(argv[0]).is_absolute():
            raise BridgeError("ABSOLUTE_EXECUTABLE_REQUIRED")
        if Path(argv[0]).suffix.lower() in {".bat", ".cmd", ".ps1"}:
            raise BridgeError("SHELL_WRAPPER_NOT_SUPPORTED")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or any(ord(c) < 32 for c in cwd):
            raise BridgeError("ABSOLUTE_WORKSPACE_REQUIRED")
        names = data.get("pass_env", [])
        denied = {"HOME", "USERPROFILE", "PATH", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "LD_PRELOAD", "LD_LIBRARY_PATH", "SYSTEMROOT", "DSH_TELEMETRY_DISABLED", "DO_NOT_TRACK", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"}
        if not isinstance(names, list) or len(names) > 8 or any(not isinstance(x, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", x) is None or x in denied or x.startswith(("DYLD_", "LD_")) for x in names):
            raise BridgeError("INVALID_ENV_ALLOWLIST")
        return cls(name, tuple(argv), cwd, tuple(sorted(set(names))),
                   integer(data.get("timeout_seconds", 30), 1, 120),
                   integer(data.get("max_output_bytes", 65536), 1, 262144),
                   integer(data.get("max_calls", 1), 1, 100))

    @classmethod
    def load(cls, path):
        try:
            if not Path(path).is_absolute():
                raise BridgeError("ABSOLUTE_PROFILE_FILE_REQUIRED")
            raw = Path(path).read_bytes()
            if len(raw) > 32768:
                raise ValueError()
            return cls.from_dict(json.loads(raw))
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise BridgeError("PROFILE_UNREADABLE") from None

    def execution_identity(self):
        # Exclude name/limits so editing a timeout/cap cannot bypass unknown quarantine.
        return {"argv": list(self.argv), "cwd": self.cwd, "pass_env": list(self.pass_env)}

    def identity(self):
        return {**self.execution_identity(), "name": self.name, "timeout_seconds": self.timeout_seconds,
                "max_output_bytes": self.max_output_bytes, "max_calls": self.max_calls}

    def child_env(self, isolated_home):
        env = {"HOME": isolated_home, "USERPROFILE": isolated_home,
               "XDG_CONFIG_HOME": isolated_home, "XDG_CACHE_HOME": isolated_home,
               "PATH": str(Path(self.argv[0]).parent), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
               "DSH_TELEMETRY_DISABLED": "1", "DO_NOT_TRACK": "1"}
        if os.name == "nt":
            env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
        for name in self.pass_env:
            if name in os.environ:
                env[name] = os.environ[name]
        return env


def prepare(profile, text):
    if not isinstance(text, str) or not text.strip() or len(text) > 24000 or SECRET.search(text):
        raise BridgeError("INVALID_OR_SENSITIVE_TEXT")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise BridgeError("INVALID_TEXT_ENCODING") from None
    if len(encoded) > 96000:
        raise BridgeError("INPUT_TOO_LARGE")
    body = {"profile": profile.execution_identity(), "text": text}
    approval = hashlib.sha256(canonical({"profile": profile.identity(), "text": text})).hexdigest()
    return {"request_sha256": approval, "profile": profile.name,
            "profile_sha256": hashlib.sha256(canonical(profile.identity())).hexdigest(),
            "input_utf8_bytes": len(encoded), "timeout_seconds": profile.timeout_seconds,
            "max_output_bytes": profile.max_output_bytes, "max_calls": profile.max_calls,
            "provider_budget_enforced": False, "permissions": "deny all client permission/fs/terminal requests",
            "warning": "ACP is not a sandbox. Child agents may access files/network and incur provider costs independently. Host must enforce explicit human approval."}, canonical(body)
