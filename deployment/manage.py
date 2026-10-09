"""Manually load/unload two user-session launchd jobs, never login items."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import plistlib
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

from serve import read_settings

LABELS = {"server": "fyi.boboji.trip-split.server", "tunnel": "fyi.boboji.trip-split.tunnel"}


def runtime_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "Trip Split"


def require_private(path: Path, *, directory: bool = False):
    info = path.lstat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError(f"{path.name} must belong to you and be accessible only by you.")


def private_write(path: Path, content: bytes):
    """Replace an owned runtime file atomically; never follow a target symlink."""
    if path.exists() or path.is_symlink():
        require_private(path)
    handle, temporary = tempfile.mkstemp(prefix=".trip-split-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=20)


def target(job: str) -> str:
    return f"gui/{os.getuid()}/{LABELS[job]}"


def loaded(job: str) -> bool:
    return launchctl("print", target(job)).returncode == 0


def job_definitions(root: Path, cloudflared: str) -> dict:
    common = {"KeepAlive": True, "ThrottleInterval": 10, "ExitTimeOut": 15,
              "WorkingDirectory": str(root), "Umask": 0o077,
              "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}}
    commands = {
        "server": [str(root / "venv" / "bin" / "python"), str(root / "serve.py")],
        "tunnel": [cloudflared, "tunnel", "--no-autoupdate", "--loglevel", "error",
                   "--metrics", "127.0.0.1:0", "run", "--token-file", str(root / "tunnel-token")],
    }
    return {job: {**common, "Label": LABELS[job], "ProgramArguments": command,
                  "StandardOutPath": str(root / "logs" / f"{job}.out.log"),
                  "StandardErrorPath": str(root / "logs" / f"{job}.err.log")}
            for job, command in commands.items()}


def wait_for_server(port: int):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            time.sleep(0.2)
    raise RuntimeError("The server did not become ready. Check the private logs.")


def stop():
    failures = []
    # Remove the connector first. bootout removes KeepAlive as well as the process.
    for job in ("tunnel", "server"):
        if loaded(job):
            result = launchctl("bootout", target(job))
            if result.returncode and loaded(job):
                failures.append(job)
    if failures:
        raise RuntimeError("Could not unload: " + ", ".join(failures))
    print("Stopped. Both jobs are unloaded; they will not restart.")


def start(root: Path):
    state = {job: loaded(job) for job in LABELS}
    if all(state.values()):
        print("Already started. Use status to check process state.")
        return
    if any(state.values()):
        raise ValueError("Only one job is loaded. Run stop, then start.")
    require_private(root, directory=True)
    for directory in ("jobs", "logs", "venv"):
        require_private(root / directory, directory=True)
    require_private(root / "settings.json")
    require_private(root / "tunnel-token")
    if not (root / "tunnel-token").read_text().strip():
        raise ValueError("Save the tunnel token with the tunnel command first.")
    settings = read_settings(root)
    binary = shutil.which("cloudflared")
    if not binary:
        raise ValueError("Install cloudflared first: brew install cloudflared")
    checked = subprocess.run([str(root / "venv" / "bin" / "python"), str(root / "serve.py"), "--check"],
                             capture_output=True, text=True, timeout=20)
    if checked.returncode:
        raise RuntimeError("Installation check failed. Run the installer again while stopped.")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", settings["port"]))
        except OSError as exc:
            raise ValueError("The app port is occupied. Stop the preview or change settings.json.") from exc
    definitions = job_definitions(root, str(Path(binary).resolve()))
    # This is deliberately NOT ~/Library/LaunchAgents or /Library/LaunchDaemons.
    try:
        for job in ("server", "tunnel"):
            path = root / "jobs" / f"{LABELS[job]}.plist"
            private_write(path, plistlib.dumps(definitions[job]))
            result = launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
            if result.returncode:
                raise RuntimeError(f"Could not load {job}: {result.stderr.strip()}")
            if job == "server":
                wait_for_server(settings["port"])
    except BaseException:
        stop()
        raise
    print(f"Started with automatic restart. Public URL: https://{settings['hostname']}")
    print("Tunnel registration is asynchronous; verify the URL in your browser.")


def save_token(root: Path):
    require_private(root, directory=True)
    if any(loaded(job) for job in LABELS):
        raise ValueError("Stop the app before changing the tunnel token.")
    if not sys.stdin.isatty():
        raise ValueError("Run the tunnel command in a terminal so the token stays hidden.")
    token = getpass.getpass("Cloudflare tunnel token (hidden): ").strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError("Paste only the token, not the dashboard's command.")
    private_write(root / "tunnel-token", (token + "\n").encode())
    print("Token saved privately outside the repository. Nothing was started.")


def status(root: Path):
    for job in LABELS:
        result = launchctl("print", target(job))
        if result.returncode:
            print(f"{job}: stopped (not loaded)")
        else:
            state = next((line.strip() for line in result.stdout.splitlines()
                          if line.strip().startswith("state =")), "loaded")
            print(f"{job}: {state}; automatic restart enabled")
    print(f"Private logs: {root / 'logs'}")
    print("This reports local processes, not public tunnel connectivity.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manually control Trip Split on this Mac.")
    parser.add_argument("command", choices=["start", "stop", "status", "tunnel"])
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.getuid() == 0:
        parser.error("Run this on macOS as your normal user, without sudo.")
    os.umask(0o077)
    root = runtime_root()
    try:
        {"start": lambda: start(root), "stop": stop,
         "status": lambda: status(root), "tunnel": lambda: save_token(root)}[args.command]()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
