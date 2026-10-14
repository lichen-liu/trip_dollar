"""Manually load/unload two user-session launchd jobs, never login items."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

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


def process_name(pid: int) -> str:
    """Read the executable name only; command arguments may contain secrets."""
    try:
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "comm="],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return "unavailable"
    if result.returncode or not result.stdout.strip():
        return "unavailable"
    return Path(result.stdout.strip()).name


def status(root: Path):
    for job in LABELS:
        result = launchctl("print", target(job))
        if result.returncode:
            print(f"{job}: stopped (not loaded); PID: none; process: none")
        else:
            # launchctl indents job fields once; nested service state is not
            # the state or PID of the managed process.
            state_match = re.search(r"(?m)^\tstate = (.+)$", result.stdout)
            pid_match = re.search(r"(?m)^\tpid = ([1-9][0-9]*)$", result.stdout)
            state = f"state = {state_match[1]}" if state_match else "loaded"
            pid = pid_match[1] if pid_match else "none"
            name = process_name(int(pid)) if pid_match else "none"
            print(f"{job}: {state}; PID: {pid}; process: {name}; automatic restart enabled")
    try:
        settings = read_settings(root)
    except (OSError, ValueError, AttributeError):
        print("Server port: unavailable (settings missing or invalid)")
    else:
        print(f"Server port: {settings['port']} (configured; http://127.0.0.1:{settings['port']}/)")
    print(f"Private logs: {root / 'logs'}")
    print("This reports local processes, not public tunnel connectivity. Use check --public.")


def probe(base_url: str):
    """Check the app and calculator without external FX lookups or stored data."""
    def origin(url):
        parts = urlsplit(url)
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)

    expected = origin(base_url)

    class SameOriginRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, new_url):
            if origin(new_url) != expected:
                raise ValueError("Trip Split redirected away from the configured origin.")
            return super().redirect_request(request, fp, code, msg, headers, new_url)

    opener = build_opener(SameOriginRedirect())

    def fetch(path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(base_url + path, data=body,
                          headers={"Content-Type": "application/json", "Origin": base_url})
        with opener.open(request, timeout=10) as response:
            if origin(response.geturl()) != expected:
                raise ValueError("The response is not from the configured Trip Split origin.")
            content = response.read(1_000_001)
            if response.status != 200 or len(content) > 1_000_000:
                raise ValueError("Unexpected or oversized response from Trip Split.")
            return content

    if b"<title>Trip Split" not in fetch("/"):
        raise ValueError("The URL is not serving Trip Split.")
    if b"/api/calculate" not in fetch("/static/app.js"):
        raise ValueError("The Trip Split frontend is missing or outdated.")
    health = json.loads(fetch("/health"))
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise ValueError("Trip Split is not healthy.")
    # Fixed inputs and manual FX make this independent of upstream providers.
    result = json.loads(fetch("/api/calculate", {
        "source": "2026.Aug.A weekend away\n\n0815\nBreakfast: L60CAD\nMuseum: C90A\nTaxi: B24CUSD\n\n0814\nDinner: L48A\nSouvenir: C20BCAD\nSnacks: B30A",
        "participants": [{"code": "L", "name": "Lee", "split": True},
                         {"code": "B", "name": "Blair", "split": True},
                         {"code": "C", "name": "Casey", "split": False}],
        "base": "CAD", "fx": "USD=1.25CAD",
    }))
    display = result.get("display") if isinstance(result, dict) else None
    audit = result.get("audit") if isinstance(result, dict) else None
    if (not isinstance(display, dict) or display.get("total") != "290.00"
            or not isinstance(audit, dict) or not isinstance(audit.get("settlements"), list)
            or not audit["settlements"]):
        raise ValueError("The example calculation did not return the expected payment plan.")


def check(root: Path, *, public: bool = False):
    require_private(root, directory=True)
    require_private(root / "settings.json")
    settings = read_settings(root)
    base = f"https://{settings['hostname']}" if public else f"http://127.0.0.1:{settings['port']}"
    probe(base)
    print(f"Verified {'live' if public else 'locally'}: {base}/ · health, frontend and example calculation passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manually control Trip Split on this Mac.")
    parser.add_argument("command", choices=["start", "stop", "status", "tunnel", "check"])
    parser.add_argument("--public", action="store_true", help="verify the configured HTTPS domain")
    args = parser.parse_args(argv)
    if args.public and args.command != "check":
        parser.error("--public is only used with check.")
    if sys.platform != "darwin" or os.getuid() == 0:
        parser.error("Run this on macOS as your normal user, without sudo.")
    os.umask(0o077)
    root = runtime_root()
    try:
        {"start": lambda: start(root), "stop": stop,
         "status": lambda: status(root), "tunnel": lambda: save_token(root),
         "check": lambda: check(root, public=args.public)}[args.command]()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
