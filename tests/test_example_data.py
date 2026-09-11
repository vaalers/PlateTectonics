"""End-to-end check: the synthetic example generator produces data the pipeline can process."""
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
GENERATOR = REPO / "tools" / "make_example_data.py"


@pytest.fixture(scope="module")
def example_root(tmp_path_factory):
    out = tmp_path_factory.mktemp("example")
    r = subprocess.run([sys.executable, str(GENERATOR), "--out", str(out), "--date", "090826", "--seed", "0"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return out


def test_generator_layout(example_root):
    date_dir = example_root / "2026" / "090826"
    ev = date_dir / "Analysis" / "Experiment_090826_1" / "Evaluation1"
    assert (ev / "PlateResults.txt").is_file()
    assert (ev / "Objects_Population - Cells.txt").is_file()
    assert (date_dir / "Analysis" / "Experiment_090826_1" / "indexfile.txt").is_file()
    text = (ev / "PlateResults.txt").read_text(encoding="utf-8")
    assert "[Data]" in text and "Row\tColumn" in text
    idx = pd.read_csv(date_dir / "Analysis" / "Experiment_090826_1" / "indexfile.txt", sep="\t")
    assert set(idx["Sequence"].unique()) == {1, 2, 3, 4}
    assert idx["Timepoint"].max() == 27


def test_pipeline_runs_on_example(example_root):
    date_dir = example_root / "2026" / "090826"
    cmd = [sys.executable, "-m", "pt.run_pt_pipeline", str(date_dir), "--python-bin", sys.executable,
           "--expected-n", "27", "--ff0-no-plots", "--no-pptx", "--no-spaghetti", "--no-combine-date-summaries"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (r.stdout + r.stderr)[-4000:]
    ev = date_dir / "Analysis" / "Experiment_090826_1" / "Evaluation1"
    assert (ev / "Experiment_090826_1_Evaluation1_per-well.xlsx").is_file()
    filtered = list(ev.rglob("*_filtered_objects.xlsx"))
    assert filtered, "filtered workbook not written"
    overview = pd.ExcelFile(filtered[0]).parse("Responder_Overview").set_index("Well")
    # Compound wells respond to Stim 1, vehicle wells barely; the poor well is dropped by QC.
    assert overview.loc["B2", "stim1_responders"] > overview.loc["C2", "stim1_responders"]
    assert overview.loc["B2", "general_responders"] >= 20
    assert "C4" not in overview.index
