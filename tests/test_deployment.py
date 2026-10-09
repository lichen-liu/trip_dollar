"""Test manual supervision without loading jobs or contacting Cloudflare."""
import importlib.util
import json
from pathlib import Path
import plistlib
import stat
import subprocess
import sys

import pytest
from waitress.proxy_headers import proxy_headers_middleware

REPO = Path(__file__).resolve().parents[1]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scripts(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "deployment"))
    manage = load_script("deployment_manage", REPO / "deployment/manage.py")
    serving = load_script("deployment_serve", REPO / "deployment/serve.py")
    installer = load_script("deployment_install", REPO / "installation/install.py")
    return manage, serving, installer


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "Trip Split"
    root.mkdir(mode=0o700)
    for name in ("jobs", "logs", "venv"):
        (root / name).mkdir(mode=0o700)
    settings = root / "settings.json"
    settings.write_text(json.dumps({"hostname": "split.boboji.fyi", "port": 8000}))
    settings.chmod(0o600)
    return root


def test_job_definitions_are_manual_private_and_secret_free(scripts, runtime):
    manage, _, _ = scripts
    definitions = manage.job_definitions(runtime, "/opt/homebrew/bin/cloudflared")
    for job in definitions.values():
        assert job["KeepAlive"] is True
        assert job["ThrottleInterval"] == 10
        assert job["Umask"] == 0o077
        assert "RunAtLoad" not in job
        assert "WatchPaths" not in job
        assert "StartInterval" not in job
        assert "LaunchAgents" not in str(job)
        assert "LaunchDaemons" not in str(job)
        assert plistlib.loads(plistlib.dumps(job)) == job
    args = definitions["tunnel"]["ProgramArguments"]
    assert "--token" not in args
    assert args[-2:] == ["--token-file", str(runtime / "tunnel-token")]
    assert "127.0.0.1:0" in args
    assert "--no-autoupdate" in args


def test_manual_stop_unloads_connector_before_server(scripts, monkeypatch):
    manage, _, _ = scripts
    calls = []
    monkeypatch.setattr(manage, "loaded", lambda job: True)
    monkeypatch.setattr(manage, "launchctl", lambda *args: (
        calls.append(args) or subprocess.CompletedProcess(args, 0)))
    manage.stop()
    assert calls == [("bootout", manage.target("tunnel")), ("bootout", manage.target("server"))]


def test_stopping_already_stopped_jobs_is_harmless(scripts, monkeypatch, capsys):
    manage, _, _ = scripts
    monkeypatch.setattr(manage, "loaded", lambda job: False)
    monkeypatch.setattr(manage, "launchctl", lambda *args: pytest.fail("No job should be changed"))
    manage.stop()
    assert "will not restart" in capsys.readouterr().out


def test_status_reports_pids_and_executable_names_without_arguments(scripts, runtime, monkeypatch, capsys):
    manage, _, _ = scripts
    calls = []
    def inspect_job(*args):
        calls.append(args)
        pid = 12345 if args[-1] == manage.target("server") else 12346
        return subprocess.CompletedProcess(args, 0, stdout=f"job = {{\n\tstate = running\n\tpid = {pid}\n\t\tstate = active\n}}\n")
    def inspect_process(args, **kwargs):
        assert args[:2] == ["/bin/ps", "-p"]
        assert args[3:] == ["-o", "comm="]
        assert kwargs == {"capture_output": True, "text": True, "timeout": 5}
        name = "/Applications/Python Framework/Python" if args[2] == "12345" else "/opt/homebrew/bin/cloudflared"
        return subprocess.CompletedProcess(args, 0, stdout=f"{name}\n")
    monkeypatch.setattr(manage, "launchctl", inspect_job)
    monkeypatch.setattr(manage.subprocess, "run", inspect_process)
    manage.status(runtime)
    output = capsys.readouterr().out
    assert "server: state = running; PID: 12345; process: Python; automatic restart enabled" in output
    assert "tunnel: state = running; PID: 12346; process: cloudflared; automatic restart enabled" in output
    assert str(runtime / "logs") in output
    assert "not public tunnel connectivity" in output
    assert calls == [("print", manage.target("server")), ("print", manage.target("tunnel"))]


def test_status_stopped_jobs_have_no_pid_and_do_not_inspect_processes(scripts, runtime, monkeypatch, capsys):
    manage, _, _ = scripts
    monkeypatch.setattr(manage, "launchctl", lambda *args: subprocess.CompletedProcess(args, 1))
    monkeypatch.setattr(manage.subprocess, "run", lambda *args, **kwargs: pytest.fail("No running process to inspect"))
    manage.status(runtime)
    output = capsys.readouterr().out
    for job in ("server", "tunnel"):
        assert f"{job}: stopped (not loaded); PID: none; process: none" in output
    assert "automatic restart enabled" not in output


@pytest.mark.parametrize("pid_line", ["", "\tpid = 0\n", "\tpid = -5\n", "\tpid = invalid\n", "\t\tpid = 999\n"])
def test_status_loaded_job_without_valid_pid_remains_loaded(scripts, runtime, monkeypatch, capsys, pid_line):
    manage, _, _ = scripts
    monkeypatch.setattr(manage, "launchctl", lambda *args: subprocess.CompletedProcess(
        args, 0, stdout=f"job = {{\n\tstate = waiting\n{pid_line}\t\tstate = active\n}}\n"))
    monkeypatch.setattr(manage.subprocess, "run", lambda *args, **kwargs: pytest.fail("No valid process to inspect"))
    manage.status(runtime)
    output = capsys.readouterr().out
    assert "state = waiting; PID: none; process: none; automatic restart enabled" in output
    assert "stopped" not in output


@pytest.mark.parametrize("failure", ["exited", "empty", "permission", "timeout"])
def test_status_still_reports_job_if_process_name_is_unavailable(scripts, runtime, monkeypatch, capsys, failure):
    manage, _, _ = scripts
    monkeypatch.setattr(manage, "launchctl", lambda *args: subprocess.CompletedProcess(
        args, 0, stdout="job = {\n\tstate = running\n\tpid = 12345\n}\n"))
    def inspect_process(args, **kwargs):
        if failure == "permission":
            raise PermissionError("Cannot inspect")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 5)
        return subprocess.CompletedProcess(args, 1 if failure == "exited" else 0, stdout="")
    monkeypatch.setattr(manage.subprocess, "run", inspect_process)
    manage.status(runtime)
    output = capsys.readouterr().out
    assert "PID: 12345; process: unavailable; automatic restart enabled" in output
    assert "tunnel:" in output


def test_stop_attempts_both_jobs_even_if_connector_fails(scripts, monkeypatch):
    manage, _, _ = scripts
    calls = []
    monkeypatch.setattr(manage, "loaded", lambda job: True)
    monkeypatch.setattr(manage, "launchctl", lambda *args: (
        calls.append(args) or subprocess.CompletedProcess(args, 1)))
    with pytest.raises(RuntimeError, match="tunnel, server"):
        manage.stop()
    assert len(calls) == 2


def test_token_prompt_never_echoes_or_starts_jobs(scripts, runtime, monkeypatch, capsys):
    manage, _, _ = scripts
    monkeypatch.setattr(manage, "loaded", lambda job: False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: "placeholder-not-a-real-token")
    manage.save_token(runtime)
    token = runtime / "tunnel-token"
    assert token.read_text() == "placeholder-not-a-real-token\n"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert "placeholder-not-a-real-token" not in capsys.readouterr().out


def test_token_prompt_rejects_noninteractive_input(scripts, runtime, monkeypatch):
    manage, _, _ = scripts
    monkeypatch.setattr(manage, "loaded", lambda job: False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    with pytest.raises(ValueError, match="terminal"):
        manage.save_token(runtime)
    assert not (runtime / "tunnel-token").exists()


def test_private_writes_reject_symlinks_and_shared_files(scripts, runtime, tmp_path):
    manage, _, _ = scripts
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    target = runtime / "tunnel-token"
    target.symlink_to(outside)
    with pytest.raises(ValueError):
        manage.private_write(target, b"not-written")
    assert outside.read_text() == "untouched"
    outside.chmod(0o644)
    with pytest.raises(ValueError):
        manage.require_private(outside)


@pytest.mark.parametrize("hostname,port", [
    ("https://split.boboji.fyi", 8000), ("*.boboji.fyi", 8000),
    ("split..boboji.fyi", 8000), ("-split.boboji.fyi", 8000),
    ("split.boboji.fyi", True), ("split.boboji.fyi", 80),
    ("split.boboji.fyi", 65536),
])
def test_bad_settings_fail_closed(scripts, runtime, hostname, port):
    _, serving, _ = scripts
    (runtime / "settings.json").write_text(json.dumps({"hostname": hostname, "port": port}))
    with pytest.raises(ValueError):
        serving.read_settings(runtime)


def test_public_serving_profile_trusts_only_local_https_proxy(scripts, runtime):
    _, serving, _ = scripts
    app, options = serving.create_server(runtime)
    assert options["host"] == "127.0.0.1"
    assert options["trusted_proxy"] == "127.0.0.1"
    assert options["trusted_proxy_headers"] == {"x-forwarded-proto"}
    assert options["expose_tracebacks"] is False
    assert options["connection_limit"] == 32
    assert "FX_CACHE" not in app.config
    # Use Waitress's actual middleware, not an independently configured ProxyFix.
    keys = ("trusted_proxy", "trusted_proxy_count", "trusted_proxy_headers")
    app.wsgi_app = proxy_headers_middleware(
        app.wsgi_app, clear_untrusted=options["clear_untrusted_proxy_headers"],
        **{key: options[key] for key in keys})
    client = app.test_client()
    payload = client.get("/static/example.json").json
    headers = {"Host": "split.boboji.fyi", "Origin": "https://split.boboji.fyi",
               "X-Forwarded-Proto": "https", "X-Forwarded-Host": "attacker.invalid"}
    response = client.post("/api/calculate", json=payload, headers=headers,
                           environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert response.status_code == 200
    assert response.json["display"]["total"] == "290.00"
    response = client.post("/api/calculate", json=payload, headers=headers,
                           environ_overrides={"REMOTE_ADDR": "198.51.100.1"})
    assert response.status_code == 403
    assert response.json["settlements"] == []
    assert client.get("/", headers={"Host": "attacker.invalid"}).status_code == 400
    headers["Origin"] = "https://attacker.invalid"
    assert client.post("/api/calculate", json=payload, headers=headers,
                       environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code == 403


def test_start_rolls_back_jobs_if_tunnel_bootstrap_fails(scripts, runtime, monkeypatch):
    manage, _, _ = scripts
    manage.private_write(runtime / "tunnel-token", b"placeholder-only")
    jobs = set()
    calls = []
    def fake_launchctl(*args):
        calls.append(args)
        job = "tunnel" if "tunnel" in args[-1] else "server"
        if args[0] == "print":
            return subprocess.CompletedProcess(args, 0 if job in jobs else 1)
        if args[0] == "bootstrap":
            if job == "tunnel":
                return subprocess.CompletedProcess(args, 1, stderr="test failure")
            jobs.add(job)
        if args[0] == "bootout":
            jobs.discard(job)
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(manage, "launchctl", fake_launchctl)
    monkeypatch.setattr(manage.shutil, "which", lambda name: "/bin/false")
    monkeypatch.setattr(manage.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    monkeypatch.setattr(manage, "wait_for_server", lambda port: None)
    class PortProbe:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def bind(self, address): assert address == ("127.0.0.1", 8000)
    monkeypatch.setattr(manage.socket, "socket", PortProbe)
    with pytest.raises(RuntimeError, match="Could not load tunnel"):
        manage.start(runtime)
    assert jobs == set()
    assert any(call[0] == "bootout" for call in calls)


def test_installer_refuses_to_change_a_loaded_app(scripts, runtime, monkeypatch):
    _, _, installer = scripts
    monkeypatch.setattr(installer, "loaded", lambda job: True)
    with pytest.raises(ValueError, match="Stop Trip Split"):
        installer.install(runtime)


def test_installer_makes_snapshot_without_starting_or_replacing_token(scripts, runtime, monkeypatch):
    manage, _, installer = scripts
    manage.private_write(runtime / "tunnel-token", b"placeholder-only")
    monkeypatch.setattr(installer, "loaded", lambda job: False)
    class FakeBuilder:
        def __init__(self, **kwargs): assert kwargs == {"with_pip": True}
        def create(self, path): assert path == runtime / "venv"
    monkeypatch.setattr(installer.venv, "EnvBuilder", FakeBuilder)
    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        if "wheel" in args:
            folder = Path(args[args.index("--wheel-dir") + 1])
            (folder / "trip_dollar-0.1.0-py3-none-any.whl").write_bytes(b"test wheel")
        output = ""
        if args[:2] == ["git", "rev-parse"]: output = "test-commit\n"
        if "freeze" in args: output = "Flask==3.1.3\n"
        return subprocess.CompletedProcess(args, 0, stdout=output)
    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    installer.install(runtime)
    assert not (runtime / "state").exists()
    assert (runtime / "tunnel-token").read_bytes() == b"placeholder-only"
    receipt = json.loads((runtime / "installation.json").read_text())
    assert receipt["source_commit"] == "test-commit"
    assert receipt["uncommitted_changes"] is False
    assert len(receipt["wheel_sha256"]) == 64
    assert any("--force-reinstall" in call for call in calls)
    assert not any("/bin/launchctl" in call for call in calls)
    assert list((runtime / "jobs").iterdir()) == []
    for name in ("serve.py", "manage.py", "installation.json", "settings.json", "tunnel-token"):
        assert stat.S_IMODE((runtime / name).stat().st_mode) == 0o600


def test_start_of_partial_job_set_requires_explicit_stop(scripts, runtime, monkeypatch):
    manage, _, _ = scripts
    monkeypatch.setattr(manage, "loaded", lambda job: job == "server")
    with pytest.raises(ValueError, match="stop, then start"):
        manage.start(runtime)
