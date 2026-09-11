#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, os, sys, shlex, shutil, re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
from threading import Lock
from datetime import datetime
import pandas as pd

# ---------------- small utils ----------------
EXP_RE = re.compile(r"^Experiment_\d{6}_\d+$", re.I)  # Experiment_082125_1
EVAL_RE = re.compile(r"^Evaluation[ _]?\d+$", re.I)  # Evaluation1 / Evaluation_1

RUN_LOG_PATH: Path | None = None
RUN_LOG_LOCK = Lock()


def _append_pipeline_log(message: str) -> None:
    global RUN_LOG_PATH
    if RUN_LOG_PATH is None:
        return
    try:
        with RUN_LOG_LOCK:
            RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with RUN_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(f"{message}\n")
    except Exception:
        pass


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)
    sep = k.get("sep", " ")
    msg = sep.join(str(x) for x in a)
    if RUN_LOG_PATH is None:
        # Log not yet finalised — messages are buffered by _finalise_log caller
        pass
    else:
        _append_pipeline_log(msg)


def norm(p: str) -> Path: return Path(p).expanduser().resolve()


def extract_context_from_path(path: Path) -> tuple[str, str, str]:
    """
    Extract (date6, ex_num, ev_num) from the path.
    - date: first folder name that is exactly 6 digits (e.g., 081825)
    - ex:   'Experiment_<date>_<num>' or 'Experiment_<num>'
    - ev:   'Evaluation<num>'
    Returns (date or 'NA', ex or 'NA', ev or 'NA').
    """
    parts = [p for p in path.parts]
    date6 = "NA"
    ex_num = "NA"
    ev_num = "NA"

    for p in parts:
        m = re.fullmatch(r"\d{6}", str(p))
        if m:
            date6 = m.group(0)
            break

    for p in parts:
        m = re.match(rf"Experiment_(?:{date6}_)?(\d+)$", str(p))
        if m:
            ex_num = m.group(1)
            break

    for p in parts:
        m = re.match(r"Evaluation(\d+)$", str(p))
        if m:
            ev_num = m.group(1)
            break

    return date6, ex_num, ev_num


def prefer_filtered_xlsx(xlsx_path: Path) -> Path:
    if xlsx_path.name.endswith("_filtered_objects.xlsx"):
        return xlsx_path
    date6, ex_num, ev_num = extract_context_from_path(xlsx_path)
    if "NA" in (date6, ex_num, ev_num):
        return xlsx_path
    candidate = xlsx_path.parent / f"Experiment_{date6}_{ex_num}_Evaluation{ev_num}_filtered_objects.xlsx"
    if candidate.exists():
        eprint(f"[info] Using included workbook: {candidate}")
        return candidate
    return xlsx_path


def infer_date_groups_from_experiments(exps: list[Path]) -> dict[Path, set[str]]:
    """
    Infer date folders from discovered Experiment paths.
    Returns mapping: {root_containing_dates: {date6, ...}}.
    """
    groups: dict[Path, set[str]] = {}
    for exp in exps:
        for i, part in enumerate(exp.parts):
            if not re.fullmatch(r"\d{6}", str(part)):
                continue
            date_dir = Path(*exp.parts[: i + 1])
            root_dir = date_dir.parent
            groups.setdefault(root_dir, set()).add(str(part))
            break
    return groups


def is_experiment_dir(p: Path) -> bool:
    return p.is_dir() and EXP_RE.match(p.name) is not None


def is_eval_dir(p: Path) -> bool:
    return p.is_dir() and EVAL_RE.match(p.name) is not None


def find_experiments(root: Path):
    """Return only real experiment dirs (ignore *_ROI_Images)."""
    if is_experiment_dir(root):
        return [root]
    cands = []
    if root.is_dir():
        # Try direct Analysis path first (most common case)
        analysis = root / "Analysis"
        if analysis.is_dir():
            eprint(f"[find_experiments] Checking {analysis}...")
            try:
                cands.extend([p for p in analysis.iterdir() if is_experiment_dir(p)])
            except Exception as e:
                eprint(f"[find_experiments][WARN] Error reading {analysis}: {e}")

        # If root is Analysis, check directly
        if root.name == "Analysis":
            eprint(f"[find_experiments] Checking {root} directly...")
            try:
                cands.extend([p for p in root.iterdir() if is_experiment_dir(p)])
            except Exception as e:
                eprint(f"[find_experiments][WARN] Error reading {root}: {e}")

        # Recursive fallback (SLOW on cloud sync - only if needed)
        if not cands:
            eprint(
                f"[find_experiments] No experiments found in direct paths, trying recursive search (this may be slow on cloud sync folders)...")
            try:
                # Limit depth to avoid extremely slow searches
                for p in root.glob("**/Analysis/Experiment_*"):
                    if is_experiment_dir(p):
                        cands.append(p)
                        # Early exit if we found some
                        if len(cands) > 0:
                            break
            except Exception as e:
                eprint(f"[find_experiments][WARN] Recursive search failed: {e}")

    seen, out = set(), []
    for p in sorted(cands):
        if p not in seen:
            seen.add(p);
            out.append(p)
    return out


def find_evaluations(exp_dir: Path):
    """Find evaluation directories. May be slow on cloud sync folders."""
    try:
        evals = [p for p in exp_dir.iterdir() if is_eval_dir(p)]
        return sorted(evals)
    except Exception as e:
        eprint(f"[find_evaluations][WARN] Error reading {exp_dir}: {e}")
        return []


def run(cmd, dry_run=False, timeout=7200):
    """
    Run a command with timeout and real-time output streaming.
    Default timeout is 2 hours (7200 seconds) for Box sync folder operations.
    Output is streamed in real-time so progress is visible.
    """
    eprint("CMD:", " ".join(shlex.quote(str(c)) for c in cmd))
    if dry_run: return 0
    try:
        # Use Popen for real-time output streaming
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1  # Line buffered
        )

        # Stream output line by line in real-time
        output_lines = []
        for line in iter(process.stdout.readline, ''):
            if line:
                line = line.rstrip()
                eprint(line)  # Print immediately
                output_lines.append(line)

        # Wait for process to complete with timeout
        try:
            returncode = process.wait(timeout=timeout)
            return returncode
        except subprocess.TimeoutExpired:
            eprint(f"\n[ERROR] Command timed out after {timeout} seconds (2 hours)")
            eprint("[INFO] This is likely due to slow file I/O on Box sync folders.")
            eprint("[INFO] Consider copying data to a local folder for faster processing.")
            process.kill()
            process.wait()
            return 1

    except Exception as e:
        eprint(f"[ERROR] Command failed: {e}")
        return 1


def build_script_path(scripts_dir: Path | None, script_name: str) -> str:
    return str((scripts_dir / script_name).resolve()) if scripts_dir else script_name


def base_plots_dir(xlsx: Path) -> Path:
    return xlsx.parent / "plots"


def combined_traces_dir_for(xlsx: Path) -> Path:  return base_plots_dir(xlsx) / "spaghetti"


def mean_traces_dir_for(xlsx: Path) -> Path:      return base_plots_dir(xlsx) / "mean"


def individual_traces_dir_for(xlsx: Path) -> Path: return base_plots_dir(xlsx) / "individual"


def snapshot_files(root: Path) -> set[Path]:
    if not root.exists(): return set()
    return {p for p in root.rglob("*") if p.is_file()}


def safe_move_to_dir(files: set[Path], dest: Path):
    dest.mkdir(parents=True, exist_ok=True)
    for p in sorted(files):
        if dest in p.parents: continue
        target = dest / p.name
        if target.exists():
            stem, suf = p.stem, p.suffix;
            i = 2
            while (dest / f"{stem}_{i}{suf}").exists(): i += 1
            target = dest / f"{stem}_{i}{suf}"
        try:
            p.rename(target)
        except Exception:
            shutil.copy2(p, target)
            try:
                p.unlink()
            except Exception:
                pass


def run_phenix_for_context(target: Path, python_bin: str, scripts_dir: Path | None, dry_run: bool,
                           timeout: int = 7200) -> int:
    # Was: script = "-m phenix_to_xlsx_batch"; run([python_bin, script, str(target)], dry_run)
    return run_module_or_script(python_bin, "pt.phenix_to_xlsx_batch", [target], dry_run, scripts_dir,
                                timeout=timeout)


# ---------------- per-well XLSX discovery ----------------
def pick_per_well_xlsx(eval_dir: Path) -> Path | None:
    files = list(eval_dir.glob("*.xlsx"))
    if not files: return None

    def score(p: Path) -> tuple:
        name = p.name.lower()
        has_op = "operaphenix" in name
        has_pw = ("per-well" in name) or ("per_well" in name) or ("perwell" in name)
        return (has_pw, has_op, -len(name))

    files.sort(key=score, reverse=True)
    top = files[0]
    if score(top)[0] or ("per" in top.name.lower() and "well" in top.name.lower()):
        return top
    return None


def has_raw_eval_txts(eval_dir: Path) -> bool:
    pr = list(eval_dir.glob("PlateResults*.txt"))
    obj = list(eval_dir.glob("Objects_Population*.txt"))
    return bool(pr and obj)


def expected_per_well_path(eval_dir: Path) -> Path:
    return eval_dir / f"{eval_dir.parent.name}_{eval_dir.name}_per-well.xlsx"


# ---------------- fallback XLSX builder ----------------
ROW_LETTERS = "ABCDEFGH"


def _row_to_letter(val):
    """Accept 1-based numeric rows or already-letter rows; return A..P etc."""
    s = str(val).strip()
    if s.isdigit():
        i = int(s)
        if 1 <= i <= len(ROW_LETTERS):
            return ROW_LETTERS[i - 1]
    # if already a letter like 'B'
    if len(s) == 1 and s.upper() in ROW_LETTERS:
        return s.upper()
    # last resort: treat as is
    return s


def _sanitize_sheet_name(name: str) -> str:
    # Excel sheet names: max 31 chars; strip illegal: : \ / ? * [ ]
    bad = r'[]:*?/\\'
    out = "".join(c for c in name if c not in bad)
    return out[:31] if len(out) > 31 else out


# ------- robust text parsing (shared with phenix_to_xlsx_batch) -------
import statistics as stats

ENCODINGS = ["utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be"]
DELIMS = ["\t", ",", ";"]


def sniff_delim(lines):
    nonempty = [ln for ln in lines if ln and ln.strip()]
    if not nonempty: return "\t"
    head = nonempty[:200]
    scores = {}
    for d in DELIMS:
        per = [ln.count(d) for ln in head]
        scores[d] = float(stats.median(per)) if per else 0.0
    return max(scores, key=scores.get)


def find_header_after_data_tag(lines, delim):
    for i, ln in enumerate(lines[:100]):
        if ln.strip().strip('"') == "[Data]":
            return i + 1
    for i, ln in enumerate(lines[:100]):
        cols = [c.strip() for c in ln.split(delim)]
        if "Row" in cols and "Column" in cols:
            return i
    return 0


def normalize_headers(cols):
    out = []
    for c in cols:
        c = str(c).replace("-¬-İ", "-İ")
        c = re.sub(r"\s+", " ", c).strip()
        out.append(c)
    return out


def read_table_robust(path: Path) -> pd.DataFrame:
    for enc in ENCODINGS:
        try:
            text = path.read_text(encoding=enc, errors="replace")
            lines = text.splitlines()
            delim = sniff_delim(lines)
            hdr = find_header_after_data_tag(lines, delim)
            df = pd.read_csv(path, sep=delim, encoding=enc, engine="python",
                             skiprows=hdr, header=0, dtype=str)
            df.columns = normalize_headers(df.columns)
            return df
        except Exception:
            continue
    raise RuntimeError(f"Could not read {path.name} with supported encodings.")


def build_per_well_xlsx_fallback(eval_dir: Path) -> Path | None:
    eprint(f"[fallback-xlsx] Building workbook directly from raw .txt in {eval_dir} ...")
    # Harmony names the export "Objects_Population - <population>.txt"; the population name is
    # user-defined, so match generically and let PT_OBJECTS_POPULATION disambiguate if needed.
    hits = sorted(eval_dir.glob("Objects_Population*.txt"))
    pop = os.environ.get("PT_OBJECTS_POPULATION")
    if pop:
        hits = [h for h in hits if pop.lower() in h.name.lower()] or hits
    obj = hits[0] if hits else None
    if obj is None:
        eprint(f"[fallback-xlsx][WARN] Objects file not found in {eval_dir}")
        return None

    try:
        df = read_table_robust(obj)
    except Exception as e:
        eprint(f"[fallback-xlsx][ERROR] Could not parse {obj.name}: {e}")
        return None

    # add Well from Row/Column if possible
    row_col = "Row" if "Row" in df.columns else None
    col_col = "Column" if "Column" in df.columns else None
    if row_col and col_col:
        rows = df[row_col].map(_row_to_letter)
        try:
            cols = df[col_col].astype(int).astype(str)
        except Exception:
            cols = df[col_col].astype(str).str.extract(r"(\d+)")[0].fillna(df[col_col].astype(str))
        df["Well"] = rows.fillna("") + cols.fillna("")

    xlsx = eval_dir / f"{eval_dir.name}_per-well.xlsx"
    try:
        with pd.ExcelWriter(xlsx) as xw:
            if "Well" in df.columns:
                def sort_key(w):
                    m = re.match(r"([A-Za-z]+)(\d+)", str(w))
                    if not m: return (str(w), 0)
                    return (m.group(1).upper(), int(m.group(2)))

                wells = sorted(df["Well"].dropna().unique(), key=sort_key)
                for w in wells:
                    sub = df[df["Well"] == w]
                    sub.to_excel(xw, sheet_name=str(w)[:31], index=False)
                df.to_excel(xw, sheet_name="all", index=False)
            else:
                df.to_excel(xw, sheet_name="all", index=False)
    except Exception as e:
        eprint(f"[fallback-xlsx][ERROR] Could not write {xlsx.name}: {e}")
        return None

    eprint(f"[fallback-xlsx] Wrote {xlsx}")
    return xlsx


# ---------------- phenix helpers ----------------
# Note: run() function is defined earlier in the file

def _module_to_path(scripts_dir: Path | None, module: str) -> Path | None:
    """
    If scripts_dir has a .py file matching the module (e.g., 'pt.spaghetti_plot_per_well' ->
    scripts_dir/'src/pt/spaghetti_plot_per_well.py'), return that path; else None.
    """
    if not scripts_dir:
        return None
    rel = Path(*module.split("."))  # e.g., pt/spaghetti_plot_per_well
    for base in ("", "src", "python", "code"):  # common project roots
        cand = (scripts_dir / base / rel).with_suffix(".py")
        if cand.exists():
            return cand
    return None


def run_module_or_script(python_bin: str, module: str, args: list[str | Path], dry_run: bool,
                         scripts_dir: Path | None = None, timeout: int = 7200) -> int:
    """
    Try 'python -m module args'. If the module file exists under scripts_dir, run that file instead.
    """
    mod_path = _module_to_path(scripts_dir, module)
    if mod_path is not None:
        cmd = [python_bin, str(mod_path), *map(str, args)]
    else:
        cmd = [python_bin, "-m", module, *map(str, args)]
    return run(cmd, dry_run, timeout=timeout)


def ensure_xlsx_for_evaluation(eval_dir: Path, python_bin: str, scripts_dir: Path | None, dry_run: bool,
                               timeout: int = 7200) -> None:
    """
    If an evaluation has raw .txt files but no per-well workbook, try to generate one.
    Try roots in order: Experiment -> Analysis -> Date -> Evaluation; if all fail, build locally.
    """
    exp_dir = eval_dir.parent
    analysis = exp_dir.parent if exp_dir.parent.name == "Analysis" else None
    date_dir = analysis.parent if analysis else None

    tried = []
    for root in [exp_dir, analysis, date_dir, eval_dir]:
        if root is None:
            continue
        eprint(f"[phenix:eval] Attempting generation from root: {root}")
        rc = run_phenix_for_context(root, python_bin, scripts_dir, dry_run, timeout=timeout)
        tried.append((root, rc))
        if rc == 0:
            break

    # Re-check; if still missing, build locally from raw .txt
    if pick_per_well_xlsx(eval_dir) is None and has_raw_eval_txts(eval_dir):
        eprint("[phenix:eval] phenix route failed; switching to in-script builder...")
        built = build_per_well_xlsx_fallback(eval_dir)
        if built is None:
            eprint("[phenix:eval][WARN] Fallback builder could not produce a workbook.")
    elif pick_per_well_xlsx(eval_dir) is None:
        eprint("[phenix:eval][WARN] No raw .txt pair present; cannot build workbook.")

    # Ensure the standard Experiment_*_Evaluation*_per-well.xlsx name exists.
    expected = expected_per_well_path(eval_dir)
    if not expected.exists():
        candidates = [p for p in eval_dir.glob("*per-well*.xlsx") if not p.name.startswith("~$")]
        if candidates:
            source = sorted(candidates)[0]
            if not dry_run:
                try:
                    import shutil
                    shutil.copy2(source, expected)
                    eprint(f"[phenix:eval] Copied {source.name} -> {expected.name}")
                except Exception as e:
                    eprint(f"[phenix:eval][WARN] Could not copy {source.name} to {expected.name}: {e}")
            else:
                eprint(f"[phenix:eval] Would copy {source.name} -> {expected.name}")


# ---------------- FF0 & spaghetti ----------------

def run_ff0(xlsx: Path, python_bin: str, scripts_dir: Path | None,
            stim: int, baseline_n: int, ylim: list[float], expected_n: int,
            per_page: int, pptx: bool, dry_run: bool,
            ff0_no_plots: bool = False, ff0_no_single_pngs: bool = False,
            ff0_no_iqr_filter: bool = False,
            background_csv: Path | None = None, timeout: int = 7200,
            min_n: int | None = None) -> Path | None:
    """
    Run per_object_ff0 and post-stats inclusion, returning the included Excel file if created.

    Returns:
        Path to included responder Excel file ({outdir}/Experiment_<MMDDYY>_<n>_Evaluation<n>_filtered_objects.xlsx)
        or None if not created
    """
    args = [
        xlsx,
        "--stim",
        str(stim),
        "--baseline-n",
        str(baseline_n),
        "--ylim",
        str(ylim[0]),
        str(ylim[1]),
        "--per-page",
        str(per_page),
        "--no-plots",
    ]
    if pptx:
        args.append("--pptx")
    if ff0_no_iqr_filter:
        args.append("--no-iqr-filter")
    if background_csv and background_csv.exists():
        args.extend(["--background-csv", str(background_csv)])

    rc = run_module_or_script(python_bin, "pt.per_object_ff0", args, dry_run, scripts_dir, timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"per_object_ff0 failed for {xlsx} (rc={rc})")

    # Determine the expected responder Excel file path
    # per_object_ff0 writes to: {outdir}/{base}_with_obj.xlsx
    # where outdir defaults to {xlsx.parent}/plots/individual
    base = xlsx.stem
    outdir = xlsx.parent / "plots" / "individual"
    responder_xlsx = outdir / f"{base}_with_obj.xlsx"
    if not responder_xlsx.exists():
        return None

    # Run post-stats inclusion to create the included workbook
    filter_args = [str(responder_xlsx)]
    if min_n is not None:
        filter_args.extend(["--min-n", str(min_n)])
    else:
        filter_args.extend(["--expected-n", str(expected_n)])
    rc = run_module_or_script(
        python_bin,
        "pt.filter_post_stats",
        filter_args,
        dry_run,
        scripts_dir,
        timeout=timeout,
    )
    if rc != 0:
        raise RuntimeError(f"filter_post_stats failed for {responder_xlsx} (rc={rc})")

    filtered_xlsx = prefer_filtered_xlsx(responder_xlsx)
    if not ff0_no_plots:
        plot_targets = [(responder_xlsx, outdir / "raw")]
        if filtered_xlsx.exists() and filtered_xlsx != responder_xlsx:
            plot_targets.append((filtered_xlsx, outdir / "included"))
        for plot_xlsx, plot_outdir in plot_targets:
            plot_args = [
                str(plot_xlsx),
                "--outdir", str(plot_outdir),
                "--ylim", str(ylim[0]), str(ylim[1]),
                "--per-page", str(per_page),
            ]
            if pptx:
                plot_args.append("--pptx")
            if ff0_no_single_pngs:
                plot_args.append("--no-single-pngs")
            rc = run_module_or_script(python_bin, "pt.per_object_ff0_plots", plot_args,
                                      dry_run, scripts_dir, timeout=timeout)
            if rc != 0:
                raise RuntimeError(f"per_object_ff0_plots failed for {plot_xlsx} (rc={rc})")

    return filtered_xlsx if filtered_xlsx.exists() else responder_xlsx


def run_spaghetti(raw_xlsx: Path, filtered_xlsx: Path | None, python_bin: str, scripts_dir: Path | None,
                  mode: str, same_ylim: bool, facet: bool,
                  outdir: Path, dry_run: bool, background_csv: Path | None = None,
                  timeout: int = 7200, both: bool = False) -> None:
    def _run_one(xlsx_path: Path, out_path: Path) -> None:
        out_path.mkdir(parents=True, exist_ok=True)
        args = [xlsx_path, "--mode", mode, "--outdir", str(out_path)]
        if same_ylim:
            args.append("--same-ylim")
        if facet:
            args.append("--facet")
        if background_csv and background_csv.exists():
            args.extend(["--background-csv", str(background_csv)])
        rc = run_module_or_script(
            python_bin, "pt.spaghetti_plot_per_well", args, dry_run, scripts_dir, timeout=timeout
        )
        if rc != 0:
            raise RuntimeError(f"spaghetti_plot_per_well ({mode}) failed for {xlsx_path} (rc={rc})")

    if both:
        _run_one(raw_xlsx, outdir / "raw")
        if filtered_xlsx and filtered_xlsx.exists():
            _run_one(filtered_xlsx, outdir / "included")
        else:
            eprint("[spaghetti][WARN] Included workbook not found; skipping included plots.")
        return

    target = filtered_xlsx if (filtered_xlsx and filtered_xlsx.exists()) else raw_xlsx
    _run_one(target, outdir)


def run_responder_bar_charts(responder_xlsx: Path, python_bin: str, scripts_dir: Path | None,
                             dry_run: bool, timeout: int = 7200) -> None:
    """
    Run responder bar charts script on the responder Excel file.

    Args:
        responder_xlsx: Path to Excel file with Responder_Overview and Responder_Object_Level sheets
        python_bin: Python binary to use
        scripts_dir: Optional scripts directory
        dry_run: If True, don't actually run
        timeout: Timeout in seconds
    """
    def _has_required_sheets(path: Path) -> bool:
        try:
            xls = pd.ExcelFile(path)
            return "Responder_Overview" in xls.sheet_names and "Responder_Object_Level" in xls.sheet_names
        except Exception:
            return False

    original = responder_xlsx
    preferred = prefer_filtered_xlsx(responder_xlsx)
    candidates = []
    for cand in [preferred, original]:
        if cand and cand.exists() and cand not in candidates:
            candidates.append(cand)

    if not candidates:
        eprint(f"[responder_charts] Skipping: responder Excel file not found: {preferred}")
        return

    chosen = None
    for cand in candidates:
        if _has_required_sheets(cand):
            chosen = cand
            break

    if chosen is None:
        eprint(f"[responder_charts] Skipping: required sheets not found in any candidate for {original}")
        return
    if chosen != preferred:
        eprint(f"[responder_charts] Using fallback workbook with required sheets: {chosen}")

    args = [str(chosen)]
    rc = run_module_or_script(python_bin, "pt.responder_bar_charts", args, dry_run, scripts_dir, timeout=timeout)
    if rc != 0:
        eprint(f"[responder_charts][WARN] responder_bar_charts failed for {chosen} (rc={rc})")


def run_combine_date_summaries(root_dir: Path, dates: list[str], python_bin: str, scripts_dir: Path | None,
                               dry_run: bool, timeout: int = 7200) -> int:
    if not dates:
        eprint(f"[combine] Skipping: no dates provided for root {root_dir}")
        return 0
    args = [*dates, "--root", str(root_dir)]
    return run_module_or_script(
        python_bin,
        "pt.combine_date_summaries",
        args,
        dry_run,
        scripts_dir,
        timeout=timeout,
    )


# ---------------- planning ----------------

def plan_for_experiment(exp_dir: Path, args):
    """
    Build task list. If an Evaluation lacks per-well XLSX but has the two raw .txt files,
    attempt to generate the workbook (phenix -> fallback builder) before downstream tasks.
    """
    tasks = []
    if not args.no_phenix:
        tasks.append(("phenix_auto", (exp_dir, args.python_bin, args.scripts_dir, args.dry_run, args.strict_phenix)))

    xlsxs = []
    for ed in find_evaluations(exp_dir):
        x = pick_per_well_xlsx(ed)
        if x is None and has_raw_eval_txts(ed) and not args.no_phenix:
            eprint(f"[discover] {ed.name}: raw .txt present but no per-well XLSX -> trying to generate")
            ensure_xlsx_for_evaluation(ed, args.python_bin, args.scripts_dir, args.dry_run,
                                       timeout=getattr(args, 'timeout', 7200))
            x = pick_per_well_xlsx(ed)
        if x is None:
            eprint(
                f"[discover][WARN] {ed.name}: no per-well XLSX found; skipping downstream steps for this evaluation.")
        else:
            eprint(f"[discover] {ed.name}: using {x.name}")
            xlsxs.append(x)

    if not xlsxs:
        eprint(f"[WARN] No per-well XLSX found under {exp_dir}.")
        return tasks

    timeout = getattr(args, 'timeout', 7200)
    background_csv = getattr(args, 'background_csv_path', None)
    for x in xlsxs:
        if not args.no_ff0:
            tasks.append(("ff0", (x, args.python_bin, args.scripts_dir,
                                  args.stim, args.baseline_n, args.ylim, args.expected_n,
                                  args.per_page, args.pptx, args.dry_run,
                                  args.ff0_no_plots, args.ff0_no_single_pngs,
                                  args.ff0_no_iqr_filter,
                                  background_csv, timeout,
                                  getattr(args, 'min_n', None))))
        if not args.no_spaghetti:
            tasks.append(("spaghetti", (x, args.python_bin, args.scripts_dir,
                                        "per-object", True, True,
                                        combined_traces_dir_for(x), args.dry_run, background_csv, timeout,
                                        args.spaghetti_both)))
            tasks.append(("spaghetti", (x, args.python_bin, args.scripts_dir,
                                        "mean", True, True,
                                        mean_traces_dir_for(x), args.dry_run, background_csv, timeout,
                                        args.spaghetti_both)))
    return tasks


# ---------------- CLI / main ----------------

def main():
    ap = argparse.ArgumentParser(
        description="Run phenix -> xlsx, FF0, and spaghetti plotting across one or more Experiments.")
    ap.add_argument("paths", nargs="+",
                    help="Experiment_* dir(s), an Analysis directory, or a date folder containing Analysis/Experiment_*")
    ap.add_argument("--python-bin", default="python3")
    ap.add_argument("--root", default=".", help="Path to the data root (default: .)")
    ap.add_argument("--dates", nargs="*", help="Specific date folders to process (e.g., 081825 082125)")
    ap.add_argument("--scripts-dir", type=Path, default=None,
                    help="Directory containing phenix_to_xlsx_batch.py, per_object_ff0.py, spaghetti_plot_per_well.py")

    # per_object_ff0 defaults
    ap.add_argument("--stim", type=int, default=10)
    ap.add_argument("--baseline-n", type=int, default=5)
    ap.add_argument("--ylim", nargs=2, type=float, default=[-0.5, 7.0], metavar=("YMIN", "YMAX"))
    ap.add_argument("--expected-n", type=int, default=20,
                    help="Expected timepoints per object (passed to filter_post_stats.py)")
    ap.add_argument("--min-n", type=int, default=None,
                    help="Minimum timepoints per object (relaxed check: pass if n >= min-n). "
                         "Overrides --expected-n when provided.")
    ap.add_argument("--per-page", type=int, default=20)
    ap.add_argument("--pptx", action="store_true", default=True)
    ap.add_argument("--no-pptx", dest="pptx", action="store_false")
    # per_object_ff0 plotting controls
    ap.add_argument("--ff0-no-plots", action="store_true",
                    help="Disable all plots in per_object_ff0 (PNG pages and PPTX)")
    ap.add_argument("--ff0-no-single-pngs", action="store_true",
                    help="Disable individual object PNGs in per_object_ff0")
    ap.add_argument("--ff0-no-iqr-filter", action="store_true",
                    help="Disable both size and intensity IQR filtering in per_object_ff0")

    # skip flags
    ap.add_argument("--no-phenix", action="store_true", help="Do not attempt any XLSX generation")
    ap.add_argument("--no-ff0", action="store_true", default=False)
    ap.add_argument("--no-spaghetti", action="store_true")
    ap.add_argument("--spaghetti-both", action="store_true", default=True,
                    help="Write both raw and included spaghetti plots into subfolders.")

    # reorg step (optional)
    ap.add_argument("--reorg-first", action="store_true",
                    help="Run phenix_reorg.py before the pipeline using --root/--dates")
    ap.add_argument("--reorg-apply", action="store_true",
                    help="Apply changes in phenix_reorg (otherwise it would be a dry-run)")
    ap.add_argument("--reorg-all-dates", action="store_true",
                    help="When reorg runs and --dates is not provided, process all date folders")
    
    # background computation (optional)
    ap.add_argument("--compute-backgrounds", action="store_true",
                    help="Compute per-phase FOV backgrounds (Baseline/Stim1/Stim2) before downstream analysis")
    ap.add_argument("--background-timepoints", type=int, default=20,
                    help="Max timepoints per phase to use for background (default: 20)")
    ap.add_argument("--background-channel", help="Filter background images by channel name (e.g., 'Alexa 488')")
    ap.add_argument("--background-csv", help="Path to existing background CSV file (skips computation if provided)")
    ap.add_argument("--raw-data-root", default=None,
                    help=r"Root of raw image data, required with --compute-backgrounds. "
                         r"Date folders live under <root>\20YY\MMDDYY (e.g. <root>\2025\092625).")
    ap.add_argument("--objects-population", default=None,
                    help="Harmony population name (substring) to use when an Evaluation folder holds several "
                         "'Objects_Population - <name>.txt' exports. Default: the first one found.")

    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--strict-phenix", action="store_true",
                    help="Fail if phenix cannot produce any per-well XLSX for an experiment")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="Print verbose progress messages (useful for debugging hangs on cloud sync folders)")
    ap.add_argument("--timeout", type=int, default=7200,
                    help="Timeout for subprocess commands in seconds (default: 7200 = 2 hours, increase for very slow Box sync)")
    ap.add_argument("--pipeline-log", type=Path, default=None,
                    help="Path to save pipeline/subprocess output (default: ./run_pt_pipeline_<timestamp>.log)")
    ap.add_argument("--no-combine-date-summaries", action="store_true",
                    help="Skip final combine_date_summaries step")

    args = ap.parse_args()
    if args.objects_population:
        os.environ["PT_OBJECTS_POPULATION"] = args.objects_population
    if args.scripts_dir: args.scripts_dir = args.scripts_dir.expanduser().resolve()

    # Defer log path until after experiment discovery so it can live in the date folder.
    # Buffer early stderr messages and flush them once the path is known.
    global RUN_LOG_PATH
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _early_log_lines: list[str] = [f"=== run_pt_pipeline started: {datetime.now().isoformat()} ==="]
    _log_finalised = False

    def _finalise_log(date_folder: "Path | None") -> None:
        nonlocal _log_finalised
        if _log_finalised:
            return
        _log_finalised = True
        if args.pipeline_log:
            RUN_LOG_PATH = args.pipeline_log.expanduser().resolve()
        elif date_folder is not None:
            RUN_LOG_PATH = date_folder / f"run_pt_pipeline_{ts}.log"
        else:
            RUN_LOG_PATH = Path.cwd() / f"run_pt_pipeline_{ts}.log"
        globals()["RUN_LOG_PATH"] = RUN_LOG_PATH
        for line in _early_log_lines:
            _append_pipeline_log(line)
        eprint(f"[log] Saving pipeline output to: {RUN_LOG_PATH}")

    # Warn about Box/cloud sync folder performance
    for p in args.paths:
        path_str = str(norm(p))
        if "CloudStorage" in path_str or "Box" in path_str or "iCloud" in path_str:
            eprint("\n[WARNING] Detected cloud sync folder (Box/iCloud).")
            eprint("         File operations may be very slow. Consider:")
            eprint("         1. Copying data to a local folder first")
            eprint("         2. Using --verbose to see progress")
            eprint("         3. Ensuring Box sync is complete before running\n")
            break

    # Optionally run phenix_reorg first
    if args.reorg_first:
        reorg_args = ["--root", str(args.root)]
        if args.dates:
            reorg_args.extend(["--dates", *args.dates])
        elif args.reorg_all_dates:
            reorg_args.append("--all-dates")
        else:
            eprint("[reorg] --dates not provided; use --reorg-all-dates to process all dates. Skipping reorg.")
            reorg_args = None

        if reorg_args is not None:
            if args.reorg_apply:
                reorg_args.append("--apply")
            reorg_args.extend(["--mirror-root", "--roi-mode", "subdir"])
            eprint("\n=== REORG (phenix_reorg) ===")
            rc = run_module_or_script(
                args.python_bin,
                "pt.phenix_reorg",
                reorg_args,
                args.dry_run,
                args.scripts_dir,
                timeout=args.timeout
            )
            if rc != 0:
                eprint(f"[ERROR] phenix_reorg failed with rc={rc}")
                sys.exit(rc)
    
    exps = []
    eprint("\n=== DISCOVERING EXPERIMENTS ===")
    for p in args.paths:
        norm_path = norm(p)
        eprint(f"[discover] Searching in: {norm_path}")
        found = find_experiments(norm_path)
        eprint(f"[discover] Found {len(found)} experiment(s) in {norm_path}")
        exps.extend(found)

    if not exps:
        _finalise_log(None)
        eprint("[Warning] No Experiment_* directories found from the provided paths.");
        sys.exit(2)

    # Derive the date folder from the first experiment path (Analysis/<exp> -> Analysis -> date)
    _date_folder = exps[0].parent.parent
    _finalise_log(_date_folder)

    eprint(f"\n[discover] Total: {len(exps)} experiment(s):")
    for e in exps: eprint("  -", e)

    # Optionally compute backgrounds (after experiments are discovered)
    background_csv_path = None

    # Auto-detect existing background CSV in the date folder
    _auto_bg_csv = _date_folder / f"{_date_folder.name}_fov_backgrounds_avg.csv"

    if args.background_csv:
        background_csv_path = Path(args.background_csv).expanduser().resolve()
        if not background_csv_path.exists():
            eprint(f"[ERROR] Background CSV not found: {background_csv_path}")
            sys.exit(1)
        eprint(f"\n=== USING EXISTING BACKGROUND CSV: {background_csv_path} ===")
    elif not args.compute_backgrounds and _auto_bg_csv.exists():
        background_csv_path = _auto_bg_csv
        eprint(f"\n=== USING AUTO-DETECTED BACKGROUND CSV: {background_csv_path} ===")
    elif args.compute_backgrounds:
        eprint("\n=== COMPUTING FOV BACKGROUNDS ===")

        # Collect all 6-digit dates from experiment paths
        all_dates = set(args.dates) if args.dates else set()
        if not all_dates:
            for exp in exps:
                for part in exp.parts:
                    if re.fullmatch(r"\d{6}", part):
                        all_dates.add(part)
                        break

        if not all_dates:
            eprint("[ERROR] Could not determine experiment date(s) for background computation.")
            sys.exit(1)

        # Resolve raw image root: <raw-data-root>\20YY\MMDDYY
        # Last two digits of MMDDYY give the year (e.g. 092625 → 2025)
        if not args.raw_data_root:
            eprint("[ERROR] --compute-backgrounds requires --raw-data-root (folder holding <20YY>/<MMDDYY> raw image folders).")
            sys.exit(1)
        raw_root = Path(args.raw_data_root)
        bg_args = []
        resolved_dates = []
        for date6 in sorted(all_dates):
            year_suffix = date6[-2:]  # e.g. "25"
            year_folder = f"20{year_suffix}"  # e.g. "2025"
            date_root = raw_root / year_folder
            if not date_root.exists():
                eprint(f"[ERROR] Raw data year folder not found: {date_root}")
                sys.exit(1)
            if not (date_root / date6).exists():
                eprint(f"[ERROR] Raw data date folder not found: {date_root / date6}")
                sys.exit(1)
            resolved_dates.append(date6)
            eprint(f"[background] Raw images root: {date_root}, date: {date6}")

        # All dates should share the same year folder (typical per-run usage)
        year_suffix = sorted(all_dates)[0][-2:]
        bg_root = raw_root / f"20{year_suffix}"
        bg_args.extend(["--root", str(bg_root)])
        bg_args.extend(["--dates", *resolved_dates])
        bg_args.extend([
            "--max-timepoints", str(args.background_timepoints),
            "--skip-start-stim", "2",
        ])
        if args.background_channel:
            bg_args.extend(["--channel", args.background_channel])

        # Save background CSV inside the date folder
        bg_output = _date_folder / f"{_date_folder.name}_fov_backgrounds_avg.csv"
        bg_args.extend(["--output", str(bg_output)])
        
        rc = run_module_or_script(
            args.python_bin,
            "pt.compute_fov_background_avg",
            bg_args,
            args.dry_run,
            args.scripts_dir,
            timeout=args.timeout
        )
        if rc != 0:
            eprint(f"[ERROR] compute_fov_background_avg failed with rc={rc}")
            sys.exit(rc)

        background_csv_path = bg_output if bg_output.exists() else None
        if background_csv_path:
            eprint(f"[background] Background CSV saved to: {background_csv_path}")
        else:
            eprint(f"[ERROR] Background CSV not found at expected path {bg_output}")
            sys.exit(1)
    
    # Store background CSV path for use in downstream tasks
    args.background_csv_path = background_csv_path

    # phenix first, ordered
    for i, exp in enumerate(exps, 1):
        eprint(f"\n{'=' * 60}")
        eprint(f"PROCESSING EXPERIMENT {i}/{len(exps)}: {exp.name}")
        eprint(f"{'=' * 60}")
        if not args.no_phenix:
            eprint(f"\n=== PHENIX -> XLSX: {exp} ===")
            # 1) generate per-well XLSX
            eprint(f"[phenix] Starting XLSX generation for {exp.name}...")
            run_phenix_for_context(exp, args.python_bin, args.scripts_dir, args.dry_run, timeout=args.timeout)
            eprint(f"[phenix] Completed XLSX generation for {exp.name}")

            # 2) annotate stim-times on the generated workbook(s)
            #    find all *_per-well.xlsx files (prefer non-recursive search first)
            eprint(f"[search] Looking for *_per-well.xlsx files in {exp}...")
            stim_xlsx_files = []

            # First try: look in Evaluation subdirectories (most common location)
            try:
                for eval_dir in find_evaluations(exp):
                    xlsx = pick_per_well_xlsx(eval_dir)
                    if xlsx and xlsx.exists():
                        stim_xlsx_files.append(xlsx)
                eprint(f"[search] Found {len(stim_xlsx_files)} per-well XLSX file(s) in Evaluation directories")
            except Exception as e:
                eprint(f"[search][WARN] Error checking Evaluation directories: {e}")

            # Fallback: recursive search (SLOW on cloud sync - only if needed)
            if not stim_xlsx_files:
                eprint(
                    f"[search] No XLSX found in Evaluation dirs, trying recursive search (this may be slow on cloud sync folders)...")
                try:
                    # Use a generator and limit to avoid hanging
                    count = 0
                    for p in exp.rglob("*_per-well.xlsx"):
                        if p.name.startswith("~$"):
                            continue  # skip Excel lockfiles
                        stim_xlsx_files.append(p)
                        count += 1
                        # Limit search to first 20 files to avoid extremely long waits
                        if count >= 20:
                            eprint(f"[search] Found {count} files, limiting search to avoid timeout")
                            break
                    eprint(f"[search] Recursive search found {len(stim_xlsx_files)} per-well XLSX file(s)")
                except Exception as e:
                    eprint(f"[ERROR] Recursive search failed: {e}")

            for stim_xlsx in stim_xlsx_files:
                if stim_xlsx.name.startswith("~$"):
                    continue  # skip Excel lockfiles
                eprint(f"=== DETECT STIMS: {stim_xlsx} ===")
                run_module_or_script(
                    args.python_bin,
                    "pt.detect_stim_times",
                    ["--perwell", str(stim_xlsx)],
                    args.dry_run,
                    args.scripts_dir,
                    timeout=args.timeout
                )
    # per-evaluation tasks (ensure XLSX as needed during planning)
    eval_tasks = []
    for exp in exps:
        eval_tasks.extend(plan_for_experiment(exp, args))
    if not eval_tasks:
        eprint("\nNo per-evaluation tasks to run.");
        return

    eprint("\n============================================================")
    eprint("==================== parallelizing tasks ====================")
    eprint(f"Running {len(eval_tasks)} evaluation-level task(s) with {args.threads} thread(s)...")
    eprint("=============================================================")

    errors = []
    responder_xlsx_cache = {}  # Cache responder Excel paths by input xlsx

    with ThreadPoolExecutor(max_workers=max(1, args.threads)) as pool:
        fut2 = {}
        for name, targs in eval_tasks:
            if name == "ff0":
                fut = pool.submit(run_ff0, *targs)
                fut2[fut] = (name, targs)
            elif name == "spaghetti":
                # Spaghetti plots should use the included workbook; run after ff0 completes.
                continue
            else:
                continue

        # Process ff0 tasks first to get responder Excel paths
        for fut in as_completed(fut2):
            name, targs = fut2[fut]
            try:
                if name == "ff0":
                    result = fut.result()
                    # Cache the responder Excel path
                    input_xlsx = targs[0]
                    responder_xlsx_cache[input_xlsx] = result
                else:
                    fut.result()
            except Exception as e:
                errors.append((name, targs, str(e)))
                eprint(f"[ERROR] {name} failed: {e}")

        # Run spaghetti plots after ff0/inclusion so raw and included inputs are both available.
        spaghetti_tasks = [(name, targs) for name, targs in eval_tasks if name == "spaghetti"]
        for name, targs in spaghetti_tasks:
            (input_xlsx, python_bin, scripts_dir, mode, same_ylim, facet, outdir,
             dry_run, background_csv, timeout, both) = targs
            filtered_xlsx = responder_xlsx_cache.get(input_xlsx)
            if filtered_xlsx is None or not filtered_xlsx.exists():
                filtered_xlsx = prefer_filtered_xlsx(input_xlsx)
            if filtered_xlsx is None or not filtered_xlsx.exists():
                filtered_xlsx = input_xlsx
            try:
                eprint(f"\n=== SPAGHETTI ({mode}): raw={input_xlsx} included={filtered_xlsx} ===")
                run_spaghetti(input_xlsx, filtered_xlsx, python_bin, scripts_dir,
                              mode, same_ylim, facet,
                              outdir, dry_run, background_csv, timeout, both)
            except Exception as e:
                errors.append((name, targs, str(e)))
                eprint(f"[ERROR] {name} failed: {e}")

    # Final stage 1/2: responder bar charts (after all evaluation-level tasks complete)
    if not args.no_ff0:
        seen_targets = set()
        responder_targets = []
        ff0_tasks = [(name, targs) for name, targs in eval_tasks if name == "ff0"]
        for _, targs in ff0_tasks:
            input_xlsx = targs[0]
            responder_xlsx = responder_xlsx_cache.get(input_xlsx)
            if responder_xlsx is None:
                fallback = prefer_filtered_xlsx(input_xlsx)
                if fallback.exists():
                    responder_xlsx = fallback
            if responder_xlsx and responder_xlsx.exists():
                key = str(responder_xlsx.resolve())
                if key not in seen_targets:
                    seen_targets.add(key)
                    responder_targets.append(responder_xlsx)
            else:
                eprint(f"[responder_charts] Skipping: responder Excel not found for {input_xlsx}")

        for responder_xlsx in responder_targets:
            try:
                eprint(f"\n=== FINAL RESPONDER BAR CHARTS: {responder_xlsx} ===")
                run_responder_bar_charts(responder_xlsx, args.python_bin, args.scripts_dir, args.dry_run, args.timeout)
            except Exception as e:
                errors.append(("responder_charts_final", (responder_xlsx,), str(e)))
                eprint(f"[ERROR] responder_charts_final failed: {e}")

    # Final stage 2/2: combine date summaries by inferred date roots.
    if not args.no_combine_date_summaries:
        date_groups = infer_date_groups_from_experiments(exps)
        if not date_groups:
            eprint("[combine] Skipping: could not infer any date folders from discovered experiments.")
        for root_dir, dates_set in sorted(date_groups.items(), key=lambda kv: str(kv[0])):
            dates = sorted(dates_set)
            try:
                eprint(f"\n=== FINAL COMBINE DATE SUMMARIES: root={root_dir} dates={dates} ===")
                rc = run_combine_date_summaries(root_dir, dates, args.python_bin, args.scripts_dir, args.dry_run,
                                                args.timeout)
                if rc != 0:
                    errors.append(("combine_date_summaries", (root_dir, dates), f"rc={rc}"))
                    eprint(f"[ERROR] combine_date_summaries failed for root={root_dir}, dates={dates} (rc={rc})")
            except Exception as e:
                errors.append(("combine_date_summaries", (root_dir, dates), str(e)))
                eprint(f"[ERROR] combine_date_summaries failed for root={root_dir}, dates={dates}: {e}")

    if errors:
        eprint(f"\nCompleted with {len(errors)} error(s).");
        sys.exit(1)
    eprint("\nAll done.")

    try:
        import winsound
        winsound.Beep(880, 5000)
    except Exception:
        pass


if __name__ == "__main__":
    main()

