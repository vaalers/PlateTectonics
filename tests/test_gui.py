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
    cmd_preview = next(c.value for c in at.code if "pt.run_pt_pipeline" in c.value)
    assert "--dry-run" in cmd_preview and "081825" in cmd_preview and "082125" in cmd_preview

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
