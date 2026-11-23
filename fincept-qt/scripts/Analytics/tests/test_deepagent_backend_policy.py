import json
import os
from pathlib import Path
import subprocess
import sys


BACKENDS = Path(__file__).resolve().parents[2] / "agents" / "deepagents" / "backends.py"


def _probe(mode: str, root: Path) -> dict:
    script = r'''
import importlib.util, json, pathlib, sys
path, mode, root = sys.argv[1:]
spec = importlib.util.spec_from_file_location("fincept_deepagent_backends", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
backend = module.get_backend(mode, root, None)
if callable(backend):
    backend = backend(object()).routes["/scripts/"]
try:
    backend._resolve_path("../outside")
    traversal_blocked = False
except ValueError:
    traversal_blocked = True
print(json.dumps({
    "virtual": backend.virtual_mode,
    "traversal_blocked": traversal_blocked,
    "absolute": str(backend._resolve_path("/etc/passwd")),
}))
'''
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-c", script, str(BACKENDS), mode, str(root)],
        cwd=root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return json.loads(completed.stdout)


def _assert_confined(result: dict, root: Path) -> None:
    assert result["virtual"] is True
    assert result["traversal_blocked"] is True
    assert Path(result["absolute"]) == root / "etc" / "passwd"
    assert Path(result["absolute"]) != Path("/etc/passwd")


def test_filesystem_backend_cannot_escape_its_declared_root(tmp_path):
    _assert_confined(_probe("filesystem", tmp_path), tmp_path)


def test_default_composite_scripts_route_uses_the_same_confinement(tmp_path):
    _assert_confined(_probe("composite", tmp_path), tmp_path)
