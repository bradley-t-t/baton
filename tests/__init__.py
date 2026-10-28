"""Run from the repo root with `python3 -m unittest`; the package is imported from src/."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Keep every test away from the real ~/.config/baton and ~/.local/state/baton, and
# never call a judge model.
os.environ["BATON_STATE"] = tempfile.mkdtemp(prefix="baton-test-state-")
os.environ["BATON_CONFIG"] = str(Path(tempfile.mkdtemp(prefix="baton-test-cfg-")) / "config.json")
os.environ["BATON_JUDGE"] = "0"
