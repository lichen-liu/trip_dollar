import runpy
from pathlib import Path


def test_demo_matches_independent_golden_and_blocks_unknown_currency():
    demo = runpy.run_path(str(Path(__file__).parents[1] / "examples/demo/run_demo.py"))
    demo["run"]()
