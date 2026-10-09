"""Install a private, non-editable app snapshot. Do not start or publish it."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deployment"))
from manage import LABELS, loaded, private_write, require_private, runtime_root


def install(root: Path, repo: Path = REPO):
    if any(loaded(job) for job in LABELS):
        raise ValueError("Stop Trip Split before installing or updating it.")
    if root.exists() or root.is_symlink():
        require_private(root, directory=True)
    else:
        root.mkdir(parents=True, mode=0o700)
    for directory in ("state", "logs", "jobs", "venv"):
        path = root / directory
        if path.exists() or path.is_symlink():
            require_private(path, directory=True)
        else:
            path.mkdir(mode=0o700)
    settings = root / "settings.json"
    if not settings.exists():
        private_write(settings, b'{"hostname": "split.boboji.fyi", "port": 8000}\n')
    else:
        require_private(settings)
    venv.EnvBuilder(with_pip=True).create(root / "venv")
    python = root / "venv" / "bin" / "python"
    # Building a wheel avoids an editable install that changes with the checkout.
    with tempfile.TemporaryDirectory(prefix="trip-split-wheel-") as temporary:
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", temporary,
                        str(repo)], check=True)
        wheels = list(Path(temporary).glob("trip_dollar-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("The build did not produce exactly one app wheel.")
        wheel = wheels[0]
        fingerprint = hashlib.sha256(wheel.read_bytes()).hexdigest()
        subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "--force-reinstall",
                        f"{wheel}[web]"], check=True)
    for name in ("manage.py", "serve.py"):
        private_write(root / name, (repo / "deployment" / name).read_bytes())
    subprocess.run([str(python), str(root / "serve.py"), "--check"], check=True)
    version = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                           capture_output=True, text=True, check=True).stdout.strip()
    dependencies = subprocess.run([str(python), "-m", "pip", "freeze"],
                                  capture_output=True, text=True, check=True).stdout.splitlines()
    private_write(root / "installation.json", json.dumps({
        "source_commit": version, "uncommitted_changes": bool(dirty),
        "wheel_sha256": fingerprint, "dependencies": dependencies,
    }, indent=2).encode() + b"\n")
    print(f"Installed privately at {root}")
    if dirty:
        print("Note: this installation includes uncommitted checkout changes.")
    print("Nothing was started. No login item, boot service or DNS record was created.")


def main():
    if sys.platform != "darwin" or os.getuid() == 0 or sys.version_info < (3, 11):
        raise SystemExit("Use Python 3.11+ on macOS as your normal user, without sudo.")
    os.umask(0o077)
    try:
        install(runtime_root())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Installation failed: {exc}") from exc


if __name__ == "__main__":
    main()
