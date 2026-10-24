"""Optional telemetry, offline registration and authenticated delivery contracts."""

import importlib
import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest
from bobowatch.config import initialize

from trip_dollar.server.app import create_app
from trip_dollar.server.monitoring import provision, wrap_application

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "split"
    root.mkdir(mode=0o700)
    path = root / "settings.json"
    path.write_text(json.dumps({"hostname": "split.boboji.fyi", "port": 8000}))
    path.chmod(0o600)
    return root


@pytest.fixture
def backend():
    # The wheel bundles only the producer. Load the test receiver separately.
    source = REPO / "vendor/bobowatch/bobowatch"
    name = "_split_watch_receiver"
    spec = importlib.util.spec_from_file_location(
        name, source / "__init__.py", submodule_search_locations=[str(source)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return SimpleNamespace(
        Application=importlib.import_module(name + ".application").Application,
        SQLiteStore=importlib.import_module(name + ".storage").SQLiteStore,
    )


def test_offline_registration_is_private_stable_and_keeps_app_settings(
    runtime, tmp_path, monkeypatch
):
    watch = tmp_path / "watch"
    initialize(watch, port=9123)
    with patch("sqlite3.connect", side_effect=AssertionError("No database during registration")):
        result = provision(runtime, watch)
    token = Path(result["token_file"]).read_bytes()
    before = (runtime / "settings.json").read_bytes()
    monkeypatch.delenv("BOBOWATCH_RUNTIME_DIR", raising=False)
    assert provision(runtime) == result
    assert before == (runtime / "settings.json").read_bytes()
    data = json.loads(before)
    assert data["hostname"] == "split.boboji.fyi"
    assert data["port"] == 8000
    assert data["monitoring_registration"]["project"] == "trip-dollar"
    assert data["monitoring_registration"]["site"] == "trip-split-production"
    assert result["endpoint"] == "http://127.0.0.1:9123/v1/ingest"
    assert result["replica"].startswith("trip-split-host-")
    assert Path(result["token_file"]).read_bytes() == token
    assert token.decode().strip() not in before.decode()
    assert Path(result["token_file"]).stat().st_mode & 0o077 == 0
    assert (runtime / "settings.json").stat().st_mode & 0o077 == 0
    assert not list(watch.glob("*.sqlite*"))


def test_disabled_monitoring_does_nothing_and_invalid_enabled_settings_fail_before_serving(
    runtime,
):
    app = create_app()
    original = app.wsgi_app
    with patch("bobowatch.adapter.Monitor.start", side_effect=AssertionError("No sender")):
        assert wrap_application(app, runtime / "settings.json") is None
        assert app.wsgi_app == original
        assert app.test_client().get("/health").status_code == 200
    data = json.loads((runtime / "settings.json").read_text())
    data["monitoring"] = {
        "enabled": True,
        "replica": "split-r01",
        "endpoint": "http://unsafe.invalid/v1/ingest",
        "token_file": "x",
    }
    (runtime / "settings.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="loopback"):
        wrap_application(create_app(), runtime / "settings.json")


def test_http_status_latency_bytes_and_privacy(runtime, tmp_path):
    initialize(tmp_path / "watch")
    provision(runtime, tmp_path / "watch")
    app = create_app({"TESTING": True})
    wrapped = wrap_application(app, runtime / "settings.json", revision="test-commit")
    with patch("bobowatch.adapter.Monitor.start"):
        client = app.test_client()
        responses = [
            client.get("/health"),
            client.get("/static/app.js?name=private-name"),
            client.get("/private-name?secret=private-notes"),
            client.get("/health", headers={"Host": "attacker.invalid"}),
            client.post(
                "/api/calculate",
                data="{" + "x" * 128_000,
                content_type="application/json",
            ),
            client.post("/api/calculate", json={"source": "private-notes"}),
        ]
        for response in responses:
            response.get_data()
            response.close()
        assert [r.status_code for r in responses] == [200, 200, 404, 400, 413, 422]
        payload = client.get("/static/example.json").json
        payload["participants"][0]["name"] = "private-person"
        response = client.post("/api/calculate", json=payload)
        assert response.status_code == 200
        assert response.json["display"]["total"] == "290.00"
        response.close()
        snapshot = wrapped.monitor.snapshot()
        assert snapshot["revision"] == "test-commit"
        assert snapshot["requests"] == 8
        assert snapshot["duration_sum_us"] > 0
        assert snapshot["http"]["in_flight"] == 0
        assert snapshot["http"]["status_classes"] == [0, 4, 0, 4, 0]
        assert snapshot["http"]["sized_requests"] >= 5
        assert snapshot["http"]["bytes_sent"] > 0
        assert snapshot["http"]["bytes_received"] > 0
        encoded = json.dumps(snapshot)
        assert all(
            value not in encoded
            for value in (
                "private-name",
                "private-person",
                "private-notes",
                "secret=",
                "290.00",
            )
        )
        labels = {row["route"] for row in snapshot["http"]["routes"]}
        assert labels == {
            "/",
            "/health",
            "/api/calculate",
            "/static/<path:filename>",
            "unmatched",
        }
    wrapped.close()


def test_preflight_does_not_start_or_require_monitoring(runtime, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "split_serving_test", REPO / "deployment/serve.py"
    )
    serving = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serving)
    data = json.loads((runtime / "settings.json").read_text())
    data["monitoring"] = {"enabled": True, "endpoint": "not-a-url"}
    (runtime / "settings.json").write_text(json.dumps(data))
    monkeypatch.setattr(serving, "__file__", str(runtime / "serve.py"))
    with patch(
        "trip_dollar.server.monitoring.wrap_application",
        side_effect=AssertionError("No telemetry in preflight"),
    ):
        serving.main(["--check"])


class QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


def test_delivery_recovers_after_watch_outage_and_retargets_live(runtime, tmp_path, backend):
    server = make_server("127.0.0.1", 0, lambda e, s: [], handler_class=QuietHandler)
    config = initialize(tmp_path / "watch", port=server.server_port)
    provision(runtime, config.path.parent)
    store = backend.SQLiteStore(config)
    application = backend.Application(config, store)
    application.reload()
    server.set_app(application)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    wrapped = wrap_application(app := create_app(), runtime / "settings.json")
    try:
        with patch("bobowatch.adapter.Monitor.start"):
            client = app.test_client()
            client.get("/health").close()
            monitor = wrapped.monitor
            run = monitor.run_id
            assert monitor.flush()
            port = server.server_port
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            # Application traffic still succeeds with no receiver.
            client.get("/health").close()
            assert not monitor.flush()
            server = make_server("127.0.0.1", port, application, handler_class=QuietHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            assert monitor.flush()
            assert monitor.run_id == run
            data = store.overview({}, 900)
            assert sum(site["requests"] for site in data["sites"]) == 2
            # A different endpoint and token are picked up without restarting.
            second_server = make_server("127.0.0.1", 0, lambda e, s: [], handler_class=QuietHandler)
            second = initialize(tmp_path / "second-watch", port=second_server.server_port)
            second_store = backend.SQLiteStore(second)
            second_application = backend.Application(second, second_store)
            second_server.set_app(second_application)
            second_thread = threading.Thread(target=second_server.serve_forever, daemon=True)
            second_thread.start()
            try:
                provision(runtime, second.path.parent)
                second_application.reload()
                assert monitor.flush()
                assert monitor.endpoint.endswith(f":{second_server.server_port}/v1/ingest")
                assert monitor.run_id == run
                data = second_store.overview({}, 900)
                assert sum(site["requests"] for site in data["sites"]) == 2
            finally:
                second_server.shutdown()
                second_server.server_close()
                second_thread.join(timeout=2)
                second_store.close()
            provision(runtime, config.path.parent)
            assert monitor.flush()
            assert monitor.run_id == run
    finally:
        wrapped.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()


def test_controller_registers_with_installed_python_and_keeps_disabled_config(runtime, monkeypatch):
    monkeypatch.syspath_prepend(str(REPO / "deployment"))
    spec = importlib.util.spec_from_file_location(
        "split_manage_test", REPO / "deployment/manage.py"
    )
    manage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(manage)
    calls = []
    monkeypatch.setattr(manage.subprocess, "run", lambda args, **kwargs: calls.append(args))
    assert manage.configure_monitoring(runtime) is False
    assert not calls
    assert manage.configure_monitoring(runtime, Path("/tmp/watch")) is True
    assert calls[-1] == [
        str(runtime / "venv/bin/python"),
        "-m",
        "trip_dollar.server.monitoring",
        "--runtime-dir",
        str(runtime),
        "--bobowatch-runtime-dir",
        "/tmp/watch",
    ]
    with pytest.raises(ValueError, match="absolute"):
        manage.configure_monitoring(runtime, Path("relative"))
    data = json.loads((runtime / "settings.json").read_text())
    data.update(
        monitoring={"enabled": True},
        monitoring_registration={"runtime_dir": "/tmp/saved-watch"},
    )
    (runtime / "settings.json").write_text(json.dumps(data))
    assert manage.configure_monitoring(runtime) is True
    assert calls[-1][-2:] == ["--runtime-dir", str(runtime)]
    data["monitoring"]["enabled"] = False
    (runtime / "settings.json").write_text(json.dumps(data))
    assert manage.configure_monitoring(runtime) is False


def test_busy_and_failed_calculations_are_observed(runtime, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    initialize(tmp_path / "watch")
    provision(runtime, tmp_path / "watch")
    app = create_app({"MAX_CONCURRENT_CALCULATIONS": 1})
    wrapped = wrap_application(app, runtime / "settings.json")
    entered, release = threading.Event(), threading.Event()
    payload = json.loads((REPO / "src/trip_dollar/frontend/static/example.json").read_text())

    def slow_calculate(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        raise RuntimeError("private calculation details")

    def calculate_request():
        response = app.test_client().post("/api/calculate", json=payload)
        response.get_data()
        response.close()
        return response.status_code

    try:
        with (
            patch("bobowatch.adapter.Monitor.start"),
            patch("trip_dollar.server.app.calculate", slow_calculate),
        ):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(calculate_request)
                try:
                    assert entered.wait(timeout=5)
                    assert wrapped.monitor.snapshot()["http"]["in_flight"] == 1
                    assert calculate_request() == 503
                finally:
                    release.set()
                assert pending.result(timeout=5) == 500
        snapshot = wrapped.monitor.snapshot()
        assert snapshot["requests"] == 2
        assert snapshot["errors"] == 2
        assert snapshot["http"]["in_flight"] == 0
        assert "private calculation details" not in json.dumps(snapshot)
    finally:
        wrapped.close()
