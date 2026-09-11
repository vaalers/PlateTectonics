"""PTHTS graphical interface (Streamlit).

Launch with ``pt-gui`` (or ``streamlit run src/pt/gui/app.py``).

The GUI does nothing the command line cannot do: every action builds the same
``python -m pt.<module> ...`` command a user would type, runs it as a subprocess,
and streams its output. The exact command is always shown so it can be copied
into a terminal or a batch file.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
APP_TITLE = "PTHTS"
SETTINGS_PATH = Path.home() / ".pthts" / "gui_settings.json"
DATE_RE = re.compile(r"^\d{6}$")
EXP_RE = re.compile(r"^Experiment_\d{6}_\d+$", re.I)
EVAL_RE = re.compile(r"^Evaluation[ _]?\d+$", re.I)
IMG_EXTS = {".png", ".jpg", ".jpeg"}
LOG_TAIL_LINES = 500

DEFAULT_OPTIONS = {
    # analysis
    "stim": 10,
    "baseline_n": 5,
    "expected_n": 20,
    "min_n": 0,          # 0 = not used (falls back to expected_n)
    "ymin": -0.5,
    "ymax": 7.0,
    # plots
    "per_page": 20,
    "pptx": True,
    "ff0_no_plots": False,
    "ff0_no_single_pngs": False,
    "ff0_no_iqr_filter": False,
    # steps
    "no_phenix": False,
    "no_ff0": False,
    "no_spaghetti": False,
    "no_combine": False,
    # background
    "compute_backgrounds": False,
    "raw_data_root": "",
    "background_channel": "",
    "background_timepoints": 20,
    "background_csv": "",
    # advanced
    "objects_population": "",
    "threads": 4,
    "timeout": 7200,
    "verbose": False,
    "strict_phenix": False,
    "dry_run": False,
    "extra_args": "",
}


# ----------------------------------------------------------------------------
# Persistent settings (per user, outside the repository)
# ----------------------------------------------------------------------------
def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    except Exception:
        pass


def settings() -> dict:
    if "settings" not in st.session_state:
        st.session_state["settings"] = load_settings()
    return st.session_state["settings"]


def remember(**kwargs) -> None:
    s = settings()
    s.update(kwargs)
    save_settings(s)


# ----------------------------------------------------------------------------
# Background job runner
# ----------------------------------------------------------------------------
@dataclass
class Job:
    cmd: list[str]
    label: str
    cwd: Optional[str] = None
    proc: Optional[subprocess.Popen] = None
    lines: list[str] = field(default_factory=list)
    rc: Optional[int] = None
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    stopped: bool = False
    context: dict = field(default_factory=dict)

    @property
    def running(self) -> bool:
        return self.rc is None

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _reader(job: Job) -> None:
    try:
        assert job.proc is not None and job.proc.stdout is not None
        for line in job.proc.stdout:
            job.lines.append(line.rstrip("\r\n"))
    except Exception as exc:  # pragma: no cover - defensive
        job.lines.append(f"[gui] reader error: {exc}")
    finally:
        try:
            job.proc.wait()
            job.rc = job.proc.returncode
        except Exception:
            job.rc = -1
        job.finished = time.time()


def start_job(cmd: list[str], label: str, cwd: Optional[str] = None, context: Optional[dict] = None) -> Job:
    popen_kwargs: dict = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd or None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        **popen_kwargs,
    )
    job = Job(cmd=cmd, label=label, cwd=cwd, proc=proc, context=context or {})
    job.lines.append(f"[gui] {datetime.now():%Y-%m-%d %H:%M:%S}  $ {shlex.join(cmd)}")
    threading.Thread(target=_reader, args=(job,), daemon=True).start()
    st.session_state["job"] = job
    return job


def stop_job(job: Job) -> None:
    if not job.running or job.proc is None:
        return
    job.stopped = True
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(job.proc.pid), "/T", "/F"], capture_output=True)
        else:
            os.killpg(os.getpgid(job.proc.pid), signal.SIGTERM)
    except Exception:
        try:
            job.proc.terminate()
        except Exception:
            pass
    job.lines.append("[gui] stop requested by user")


def current_job() -> Optional[Job]:
    return st.session_state.get("job")


def _job_body(job: Job) -> None:
    mins, secs = divmod(int(job.elapsed), 60)
    if job.running:
        st.info(f"**{job.label}** — running for {mins:d}m {secs:02d}s", icon="⏳")
        if st.button("Stop", key="stop_job_btn", type="secondary"):
            stop_job(job)
    elif job.stopped:
        st.warning(f"**{job.label}** — stopped by user after {mins:d}m {secs:02d}s", icon="🛑")
    elif job.rc == 0:
        st.success(f"**{job.label}** — finished successfully in {mins:d}m {secs:02d}s", icon="✅")
    else:
        st.error(f"**{job.label}** — failed (exit code {job.rc}) after {mins:d}m {secs:02d}s. "
                 "Scroll the log for lines starting with [ERROR].", icon="❌")

    with st.expander("Command", expanded=False):
        st.code(shlex.join(job.cmd), language="bash")

    tail = job.lines[-LOG_TAIL_LINES:]
    if len(job.lines) > LOG_TAIL_LINES:
        st.caption(f"Showing the last {LOG_TAIL_LINES} of {len(job.lines)} lines.")
    with st.container(height=420):
        st.code("\n".join(tail) if tail else "(no output yet)", language="text")
    st.download_button(
        "Download full log",
        data=job.text,
        file_name=f"{job.label.lower().replace(' ', '_')}_{datetime.fromtimestamp(job.started):%Y%m%d_%H%M%S}.log",
        mime="text/plain",
        key="dl_job_log",
    )


@st.fragment(run_every="1s")
def _live_job_panel() -> None:
    job = current_job()
    if job is None:
        return
    _job_body(job)
    if not job.running:
        st.rerun()


def render_job_panel() -> None:
    job = current_job()
    if job is None:
        return
    st.subheader("Run output")
    if job.running:
        _live_job_panel()
    else:
        _job_body(job)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def open_in_file_manager(path: Path) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as exc:
        st.warning(f"Could not open {path}: {exc}")


def path_or_none(text: str) -> Optional[Path]:
    text = (text or "").strip().strip('"').strip("'")
    return Path(text).expanduser() if text else None


@st.cache_data(ttl=20, show_spinner=False)
def scan_root(root: str) -> list[dict]:
    rootp = Path(root)
    rows: list[dict] = []
    if not rootp.is_dir():
        return rows
    for d in sorted(rootp.iterdir()):
        if not (d.is_dir() and DATE_RE.match(d.name)):
            continue
        analysis = d / "Analysis"
        exps = [e for e in analysis.glob("Experiment_*") if e.is_dir()] if analysis.is_dir() else []
        evals = [ev for e in exps for ev in e.iterdir() if ev.is_dir() and EVAL_RE.match(ev.name)]
        n_raw = sum(1 for ev in evals if any(ev.glob("PlateResults*.txt")) and any(ev.glob("Objects_Population*.txt")))
        n_perwell = sum(1 for ev in evals if any(ev.glob("*_per-well.xlsx")))
        n_filtered = sum(1 for ev in evals if any(ev.rglob("*_filtered_objects.xlsx")))
        if n_filtered and n_filtered == len(evals):
            status = "analyzed"
        elif n_perwell or n_filtered:
            status = "partial"
        elif n_raw:
            status = "raw exports"
        else:
            status = "no data"
        rows.append({
            "date": d.name,
            "experiments": len(exps),
            "evaluations": len(evals),
            "raw exports": n_raw,
            "per-well xlsx": n_perwell,
            "filtered": n_filtered,
            "status": status,
            "path": str(d),
        })
    return rows


def list_experiments(date_dir: Path) -> list[Path]:
    analysis = date_dir / "Analysis"
    if not analysis.is_dir():
        return []
    return sorted(p for p in analysis.iterdir() if p.is_dir() and EXP_RE.match(p.name))


def list_evaluations(exp_dir: Path) -> list[Path]:
    return sorted(p for p in exp_dir.iterdir() if p.is_dir() and EVAL_RE.match(p.name))


def build_pipeline_cmd(paths: list[str], o: dict) -> list[str]:
    """Translate GUI options into the exact ``pt-run`` command line."""
    cmd = [sys.executable, "-m", "pt.run_pt_pipeline", *paths, "--python-bin", sys.executable]
    cmd += ["--stim", str(int(o["stim"])), "--baseline-n", str(int(o["baseline_n"]))]
    cmd += ["--ylim", str(float(o["ymin"])), str(float(o["ymax"]))]
    if int(o.get("min_n") or 0) > 0:
        cmd += ["--min-n", str(int(o["min_n"]))]
    else:
        cmd += ["--expected-n", str(int(o["expected_n"]))]
    cmd += ["--per-page", str(int(o["per_page"]))]
    if not o["pptx"]:
        cmd.append("--no-pptx")
    for key, flag in [
        ("ff0_no_plots", "--ff0-no-plots"),
        ("ff0_no_single_pngs", "--ff0-no-single-pngs"),
        ("ff0_no_iqr_filter", "--ff0-no-iqr-filter"),
        ("no_phenix", "--no-phenix"),
        ("no_ff0", "--no-ff0"),
        ("no_spaghetti", "--no-spaghetti"),
        ("no_combine", "--no-combine-date-summaries"),
        ("verbose", "--verbose"),
        ("strict_phenix", "--strict-phenix"),
        ("dry_run", "--dry-run"),
    ]:
        if o.get(key):
            cmd.append(flag)
    if o.get("compute_backgrounds"):
        cmd.append("--compute-backgrounds")
        cmd += ["--background-timepoints", str(int(o["background_timepoints"]))]
        if (o.get("raw_data_root") or "").strip():
            cmd += ["--raw-data-root", o["raw_data_root"].strip()]
        if (o.get("background_channel") or "").strip():
            cmd += ["--background-channel", o["background_channel"].strip()]
    if (o.get("background_csv") or "").strip():
        cmd += ["--background-csv", o["background_csv"].strip()]
    if (o.get("objects_population") or "").strip():
        cmd += ["--objects-population", o["objects_population"].strip()]
    cmd += ["--threads", str(int(o["threads"])), "--timeout", str(int(o["timeout"]))]
    if (o.get("extra_args") or "").strip():
        cmd += shlex.split(o["extra_args"])
    return cmd


# ----------------------------------------------------------------------------
# Page: Run pipeline
# ----------------------------------------------------------------------------
def page_run() -> None:
    st.title("Run the pipeline")
    st.caption("Select one or more date folders, adjust options, and run. "
               "The same command is available for copying into a terminal.")

    s = settings()
    saved = dict(DEFAULT_OPTIONS, **s.get("pipeline_options", {}))

    # ---- data selection
    st.subheader("1. Data")
    col_a, col_b = st.columns([4, 1])
    root_text = col_a.text_input(
        "Analyzed data root (the folder that contains 6-digit MMDDYY date folders)",
        value=s.get("analyzed_root", ""),
        placeholder=r"e.g. D:\PhenixData\Analyzed  or  /Volumes/data/Analyzed",
    )
    if col_b.button("Rescan", width="stretch"):
        scan_root.clear()
    root = path_or_none(root_text)
    selected_paths: list[str] = []
    if root and root.is_dir():
        if s.get("analyzed_root") != str(root):
            remember(analyzed_root=str(root))
        rows = scan_root(str(root))
        if rows:
            df = pd.DataFrame(rows).drop(columns=["path"])
            st.dataframe(df, hide_index=True, width="stretch")
            labels = {f"{r['date']}  ({r['status']})": r["path"] for r in rows}
            key = "date_multiselect"
            # Seed the widget once from the last run; keep only labels that still exist.
            if key not in st.session_state:
                st.session_state[key] = [lab for lab, p in labels.items()
                                         if Path(p).name in s.get("last_dates", [])]
            st.session_state[key] = [lab for lab in st.session_state[key] if lab in labels]
            chosen = st.multiselect("Date folders to process", options=list(labels), key=key)
            selected_paths = [labels[c] for c in chosen]
            c1, c2, _ = st.columns([1, 1, 4])
            c1.button("Select all", on_click=lambda: st.session_state.update({key: list(labels)}))
            c2.button("Clear", on_click=lambda: st.session_state.update({key: []}))
        else:
            st.warning("No 6-digit date folders found directly under this root.")
    elif root:
        st.error(f"Folder not found: {root}")

    other = st.text_input(
        "…or a single Experiment_* / Analysis / date folder path (optional)",
        value="", placeholder="Leave empty to use the selection above",
    )
    if other.strip():
        p = path_or_none(other)
        if p and p.exists():
            selected_paths = [str(p)]
        else:
            st.error(f"Path not found: {other}")

    # ---- options
    st.subheader("2. Options")
    o = dict(saved)
    with st.expander("Analysis parameters", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        o["stim"] = c1.number_input("Stim 1 timepoint (--stim)", min_value=1, value=int(saved["stim"]),
                                    help="Fallback stimulus timepoint index used when it cannot be detected from indexfile.txt.")
        o["baseline_n"] = c2.number_input("Baseline timepoints (--baseline-n)", min_value=1, value=int(saved["baseline_n"]),
                                          help="Last N timepoints before Stim 1 used to compute F0.")
        o["expected_n"] = c3.number_input("Expected timepoints (--expected-n)", min_value=1, value=int(saved["expected_n"]))
        o["min_n"] = c4.number_input("Minimum timepoints (--min-n, 0 = off)", min_value=0, value=int(saved["min_n"]),
                                     help="Relaxed check: keep objects with at least this many timepoints. Overrides expected timepoints.")
        c1, c2 = st.columns(2)
        o["ymin"] = c1.number_input("Plot y-axis minimum", value=float(saved["ymin"]), step=0.5)
        o["ymax"] = c2.number_input("Plot y-axis maximum", value=float(saved["ymax"]), step=0.5)

    with st.expander("Plots"):
        c1, c2 = st.columns(2)
        o["per_page"] = c1.number_input("Traces per facet page (--per-page)", min_value=1, value=int(saved["per_page"]))
        o["pptx"] = c2.checkbox("Build PowerPoint of facet pages", value=bool(saved["pptx"]))
        o["ff0_no_plots"] = st.checkbox("Skip all per-object plots (faster)", value=bool(saved["ff0_no_plots"]))
        o["ff0_no_single_pngs"] = st.checkbox("Skip individual object PNGs", value=bool(saved["ff0_no_single_pngs"]))
        o["ff0_no_iqr_filter"] = st.checkbox("Disable IQR size/intensity pre-filter", value=bool(saved["ff0_no_iqr_filter"]))

    with st.expander("Steps to run"):
        c1, c2 = st.columns(2)
        o["no_phenix"] = c1.checkbox("Skip Excel generation (--no-phenix)", value=bool(saved["no_phenix"]),
                                     help="Use when per-well workbooks already exist.")
        o["no_ff0"] = c1.checkbox("Skip F/F0 analysis (--no-ff0)", value=bool(saved["no_ff0"]))
        o["no_spaghetti"] = c2.checkbox("Skip spaghetti plots (--no-spaghetti)", value=bool(saved["no_spaghetti"]))
        o["no_combine"] = c2.checkbox("Skip date summaries (--no-combine-date-summaries)", value=bool(saved["no_combine"]))

    with st.expander("Background correction"):
        o["compute_backgrounds"] = st.checkbox("Compute FOV backgrounds from raw images (--compute-backgrounds)",
                                               value=bool(saved["compute_backgrounds"]))
        c1, c2 = st.columns([3, 1])
        o["raw_data_root"] = c1.text_input("Raw image root (contains 20YY/MMDDYY folders)", value=saved["raw_data_root"],
                                           disabled=not o["compute_backgrounds"])
        o["background_timepoints"] = c2.number_input("Timepoints per phase", min_value=1,
                                                     value=int(saved["background_timepoints"]),
                                                     disabled=not o["compute_backgrounds"])
        o["background_channel"] = st.text_input("Channel name filter (e.g. 'Alexa 488')", value=saved["background_channel"],
                                                disabled=not o["compute_backgrounds"])
        o["background_csv"] = st.text_input("Existing background CSV (skips computation)", value=saved["background_csv"])
        if o["compute_backgrounds"] and not o["raw_data_root"].strip():
            st.warning("A raw image root is required to compute backgrounds.")

    with st.expander("Advanced"):
        c1, c2, c3 = st.columns(3)
        o["objects_population"] = c1.text_input("Harmony population (substring)", value=saved["objects_population"],
                                                help="Only needed when an Evaluation folder holds several Objects_Population exports.")
        o["threads"] = c2.number_input("Threads", min_value=1, max_value=64, value=int(saved["threads"]))
        o["timeout"] = c3.number_input("Per-step timeout (s)", min_value=60, value=int(saved["timeout"]), step=600)
        c1, c2, c3 = st.columns(3)
        o["verbose"] = c1.checkbox("Verbose", value=bool(saved["verbose"]))
        o["strict_phenix"] = c2.checkbox("Strict Phenix (fail if no workbook)", value=bool(saved["strict_phenix"]))
        o["dry_run"] = c3.checkbox("Dry run (show commands only)", value=bool(saved["dry_run"]))
        o["extra_args"] = st.text_input("Extra pt-run arguments", value=saved["extra_args"],
                                        placeholder="--pipeline-log C:\\logs\\run.log")

    # ---- command + run
    st.subheader("3. Run")
    cmd = build_pipeline_cmd(selected_paths or ["<date folder>"], o)
    st.code(shlex.join(cmd), language="bash")
    job = current_job()
    disabled = (not selected_paths) or (job is not None and job.running)
    if not selected_paths:
        st.caption("Select at least one date folder to enable the run button.")
    if st.button("▶ Run pipeline", type="primary", disabled=disabled):
        remember(pipeline_options=o, last_dates=[Path(p).name for p in selected_paths])
        start_job(cmd, label="Pipeline", cwd=str(root) if root and root.is_dir() else None,
                  context={"paths": selected_paths})
        st.rerun()

    render_job_panel()
    job = current_job()
    if job and not job.running and job.label == "Pipeline" and job.context.get("paths"):
        st.subheader("Outputs")
        for p in job.context["paths"]:
            pp = Path(p)
            date_dir = pp if DATE_RE.match(pp.name) else next((a for a in pp.parents if DATE_RE.match(a.name)), pp)
            logs = sorted(date_dir.glob("run_pt_pipeline_*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
            n_filtered = len(list(date_dir.rglob("*_filtered_objects.xlsx")))
            n_png = sum(1 for f in date_dir.rglob("*.png"))
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"**{date_dir.name}** — {n_filtered} filtered workbook(s), {n_png} plot image(s)"
                        + (f", log: `{logs[0].name}`" if logs else ""))
            if c2.button("Open folder", key=f"open_{date_dir}"):
                open_in_file_manager(date_dir)
        st.caption("Browse plots and workbooks in the **Results** section.")


# ----------------------------------------------------------------------------
# Page: Results
# ----------------------------------------------------------------------------
def page_results() -> None:
    st.title("Results")
    s = settings()
    root_text = st.text_input("Analyzed data root", value=s.get("analyzed_root", ""))
    root = path_or_none(root_text)
    if not (root and root.is_dir()):
        st.info("Enter the folder that contains your date folders.")
        return
    if s.get("analyzed_root") != str(root):
        remember(analyzed_root=str(root))
    dates = [d for d in sorted(root.iterdir()) if d.is_dir() and DATE_RE.match(d.name)]
    if not dates:
        st.warning("No date folders found.")
        return
    c1, c2, c3 = st.columns(3)
    date_dir = c1.selectbox("Date", dates, format_func=lambda p: p.name, index=len(dates) - 1)
    exps = list_experiments(date_dir)
    exp_dir = c2.selectbox("Experiment", exps, format_func=lambda p: p.name) if exps else None
    evals = list_evaluations(exp_dir) if exp_dir else []
    eval_dir = c3.selectbox("Evaluation", evals, format_func=lambda p: p.name) if evals else None

    tab_plots, tab_books, tab_logs = st.tabs(["Plots", "Workbooks", "Logs"])

    with tab_plots:
        if eval_dir is None:
            st.info("No Experiment_*/Evaluation* folders under this date.")
        else:
            plots_root = eval_dir / "plots"
            images = [p for p in plots_root.rglob("*") if p.suffix.lower() in IMG_EXTS] if plots_root.is_dir() else []
            if not images:
                st.info("No plot images yet for this evaluation. Run the pipeline first.")
            else:
                groups: dict[str, list[Path]] = {}
                for img in images:
                    rel = str(img.parent.relative_to(plots_root)) or "."
                    groups.setdefault(rel, []).append(img)
                names = sorted(groups)
                friendly = {
                    "raw": "raw — spaghetti plots, all objects",
                    "included": "included — spaghetti plots, QC-passed objects",
                    "individual": "individual — per-object facet pages",
                }
                c1, c2, c3 = st.columns([3, 2, 1])
                grp = c1.selectbox("Plot folder", names,
                                   format_func=lambda n: f"{friendly.get(n.split('/')[0].split(os.sep)[0], n)}  ({len(groups[n])})" if n in friendly else f"{n}  ({len(groups[n])})")
                flt = c2.text_input("Filter by well or name", value="", placeholder="e.g. B3")
                per_page = 12
                imgs = sorted(p for p in groups[grp] if flt.lower() in p.name.lower())
                n_pages = max(1, (len(imgs) + per_page - 1) // per_page)
                page = c3.number_input("Page", min_value=1, max_value=n_pages, value=1)
                st.caption(f"{len(imgs)} image(s) — page {page} of {n_pages}")
                chunk = imgs[(page - 1) * per_page: page * per_page]
                cols = st.columns(3)
                for i, img in enumerate(chunk):
                    with cols[i % 3]:
                        st.image(str(img), caption=img.name, width="stretch")
                if st.button("Open plots folder"):
                    open_in_file_manager(plots_root)

    with tab_books:
        books: list[Path] = []
        if eval_dir is not None:
            books += [p for p in eval_dir.rglob("*.xlsx") if not p.name.startswith("~$")]
        analysis = date_dir / "Analysis"
        if analysis.is_dir():
            books += [p for p in analysis.glob("*.xlsx") if not p.name.startswith("~$")]
        books += [p for p in date_dir.glob("*.xlsx") if not p.name.startswith("~$")]
        books = sorted(set(books), key=lambda p: (p.parent != date_dir, p.parent != analysis, p.name))
        if not books:
            st.info("No workbooks found for this selection.")
        else:
            book = st.selectbox("Workbook", books, format_func=lambda p: str(p.relative_to(date_dir)))
            try:
                xl = pd.ExcelFile(book)
                sheet = st.selectbox("Sheet", xl.sheet_names)
                df = xl.parse(sheet, nrows=5000)
                st.caption(f"{len(df)} row(s) shown (max 5000) × {df.shape[1]} column(s)")
                st.dataframe(df, width="stretch", hide_index=True)
                st.download_button("Download workbook", data=book.read_bytes(), file_name=book.name,
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            except Exception as exc:
                st.error(f"Could not read {book.name}: {exc}")

    with tab_logs:
        logs = sorted(date_dir.rglob("run_pt_pipeline_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not logs:
            st.info("No pipeline logs in this date folder.")
        else:
            log = st.selectbox("Log file", logs, format_func=lambda p: f"{p.name}  ({datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M})")
            text = log.read_text(encoding="utf-8", errors="replace")
            errors = [ln for ln in text.splitlines() if "[ERROR]" in ln or "Traceback" in ln]
            if errors:
                st.error(f"{len(errors)} error line(s):\n\n" + "\n".join(errors[:20]))
            with st.container(height=420):
                st.code(text[-60000:], language="text")


# ----------------------------------------------------------------------------
# Page: Reorganize raw data
# ----------------------------------------------------------------------------
def page_reorg() -> None:
    st.title("Reorganize raw Harmony exports")
    st.markdown(
        "Renames `<name>__<timestamp>-Measurement N` folders to `Experiment_<MMDDYY>_<n>`, attaches ROI image "
        "folders via `indexfile.txt`, and splits image folders into per-well bins. "
        "**Runs as a dry run unless *Apply changes* is ticked.**"
    )
    s = settings()
    root_text = st.text_input("Data root (contains MMDDYY date folders)", value=s.get("analyzed_root", ""))
    root = path_or_none(root_text)
    rows = scan_root(str(root)) if root and root.is_dir() else []
    all_dates = st.checkbox("All date folders", value=True)
    dates: list[str] = []
    if not all_dates:
        dates = st.multiselect("Date folders", [r["date"] for r in rows])
    c1, c2, c3 = st.columns(3)
    apply = c1.checkbox("Apply changes (otherwise dry run)", value=False)
    overwrite = c2.checkbox("Allow overwriting existing targets", value=False)
    mirror = c3.checkbox("Also rename experiments at date root (--mirror-root)", value=False)
    c1, c2, c3 = st.columns(3)
    roi_mode = c1.selectbox("Per-well folder style (--roi-mode)", ["sibling", "subdir"])
    well_source = c2.selectbox("Well assignment (--well-source)", ["filename", "indexfile"])
    fov = c3.checkbox("FOV sub-folders (--fov-subdirs)", value=False)
    consolidate = st.checkbox("Consolidate ROI sibling folders (--consolidate-roi-siblings)", value=False)
    no_scan = st.checkbox("Do not scan all image directories (--no-scan-all-img-dirs)", value=False)

    cmd = [sys.executable, "-m", "pt.phenix_reorg", "--root", str(root) if root else "<root>"]
    cmd += ["--all-dates"] if all_dates else ["--dates", *dates]
    if apply:
        cmd.append("--apply")
    if overwrite:
        cmd.append("--overwrite")
    if mirror:
        cmd.append("--mirror-root")
    cmd += ["--roi-mode", roi_mode, "--well-source", well_source]
    if fov:
        cmd.append("--fov-subdirs")
    if consolidate:
        cmd.append("--consolidate-roi-siblings")
    if no_scan:
        cmd.append("--no-scan-all-img-dirs")
    st.code(shlex.join(cmd), language="bash")
    job = current_job()
    ok = bool(root and root.is_dir()) and (all_dates or dates)
    if apply:
        st.warning("Apply is ticked: folders and files will be moved/renamed. Run a dry run first and read the plan.")
    if st.button("▶ Run reorganization", type="primary", disabled=(not ok) or (job is not None and job.running)):
        remember(analyzed_root=str(root))
        start_job(cmd, label="Reorganize", cwd=str(root))
        st.rerun()
    render_job_panel()


# ----------------------------------------------------------------------------
# Page: Sample manifest
# ----------------------------------------------------------------------------
def page_manifest() -> None:
    st.title("Build a sample manifest")
    st.markdown("Combines plate-layout workbooks with pipeline outputs into one manifest workbook "
                "(one row per well) with scorability and Stim 1 positivity flags.")
    s = settings()
    layout = st.text_input("Plate layout folder (--layout-root)", value=s.get("layout_root", ""))
    analysis = st.text_input("Analyzed data root(s) (--analysis-root; separate several with ;)",
                             value=s.get("analyzed_root", ""))
    supp = st.text_input("Supplemental layout folder (optional)", value=s.get("supplemental_root", ""))
    c1, c2, c3 = st.columns(3)
    output = c1.text_input("Output workbook", value=s.get("manifest_output", "sample_manifest.xlsx"))
    cell_types = c2.text_input("Cell-type vocabulary (optional, comma-separated)", value=s.get("cell_types", ""))
    protocol = c3.text_input("Protocol version label", value=s.get("protocol_version", "1"))
    roots = [r.strip() for r in analysis.split(";") if r.strip()]
    cmd = [sys.executable, "-m", "pt.build_plate_manifest", "--layout-root", layout or "<layout root>",
           "--analysis-root", *(roots or ["<analysis root>"]), "--output", output]
    if supp.strip():
        cmd += ["--supplemental-layout-root", supp.strip()]
    if cell_types.strip():
        cmd += ["--cell-types", cell_types.strip()]
    if protocol.strip():
        cmd += ["--protocol-version", protocol.strip()]
    st.code(shlex.join(cmd), language="bash")
    job = current_job()
    ok = bool(layout.strip()) and bool(roots)
    if st.button("▶ Build manifest", type="primary", disabled=(not ok) or (job is not None and job.running)):
        remember(layout_root=layout, supplemental_root=supp, manifest_output=output,
                 cell_types=cell_types, protocol_version=protocol)
        start_job(cmd, label="Sample manifest", cwd=str(Path(layout).parent) if Path(layout).exists() else None)
        st.rerun()
    render_job_panel()


# ----------------------------------------------------------------------------
# Page: Plate overview
# ----------------------------------------------------------------------------
def page_overview() -> None:
    st.title("Plate overview sheet")
    st.markdown("Adds a color-coded 96-well *Overview* sheet (and a flat *Overview_Data* table) to a copy of a "
                "plate-layout workbook.")
    xlsx = st.text_input("Plate layout workbook (.xlsx)", value="")
    out = st.text_input("Output workbook (optional; default <input>_with_overview.xlsx)", value="")
    cmd = [sys.executable, "-m", "pt.build_plate_overview", xlsx or "<layout.xlsx>"]
    if out.strip():
        cmd += ["-o", out.strip()]
    st.code(shlex.join(cmd), language="bash")
    job = current_job()
    p = path_or_none(xlsx)
    ok = bool(p and p.is_file())
    if xlsx and not ok:
        st.error("Workbook not found.")
    if st.button("▶ Build overview", type="primary", disabled=(not ok) or (job is not None and job.running)):
        start_job(cmd, label="Plate overview", cwd=str(p.parent))
        st.rerun()
    render_job_panel()


# ----------------------------------------------------------------------------
# Page: Help
# ----------------------------------------------------------------------------
def page_help() -> None:
    st.title("Help")
    st.markdown(
        """
**Typical workflow**

1. Export from Harmony: for each evaluation, a `PlateResults.txt` and an `Objects_Population - <name>.txt`.
2. If the export folders are not yet named `Experiment_<MMDDYY>_<n>`, use **Reorganize** (dry run first).
3. In **Run pipeline**, enter the folder that contains your `MMDDYY` date folders, tick the dates, and press *Run*.
4. Watch the log. When it finishes, open **Results** to browse spaghetti plots, facet pages and workbooks.
5. Optional: build a **Sample manifest** from plate-layout workbooks, or a **Plate overview** sheet.

**Where things are written**

| Output | Location |
|---|---|
| Per-well workbook | `<date>/Analysis/Experiment_*/Evaluation*/…_per-well.xlsx` |
| Augmented and filtered workbooks | `…/Evaluation*/plots/individual/` |
| Spaghetti plots | `…/Evaluation*/plots/raw/` and `…/plots/included/` |
| Date-level summaries | `<date>/Analysis/*.xlsx` |
| Pipeline log | `<date>/run_pt_pipeline_<timestamp>.log` |

**Tips**

- Every page shows the exact command it runs, so anything you do here can be scripted later.
- Settings (folders, last options) are remembered per user in `~/.pthts/gui_settings.json`.
- Data on network or cloud-synced drives is slow; copy a date folder locally for large runs.
- The full option reference is in `docs/cli_reference.md` of the repository.
        """
    )


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🧫", layout="wide")
    with st.sidebar:
        st.markdown(f"## 🧫 {APP_TITLE}")
        st.caption("PlateTectonics high-throughput screening pipeline")
        page = st.radio(
            "Section",
            ["Run pipeline", "Results", "Reorganize raw data", "Sample manifest", "Plate overview", "Help"],
            label_visibility="collapsed",
        )
        job = current_job()
        if job is not None:
            if job.running:
                st.info(f"{job.label} running…", icon="⏳")
            elif job.rc == 0 and not job.stopped:
                st.success(f"{job.label} finished", icon="✅")
            else:
                st.error(f"{job.label} {'stopped' if job.stopped else 'failed'}", icon="❌")
        st.divider()
        st.caption(f"Python: `{sys.executable}`")
        st.caption(f"Settings: `{SETTINGS_PATH}`")

    {
        "Run pipeline": page_run,
        "Results": page_results,
        "Reorganize raw data": page_reorg,
        "Sample manifest": page_manifest,
        "Plate overview": page_overview,
        "Help": page_help,
    }[page]()


main()
