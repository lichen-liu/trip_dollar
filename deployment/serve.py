"""Loopback-only production server; no deployment credentials enter Flask."""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re


def read_settings(root: Path) -> dict:
    data = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    hostname, port = data.get("hostname"), data.get("port")
    if not isinstance(hostname, str) or len(hostname) > 253 or "." not in hostname or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise ValueError("Use a valid public hostname in settings.json.")
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise ValueError("Use a port between 1024 and 65535 in settings.json.")
    return {"hostname": hostname, "port": port}


def create_server(root: Path):
    from trip_dollar.server.app import create_app

    settings = read_settings(root)
    app = create_app({
        "TRUSTED_HOSTS": [settings["hostname"], "localhost", "127.0.0.1", "[::1]"],
        "MAX_CONCURRENT_CALCULATIONS": 2,
    })
    options = {
        "host": "127.0.0.1", "port": settings["port"], "threads": 4,
        "trusted_proxy": "127.0.0.1", "trusted_proxy_count": 1,
        "trusted_proxy_headers": {"x-forwarded-proto"},
        "clear_untrusted_proxy_headers": True,
        "max_request_body_size": 128_000, "max_request_header_size": 16_384,
        "connection_limit": 32, "backlog": 32, "channel_timeout": 30,
        "expose_tracebacks": False,
    }
    return app, options


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the installed Trip Split app.")
    parser.add_argument("--check", action="store_true", help="check installation without listening")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    app, options = create_server(root)
    if args.check:
        client = app.test_client()
        if client.get("/health").status_code != 200 or client.get("/static/app.js").status_code != 200:
            raise RuntimeError("The installed app or frontend assets are missing.")
        print("Installed app and frontend assets are ready.")
        return
    handler = RotatingFileHandler(root / "logs" / "server.log", maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.WARNING)
    from waitress import serve

    serve(app, **options)


if __name__ == "__main__":
    main()
