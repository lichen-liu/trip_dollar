"""Explicit stop/install/start/check; preserve private settings and credentials."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

from manage import check, runtime_root, start, stop

REPO = Path(__file__).resolve().parents[1]


def deploy(root: Path, watch_runtime: Path | None = None):
    subprocess.run(["git", "submodule", "update", "--init", "vendor/bobowatch"],
                   cwd=REPO, check=True)
    spec = importlib.util.spec_from_file_location("trip_split_installer", REPO / "installation/install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    stop()
    installer.install(root, REPO)
    try:
        start(root, watch_runtime=watch_runtime)
        check(root)
    except BaseException as error:
        try:
            stop()
        except Exception as cleanup_error:
            raise RuntimeError(f"Deployment failed; cleanup incomplete: {cleanup_error}") from error
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["deploy"])
    parser.add_argument("--bobowatch-runtime-dir", type=Path)
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.getuid() == 0 or sys.version_info < (3, 11):
        parser.error("Use Python 3.11+ on macOS as your normal user, without sudo")
    os.umask(0o077)
    try:
        deploy(runtime_root(), args.bobowatch_runtime_dir)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, "Deployment failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
