import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "src" / "pt" / "gui" / "app.py"


def test_launcher_importable():
    from pt.gui import launch
    assert callable(launch.main)


def test_gui_renders_without_error():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(t.value == "Run the pipeline" for t in at.title)
    assert any("PTHTS" in m.value for m in at.sidebar.markdown)
    # The command preview must target the real pipeline module.
    assert any("pt.run_pt_pipeline" in c.value for c in at.code)


def test_pipeline_command_builder():
    pytest.importorskip("streamlit")
    from pt.gui.app import DEFAULT_OPTIONS, build_pipeline_cmd

    opts = dict(DEFAULT_OPTIONS, min_n=18, no_phenix=True, compute_backgrounds=True,
                raw_data_root="/data/raw", objects_population="Nuclei")
    cmd = build_pipeline_cmd(["/data/Analyzed/081825"], opts)
    assert cmd[1:3] == ["-m", "pt.run_pt_pipeline"]
    assert "--min-n" in cmd and "18" in cmd
    assert "--expected-n" not in cmd
    assert "--no-phenix" in cmd
    assert cmd[cmd.index("--raw-data-root") + 1] == "/data/raw"
    assert cmd[cmd.index("--objects-population") + 1] == "Nuclei"


def _make_demo_tree(root: Path) -> None:
    for date, n in (("081825", 2), ("082125", 1)):
        for i in range(1, n + 1):
            ev = root / date / "Analysis" / f"Experiment_{date}_{i}" / "Evaluation1"
            ev.mkdir(parents=True)
            (ev / "PlateResults.txt").write_text("[Data]\nRow\tColumn\tTimepoint\tNumber of Objects\n")
            (ev / "Objects_Population - Nuclei.txt").write_text(
                "[Data]\nRow\tColumn\tTimepoint\tField\tObject No\tIntensity Nucleus Mean\n")


def test_gui_dry_run_end_to_end(tmp_path, monkeypatch):
    """Drive the Run page: set root -> Select all -> Dry run -> Run -> log streams and exits 0."""
    pytest.importorskip("streamlit")
    import time
    from streamlit.testing.v1 import AppTest

    root = tmp_path / "Analyzed"
    _make_demo_tree(root)
    monkeypatch.setenv("HOME", str(tmp_path))          # keep ~/.pthts settings out of the real home
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    at = AppTest.from_file(str(APP), default_timeout=60).run()
    root_box = next(t for t in at.text_input if t.label.startswith("Analyzed data root"))
    root_box.set_value(str(root)).run()
    assert at.dataframe, "date folder table should render"
    assert list(at.dataframe[0].value["date"]) == ["081825", "082125"]

    next(b for b in at.button if b.label == "Select all").click().run()
    assert sorted(lab.split()[0] for lab in at.session_state["date_multiselect"]) == ["081825", "082125"]
    next(c for c in at.checkbox if c.label.startswith("Dry run")).check().run()
    # Background correction is required: without a source the run button stays disabled.
    run_btn = next(b for b in at.button if "Run pipeline" in b.label)
    assert run_btn.disabled, "run must be blocked until a background source is valid"
    bg_csv = tmp_path / "bg.csv"
    bg_csv.write_text("well,field,sequence,background\n")
    at.radio(key="background_mode_radio").set_value("csv").run()
    next(t for t in at.text_input if t.label.startswith("Background CSV file")).set_value(str(bg_csv)).run()
    cmd_preview = next(c.value for c in at.code if "pt.run_pt_pipeline" in c.value)
    assert "--dry-run" in cmd_preview and "081825" in cmd_preview and "082125" in cmd_preview
    assert "--background-csv" in cmd_preview and "--compute-backgrounds" not in cmd_preview

    next(b for b in at.button if "Run pipeline" in b.label).click().run()
    job = at.session_state["job"]
    for _ in range(60):
        if not job.running:
            break
        time.sleep(1)
    at.run()
    print(job.text)
    assert job.rc == 0, job.text
    assert "DRY" in job.text.upper() or "dry" in job.text, job.text
    assert any("[gui]" in c.value and "pt.run_pt_pipeline" in c.value for c in at.code)
    assert any("finished successfully" in s.value for s in at.success), [w.value for w in at.error]


def test_upload_and_results_zip_helpers(tmp_path, monkeypatch):
    """extract_upload accepts a zipped date folder (with or without a parent) and zip_results
    packs outputs while leaving raw Harmony inputs out unless asked."""
    pytest.importorskip("streamlit")
    import io
    import zipfile
    from pt.gui.app import extract_upload, zip_results

    src = tmp_path / "src"
    _make_demo_tree(src)
    # zip with a parent folder around the date folders
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in src.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=str(Path("Analyzed") / f.relative_to(src)))
    root = extract_upload(buf.getvalue(), "Analyzed.zip", tmp_path / "ws")
    assert sorted(p.name for p in root.iterdir()) == ["081825", "082125"]

    # zip of a bare date folder's contents
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in (src / "081825").rglob("*"):
            if f.is_file():
                zf.write(f, arcname=str(f.relative_to(src / "081825")))
    root = extract_upload(buf.getvalue(), "081825.zip", tmp_path / "ws2")
    assert (root / "081825" / "Analysis").is_dir()

    # results zip: fake an output next to the raw inputs
    date_dir = root / "081825"
    (date_dir / "Analysis" / "081825_summary.xlsx").write_bytes(b"x")
    names = zipfile.ZipFile(io.BytesIO(zip_results(date_dir))).namelist()
    assert "081825/Analysis/081825_summary.xlsx" in names
    assert not any(n.endswith("PlateResults.txt") for n in names)
    names_raw = zipfile.ZipFile(io.BytesIO(zip_results(date_dir, include_raw=True))).namelist()
    assert any(n.endswith("PlateResults.txt") for n in names_raw)


def test_materialize_example_copies_inputs_only(tmp_path):
    pytest.importorskip("streamlit")
    from pt.gui.app import EXAMPLE_DATA_DIR, example_dates, materialize_example
    if not EXAMPLE_DATA_DIR.is_dir():
        pytest.skip("examples/example_data not present")
    assert example_dates(), "example date folder should be discoverable"
    root = materialize_example(tmp_path)
    assert re.fullmatch(r"20\d\d", root.name), "returns the <20YY> folder that holds the date folder"
    files = [f for f in root.rglob("*") if f.is_file()]
    assert files and all(f.suffix.lower() in {".txt", ".tiff", ".tif", ".csv"} for f in files)
    assert any(f.name.startswith("Objects_Population") for f in files)
    assert not any(f.suffix == ".xlsx" for f in files)
