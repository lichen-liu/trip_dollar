"""Keep the browser, HTTP server and reusable ledger core separate."""
import os
from pathlib import Path
import subprocess
import sys

from trip_dollar import frontend
from trip_dollar.server.app import create_app


def test_server_serves_assets_from_frontend_package():
    app = create_app({"TESTING": True})
    root = Path(frontend.__file__).parent
    assert Path(app.static_folder) == root / "static"
    assert app.jinja_loader.searchpath == [str(root / "templates")]
    assert app.test_client().get("/").status_code == 200
    assert app.test_client().get("/static/fonts/manrope-variable.woff2").status_code == 200


def test_core_and_cli_work_without_web_dependencies():
    source = Path(__file__).resolve().parents[1] / "src"
    script = """
import sys
sys.modules['flask'] = None
sys.modules['waitress'] = None
from trip_dollar import LedgerEngine, LedgerResult
from trip_dollar.cli import argument_parser
from trip_dollar.core.service import LedgerOptions, calculate
result = calculate(LedgerOptions(['X', 'Y'], 'CAD', ['X', 'Y'], []), 'X10CADA')
assert result['net'] == {'X': '5', 'Y': '-5'}
assert argument_parser().prog
assert not any(name.startswith('trip_dollar.server') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            text=True, env={**os.environ, "PYTHONPATH": str(source)})
    assert result.returncode == 0, result.stderr
