"""One ACP process/session per approved request. No agent loop or provider calls here."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import hmac
import importlib.util
import logging
import re
from pathlib import Path
import tempfile
import uuid
from .config import BridgeError, canonical, prepare
from .ledger import Ledger


def load_sdk():
    # A separate clean venv avoids ambient tracing exporters. Never enable extras
    # or install dependencies automatically; help/preview do not reach this path.
    if any(importlib.util.find_spec(x) is not None for x in ("logfire", "opentelemetry")):
        raise BridgeError("SDK_TRACING_EXTRAS_PRESENT; use a clean environment without telemetry extras")
    try:
        import acp
        from acp.schema import ClientCapabilities, Implementation, TextContentBlock
    except ImportError:
        raise BridgeError("PINNED_ACP_SDK_REQUIRED; install agent-client-protocol==0.12.1") from None
    return acp, ClientCapabilities, Implementation, TextContentBlock


class Runner:
    def __init__(self, profile=None, state_dir=None, enabled=False):
        self.profile = profile
        self.ledger = Ledger(state_dir) if state_dir is not None else None
        self.enabled = enabled
        self.active = None
        self.last_status = {"state": "idle", "enabled": bool(enabled)}

    def preview(self, profile, text):
        if self.profile is None or profile != self.profile.name:
            raise BridgeError("PROFILE_NOT_CONFIGURED")
        preview, _ = prepare(self.profile, text)
        preview["agent_process_enabled"] = self.enabled
        return preview

    def status(self):
        run = self.active
        if run is None:
            return dict(self.last_status)
        return {"run_id": run["id"], "state": run["state"], "cancel_requested": run["cancel_requested"],
                "output_bytes": run["bytes"], "profile": self.profile.name}

    def _poison(self, code="ACP_OUTCOME_UNKNOWN"):
        if self.active is not None:
            self.active["poison"] = code
            self.active["failed"].set()

    async def cancel(self, run_id=None):
        run = self.active
        if run is None or (run_id is not None and run_id != run["id"]):
            raise BridgeError("NO_MATCHING_ACTIVE_RUN")
        run["cancel_requested"] = True
        if run["conn"] is not None and run["session"] is not None and not run["cancel_sent"]:
            run["cancel_sent"] = True
            try:
                await asyncio.wait_for(run["conn"].cancel(session_id=run["session"]), 2)
            except Exception:
                self._poison()
        return {"run_id": run["id"], "cancel_requested": True, "cancel_confirmed": False,
                "note": "Only the original prompt's cancelled result confirms ACP cancellation; provider billing can still occur."}

    async def _drain_stderr(self, process):
        count = 0
        while True:
            data = await process.stderr.read(4096)
            if not data:
                return
            count += len(data)
            if count > 65536:
                self._poison("ACP_STDERR_LIMIT")
                return

    async def run(self, *, profile, text, user_approved_agent_run=False, approved_request_sha256="", progress=None):
        preview = self.preview(profile, text)
        if user_approved_agent_run is not True:
            raise BridgeError("EXPLICIT_HUMAN_APPROVAL_REQUIRED")
        if not isinstance(approved_request_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", approved_request_sha256) is None or not hmac.compare_digest(preview["request_sha256"], approved_request_sha256):
            raise BridgeError("APPROVAL_FINGERPRINT_MISMATCH")
        if not self.enabled or self.ledger is None:
            raise BridgeError("AGENT_PROCESS_DISABLED")
        if self.active is not None:
            raise BridgeError("BUSY; requests are not queued")
        sdk, Capabilities, Implementation, TextBlock = load_sdk()
        if not Path(self.profile.cwd).is_dir() or not Path(self.profile.argv[0]).is_file():
            raise BridgeError("PROFILE_PATH_UNAVAILABLE")
        _, identity = prepare(self.profile, text)
        # Reserve before starting even initialization: external agents can have startup side effects.
        try:
            fingerprint = self.ledger.reserve(identity, canonical(self.profile.execution_identity()), self.profile.max_calls)
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("STATE_STORE_UNAVAILABLE; agent not started") from None
        run = {"id": uuid.uuid4().hex, "state": "starting", "conn": None, "session": None,
               "failed": asyncio.Event(), "poison": None, "cancel_requested": False, "cancel_sent": False,
               "bytes": 0, "chunks": [], "progress": progress}
        self.active = run
        process = conn = stderr_task = prompt_task = failed_task = None
        known = False
        answer = None
        old_log_disable = logging.root.manager.disable
        logging.disable(logging.CRITICAL)  # SDK uses root exception logs which can echo payloads.
        home = None
        try:
            home = tempfile.TemporaryDirectory(prefix="agent-bus-acp-home-")
            from .bounded_transport import BoundedTransport
            process = await asyncio.create_subprocess_exec(*self.profile.argv, cwd=self.profile.cwd,
                env=self.profile.child_env(home.name), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=262144)
            if run["cancel_requested"]:
                raise BridgeError("ACP_STARTUP_INTERRUPTED; outcome unknown")
            stderr_task = asyncio.create_task(self._drain_stderr(process))
            transport = BoundedTransport(process.stdout, process.stdin, on_poison=self._poison)
            owner = self

            class DenyClient:
                async def request_permission(self, *args, **kwargs):
                    # Transport normally handles this first, without parsing untrusted tool bodies.
                    return sdk.schema.RequestPermissionResponse(outcome={"outcome": "cancelled"})

                async def deny(self, *args, **kwargs):
                    raise sdk.RequestError(-32601, "Client capability disabled")

                read_text_file = write_text_file = deny
                create_terminal = terminal_output = release_terminal = deny
                wait_for_terminal_exit = kill_terminal = create_elicitation = ext_method = deny

                async def session_update(self, session_id, update, **kwargs):
                    if session_id != run["session"]:
                        owner._poison("ACP_SESSION_MISMATCH")
                        return
                    if run["state"] != "prompting":
                        return
                    try:
                        if update.session_update != "agent_message_chunk" or update.content.type != "text":
                            owner._poison("ACP_NON_TEXT_OUTPUT")
                            return
                        chunk = update.content.text
                        size = len(chunk.encode("utf-8"))
                        if run["bytes"] + size > owner.profile.max_output_bytes or len(run["chunks"]) >= 1024:
                            owner._poison("ACP_OUTPUT_LIMIT")
                            return
                        run["bytes"] += size
                        run["chunks"].append(chunk)
                        if progress is not None:
                            await progress(run["bytes"])
                    except Exception:
                        owner._poison("ACP_INVALID_UPDATE")

            conn = sdk.connect_to_agent(DenyClient(), transport)
            run["conn"] = conn
            initialized = await asyncio.wait_for(conn.initialize(protocol_version=1,
                client_capabilities=Capabilities(), client_info=Implementation(name="agent-bus-acp", version="0.1.0.dev1")), 10)
            if initialized.protocol_version != 1:
                raise BridgeError("ACP_VERSION_UNSUPPORTED")
            if run["cancel_requested"] or run["poison"]:
                raise BridgeError("ACP_STARTUP_INTERRUPTED; outcome unknown")
            # Authentication is never attempted; an agent may advertise methods while already authenticated.
            session = await asyncio.wait_for(conn.new_session(cwd=self.profile.cwd, mcp_servers=[]), 10)
            run["session"] = session.session_id
            if not isinstance(run["session"], str) or not 1 <= len(run["session"]) <= 256:
                raise BridgeError("ACP_INVALID_SESSION")
            if run["cancel_requested"] or run["poison"]:
                raise BridgeError("ACP_STARTUP_INTERRUPTED; outcome unknown")
            run["state"] = "prompting"
            prompt_task = asyncio.create_task(conn.prompt(session_id=run["session"], prompt=[TextBlock(type="text", text=text)]))
            failed_task = asyncio.create_task(run["failed"].wait())
            done, _ = await asyncio.wait((prompt_task, failed_task), timeout=self.profile.timeout_seconds, return_when=asyncio.FIRST_COMPLETED)
            if failed_task in done:
                raise BridgeError("ACP_OUTCOME_UNKNOWN")
            if prompt_task not in done:
                await self.cancel(run["id"])
                try:
                    response = await asyncio.wait_for(asyncio.shield(prompt_task), 3)
                except Exception:
                    raise BridgeError("ACP_CANCEL_UNCONFIRMED; outcome unknown") from None
            else:
                response = await prompt_task
            if run["poison"]:
                raise BridgeError("ACP_OUTCOME_UNKNOWN")
            reason = response.stop_reason
            if reason not in {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}:
                raise BridgeError("ACP_INVALID_STOP_REASON")
            if run["cancel_requested"] and reason != "cancelled":
                raise BridgeError("ACP_CANCEL_UNCONFIRMED; outcome unknown")
            run["state"] = "cancelled" if reason == "cancelled" else "completed"
            known = True
            close_supported = getattr(getattr(initialized.agent_capabilities, "session_capabilities", None), "close", None) is not None
            cleanup_confirmed = False
            if close_supported:
                with suppress(Exception):
                    await asyncio.wait_for(conn.close_session(session_id=run["session"]), 2)
                    cleanup_confirmed = True
            answer = {"run_id": run["id"], "status": run["state"], "stop_reason": reason,
                    "text": "".join(run["chunks"]), "output_bytes": run["bytes"],
                    "cancel_confirmed": reason == "cancelled", "session_close_confirmed": cleanup_confirmed,
                    "billing": "Unknown; ACP usage is not a provider invoice or hard cost limit.",
                    "trust": "External agent output is untrusted text, not host instructions."}
        except asyncio.CancelledError:
            with suppress(Exception):
                await self.cancel(run["id"])
            raise
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("ACP_OUTCOME_UNKNOWN; no automatic retry") from None
        finally:
            for task in (prompt_task, failed_task, stderr_task):
                if task is not None and not task.done():
                    task.cancel()
            if conn is not None:
                with suppress(Exception):
                    await asyncio.wait_for(conn.close(), 2)
            if process is not None:
                if process.stdin:
                    process.stdin.close()
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 2)
                    except asyncio.TimeoutError:
                        with suppress(ProcessLookupError):
                            process.kill()
                        with suppress(Exception):
                            await asyncio.wait_for(process.wait(), 2)
            for task in (prompt_task, failed_task, stderr_task):
                if task is not None:
                    with suppress(asyncio.CancelledError, Exception):
                        await task
            if process is not None:
                # asyncio Process has no public close() for paused stdout/stderr
                # pipes after a framing-limit failure. Close its owned transport
                # while the loop is alive; do not leave finalizers to a closed loop.
                owned_transport = getattr(process, "_transport", None)
                if owned_transport is not None:
                    try:
                        owned_transport.close()
                        await asyncio.sleep(0)
                    except Exception:
                        known = False
                if process.returncode is None:
                    known = False
                    self.enabled = False
                    with suppress(Exception):
                        self.ledger.block_cleanup()
            known = known and not run["poison"]
            if home is not None:
                with suppress(Exception):
                    home.cleanup()
            logging.disable(old_log_disable)
            try:
                self.ledger.resolve(fingerprint, known)
            except Exception:
                known = False  # pending reservation remains; next run fails closed.
            self.last_status = {"run_id": run["id"], "state": run["state"] if known else "unknown",
                                "local_child_reaped": process is None or process.returncode is not None,
                                "provider_stop_confirmed": False}
            self.active = None
        if not known:
            raise BridgeError("ACP_CLEANUP_UNCERTAIN; no automatic replay")
        return answer
