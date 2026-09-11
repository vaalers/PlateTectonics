import importlib
import subprocess
import sys
from pathlib import Path

import pytest

MODULES = [
    "pt.run_pt_pipeline",
    "pt.phenix_reorg",
    "pt.phenix_to_xlsx_batch",
    "pt.per_object_ff0",
    "pt.filter_post_stats",
    "pt.spaghetti_plot_per_well",
    "pt.combine_date_summaries",
    "pt.build_plate_manifest",
]


def _run(script, *args):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)


@pytest.mark.parametrize("module", MODULES)
def test_each_script_has_help(module):
    mod = importlib.import_module(module)
    script_path = Path(mod.__file__)
    assert script_path.exists()

    r = _run(script_path, "-h")
    assert r.returncode == 0, r.stderr
    assert "usage:" in (r.stdout + r.stderr).lower()
