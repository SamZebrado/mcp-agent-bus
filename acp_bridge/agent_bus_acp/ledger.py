"""Private metadata-only durable unknown quarantine, not a queue or prompt audit."""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from .config import BridgeError


class Ledger:
    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise BridgeError("ABSOLUTE_STATE_DIRECTORY_REQUIRED")

    def _connect(self):
        # Called only after exact approval; import/preview never creates state.
        self.directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise BridgeError("INVALID_STATE_DIRECTORY")
        if os.name != "nt" and stat.S_IMODE(self.directory.stat().st_mode) & 0o077:
            raise BridgeError("STATE_DIRECTORY_MUST_BE_PRIVATE_0700")
        path = self.directory / "quarantine.sqlite"
        if path.is_symlink():
            raise BridgeError("INVALID_STATE_FILE")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise BridgeError("STATE_FILE_MUST_BE_PRIVATE_0600")
        db = sqlite3.connect(path, timeout=5, isolation_level=None)
        db.execute("CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value BLOB NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS requests (fingerprint TEXT PRIMARY KEY, state TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS counts (scope TEXT PRIMARY KEY, attempts INTEGER NOT NULL)")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('hmac_key', ?)", (secrets.token_bytes(32),))
        return db

    def reserve(self, identity, scope, max_calls):
        db = self._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM settings WHERE name='cleanup_blocked'").fetchone():
                raise BridgeError("PRIOR_CHILD_CLEANUP_UNCONFIRMED; operator reconciliation required")
            key = db.execute("SELECT value FROM settings WHERE name='hmac_key'").fetchone()[0]
            fingerprint = hmac.new(key, identity, hashlib.sha256).hexdigest()
            scope_hash = hmac.new(key, scope, hashlib.sha256).hexdigest()
            if db.execute("SELECT 1 FROM requests WHERE fingerprint=?", (fingerprint,)).fetchone():
                raise BridgeError("PREVIOUS_OUTCOME_UNKNOWN_OR_INFLIGHT; no automatic replay")
            row = db.execute("SELECT attempts FROM counts WHERE scope=?", (scope_hash,)).fetchone()
            if row and row[0] >= max_calls:
                raise BridgeError("OPERATOR_CALL_LIMIT_EXHAUSTED")
            if db.execute("SELECT count(*) FROM requests").fetchone()[0] >= 10000:
                raise BridgeError("QUARANTINE_CAPACITY_EXHAUSTED")
            db.execute("INSERT INTO requests VALUES (?, 'pending')", (fingerprint,))
            db.execute("INSERT INTO counts VALUES (?, 1) ON CONFLICT(scope) DO UPDATE SET attempts=attempts+1", (scope_hash,))
            db.commit()
            return fingerprint
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def resolve(self, fingerprint, known):
        db = self._connect()
        try:
            if known:
                db.execute("DELETE FROM requests WHERE fingerprint=?", (fingerprint,))
            else:
                db.execute("UPDATE requests SET state='unknown' WHERE fingerprint=?", (fingerprint,))
        finally:
            db.close()

    def block_cleanup(self):
        db = self._connect()
        try:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('cleanup_blocked', ?)", (b'1',))
        finally:
            db.close()
