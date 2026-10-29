"""Run the standalone light suite without touching the original bus tests."""
import pathlib
import runpy

runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1] / "light" / "scripts" / "verify_offline.py"), run_name="__main__")
