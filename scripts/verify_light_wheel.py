"""Offline wheel/install regression for the additive light package.

Builds in a private source copy, installs into a new venv from that wheel only,
and runs installed imports/entry points outside the source tree. Probes forbid
SQLite access, child processes, sockets, and filesystem mutations via Python
audit hooks. No API key, network installation, or system package change occurs.
Python 3.10+; building requires pip, setuptools and wheel already installed.
With --standalone, copies only light/ and proves it runs and installs alone.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LIGHT_ROOT = ROOT / "light"
EXPECTED_SCRIPTS = {
    "mcp-agent-bus": "mcp_agent_bus.cli:main",
    "mcp-agent-bus-dashboard": "mcp_agent_bus.dashboard:main",
    "mcp-agent-bus-light": "mcp_agent_bus_light.cli:main",
}
GUARD = r'''
import os
import sys

def _no_side_effects(event, args):
    if event == "sqlite3.connect" or event.startswith("socket.") or event.startswith("subprocess."):
        raise RuntimeError("Forbidden side effect during installed probe: " + event)
    if event in {"os.system", "os.posix_spawn", "os.fork", "os.forkpty", "os.exec", "os.spawn",
                 "os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink", "os.truncate"}:
        raise RuntimeError("Forbidden side effect during installed probe: " + event)
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if (isinstance(mode, str) and any(x in mode for x in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            raise RuntimeError("Forbidden file mutation during installed probe")

sys.addaudithook(_no_side_effects)
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(evidence: Path, standalone=False):
    evidence.mkdir(parents=True, exist_ok=True)
    if any(evidence.iterdir()):
        raise ValueError("Use a new empty evidence directory; prior results are preserved.")
    work = Path(tempfile.mkdtemp(prefix="bus-light-wheel-"))
    home, probe_cwd, guard = (work / x for x in ("home", "probe-cwd", "guard"))
    for p in (home, probe_cwd, guard):
        p.mkdir(mode=0o700)
    env = {
        "HOME": str(home), "USERPROFILE": str(home),
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8",
        "PIP_NO_INDEX": "1", "PIP_CONFIG_FILE": os.devnull, "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
    }
    if os.name == "nt":
        env["SystemRoot"] = os.environ.get("SystemRoot", r"C:\Windows")
    source_root = LIGHT_ROOT if standalone else ROOT
    expected_scripts = ({"mcp-agent-bus-light": EXPECTED_SCRIPTS["mcp-agent-bus-light"]}
                        if standalone else EXPECTED_SCRIPTS)
    distribution = "mcp-agent-bus-light" if standalone else "mcp-agent-bus"
    report = {"status": "running", "python": sys.version, "temporary_work": str(work),
              "mode": "standalone-light" if standalone else "combined-root",
              "copied_source": str(source_root),
              "environment": "new HOME; allowlisted environment without API keys; pip no-index/no-deps",
              "runtime_network_calls": 0, "probes": []}
    original_paths = sorted([*ROOT.glob("mcp_agent_bus/*.py"), *ROOT.glob("tests/*.py"), ROOT / "run_smoke.sh"])
    original_hashes = {str(p.relative_to(ROOT)): sha(p) for p in original_paths}

    def command(label, argv, *, cwd=probe_cwd, environment=env, expected=0, stdin=None):
        result = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True,
                                text=True, encoding="utf-8", timeout=90, input=stdin)
        (evidence / (label + ".stdout.txt")).write_text(result.stdout, encoding="utf-8")
        (evidence / (label + ".stderr.txt")).write_text(result.stderr, encoding="utf-8")
        report["probes"].append({"name": label, "returncode": result.returncode,
                                 "expected_returncode": expected})
        if result.returncode != expected:
            raise AssertionError(label + " returned " + str(result.returncode) + ": " + result.stderr[-2000:])
        return result

    try:
        report["build_tools"] = {name: importlib.metadata.version(name) for name in ("pip", "setuptools", "wheel")}
        stage = work / "source-copy"
        shutil.copytree(source_root, stage, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "build", "dist", "*.egg-info"))
        package_roots = {"mcp_agent_bus_light": stage / "mcp_agent_bus_light" if standalone
                         else stage / "light" / "mcp_agent_bus_light"}
        if not standalone:
            package_roots["mcp_agent_bus"] = stage / "mcp_agent_bus"
        else:
            assert not (stage / "mcp_agent_bus").exists()
            assert not (stage.parent / "mcp_agent_bus").exists()
            report["standalone_copy_contains_no_parent_bus"] = True
        wheel_dir = evidence / "dist"
        wheel_dir.mkdir()
        command("wheel-build", [sys.executable, "-m", "pip", "wheel", "--no-index", "--no-deps",
                "--no-build-isolation", "--wheel-dir", str(wheel_dir), str(stage)])
        wheels = list(wheel_dir.glob("*.whl"))
        assert len(wheels) == 1, wheels
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as z:
            members = sorted(z.namelist())
            assert ("mcp_agent_bus/__init__.py" in members) is (not standalone)
            if standalone:
                assert not any(x.startswith("mcp_agent_bus/") for x in members)
            assert "mcp_agent_bus_light/__init__.py" in members
            assert not any(x.startswith("dual_lane/") for x in members)
            source_hashes = {name + "/" + p.relative_to(directory).as_posix(): sha(p)
                             for name, directory in package_roots.items()
                             for p in sorted(directory.rglob("*.py"))}
            assert source_hashes and "mcp_agent_bus_light/__init__.py" in source_hashes
            for name, digest in source_hashes.items():
                assert name in members, "Package discovery omitted " + name
                assert hashlib.sha256(z.read(name)).hexdigest() == digest, "Wheel source mismatch: " + name
            entries = next(x for x in members if x.endswith(".dist-info/entry_points.txt"))
            config = configparser.ConfigParser()
            config.read_string(z.read(entries).decode())
            actual_scripts = dict(config["console_scripts"])
            assert actual_scripts == expected_scripts, actual_scripts
            metadata_path = next(x for x in members if x.endswith(".dist-info/METADATA"))
            metadata = z.read(metadata_path).decode()
            assert "Requires-Python: >=3.10" in metadata, metadata
            assert "Requires-Dist:" not in metadata, "Runtime dependency added"
            modules = [x[:-3].replace("/", ".") for x in members
                       if x.endswith(".py") and (x.startswith("mcp_agent_bus/") or x.startswith("mcp_agent_bus_light/"))
                       and not x.endswith("/__main__.py")]
            modules = [x.removesuffix(".__init__") for x in modules]
        report.update(wheel=wheel.name, wheel_sha256=sha(wheel), wheel_bytes=wheel.stat().st_size,
                      wheel_members=members, console_scripts=actual_scripts,
                      imported_modules=modules, runtime_dependencies=[], packaged_source_sha256=source_hashes,
                      pyproject_sha256=sha(stage / "pyproject.toml"))
        target_venv = work / "venv"
        # ensurepip uses only the Python distribution's bundled wheel, not PyPI.
        venv.EnvBuilder(with_pip=False, clear=False).create(target_venv)
        bin_dir = target_venv / ("Scripts" if os.name == "nt" else "bin")
        python = bin_dir / ("python.exe" if os.name == "nt" else "python")
        command("venv-ensurepip", [str(python), "-Im", "ensurepip", "--upgrade", "--default-pip"])
        command("wheel-install", [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)])
        command("installed-metadata", [str(python), "-c",
            "import importlib.metadata as m,json;d=m.distribution(" + repr(distribution) + ");"
            "print(json.dumps({'version':d.version,'requires':d.requires or [],'scripts':{e.name:e.value for e in d.entry_points}}))"])
        (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
        guarded_env = {**env, "PYTHONPATH": str(guard)}
        probe = ("import importlib,json,pathlib; expected=pathlib.Path(" + repr(str(target_venv)) + ").resolve(); found={};"
                 "\nfor name in " + repr(modules) + ":\n"
                 " m=importlib.import_module(name); p=pathlib.Path(m.__file__).resolve();"
                 " assert p.is_relative_to(expected),(name,str(p)); found[name]=str(p)\n"
                 "print(json.dumps(found,indent=2))")
        command("installed-imports-no-side-effects", [str(python), "-c", probe], environment=guarded_env)
        if standalone:
            command("standalone-no-bus-installed", [str(python), "-c",
                    "import importlib.util;assert importlib.util.find_spec('mcp_agent_bus') is None"],
                    environment=guarded_env)
        for name in expected_scripts:
            executable = bin_dir / (name + (".exe" if os.name == "nt" else ""))
            assert executable.exists(), executable
            command(name + "-help", [str(executable), "--help"], environment=guarded_env)
        if not standalone:
            command("legacy-module-help", [str(python), "-m", "mcp_agent_bus", "--help"], environment=guarded_env)
        command("light-module-help", [str(python), "-m", "mcp_agent_bus_light", "--help"], environment=guarded_env)
        light = bin_dir / ("mcp-agent-bus-light.exe" if os.name == "nt" else "mcp-agent-bus-light")
        capability = command("light-capabilities", [str(light), "capabilities"], environment=guarded_env)
        report["capabilities"] = json.loads(capability.stdout)
        assert report["capabilities"]["default_backend"] == "responses"
        assert report["capabilities"]["harness_acp"]["implemented"] is False
        assert report["capabilities"]["harness_acp"]["enabled"] is False
        for mode in ("serve", "chat", "configure", "capabilities"):
            command("light-" + mode + "-help", [str(light), mode, "--help"], environment=guarded_env)
        unavailable = command("light-harness-fail-closed", [str(light), "--backend", "harness-acp", "serve"],
                              environment=guarded_env, expected=2)
        assert "HARNESS_UNAVAILABLE" in unavailable.stderr
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "api_preview", "arguments": {
                "question": "Offline installed-wheel probe.", "model": "gpt-6-luna", "max_output_tokens": 64, "max_cost_usd": .01}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "api_second_opinion", "arguments": {
                "question": "Offline installed-wheel probe.", "user_approved_api_send": False, "max_cost_usd": .01}}},
        ]
        def stdio_check(label, argv, cwd=probe_cwd):
            served = command(label, argv, environment=guarded_env, cwd=cwd,
                             stdin="".join(json.dumps(x) + "\n" for x in requests))
            replies = {r["id"]: r for r in map(json.loads, served.stdout.splitlines())}
            assert replies[1]["result"]["serverInfo"]["name"] == "mcp-agent-bus-light"
            assert {x["name"] for x in replies[2]["result"]["tools"]} == {"api_preview", "api_second_opinion"}
            assert replies[3]["result"]["isError"] is False
            assert replies[4]["result"]["isError"] is True
        stdio_check("installed-light-stdio-no-side-effects", [str(light), "serve"])
        report["installed_stdio_preview_and_approval_block_passed"] = True
        if standalone:
            # A second fresh venv has no package installed. Cwd contains only the
            # copied light directory, so source execution cannot fall back to the
            # parent package or the installed wheel that was tested above.
            source_venv = work / "source-only-venv"
            venv.EnvBuilder(with_pip=False, clear=False).create(source_venv)
            source_python = source_venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            command("standalone-source-imports-no-parent", [str(source_python), "-c",
                    "import importlib.util,pathlib,mcp_agent_bus_light;"
                    "assert importlib.util.find_spec('mcp_agent_bus') is None;"
                    "assert pathlib.Path(mcp_agent_bus_light.__file__).resolve().is_relative_to(" +
                    repr(str(stage)) + ");print(mcp_agent_bus_light.__file__)"],
                    cwd=stage, environment=guarded_env)
            command("standalone-source-module-help", [str(source_python), "-m", "mcp_agent_bus_light", "--help"],
                    cwd=stage, environment=guarded_env)
            direct_capability = command("standalone-source-capabilities", [str(source_python), "-m", "mcp_agent_bus_light", "capabilities"],
                                        cwd=stage, environment=guarded_env)
            assert json.loads(direct_capability.stdout) == report["capabilities"]
            stdio_check("standalone-source-stdio-no-side-effects", [str(source_python), "-m", "mcp_agent_bus_light", "serve"], stage)
            command("standalone-copied-offline-tests", [str(source_python), str(stage / "scripts" / "verify_offline.py")],
                    cwd=stage, environment=env)
            report["standalone_source_only_passed"] = True
            report["standalone_copied_offline_tests_passed"] = True
        report["probe_directory_empty"] = not any(probe_cwd.iterdir())
        assert report["probe_directory_empty"], "Installed help/import/capabilities created local artifacts"
        report["installed_location"] = str(target_venv)
        assert original_hashes == {str(p.relative_to(ROOT)): sha(p) for p in original_paths}, "Original bus source/tests changed"
        report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        raise
    finally:
        report["original_files_unchanged"] = original_hashes == {str(p.relative_to(ROOT)): sha(p) for p in original_paths}
        report["original_source_sha256"] = original_hashes
        (evidence / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("wheel_members", "original_source_sha256")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--standalone", action="store_true", help="Copy and install only the self-contained light directory")
    args = parser.parse_args()
    run(args.evidence_dir.resolve(), standalone=args.standalone)
