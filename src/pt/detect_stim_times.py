#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Iterable
import numpy as np
import pandas as pd

# ---------------- small utils ----------------
def eprint(*a, **k): print(*a, file=sys.stderr, **k)

ENCODINGS = ["utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be"]
DELIMS    = ["\t", ",", ";", "|"]

def sniff_delim(lines: List[str]) -> str:
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return "\t"
    scores = {}
    for d in DELIMS:
        per = [ln.count(d) for ln in nonempty[:200]]
        scores[d] = float(np.median(per)) if per else 0.0
    return max(scores, key=scores.get)

def read_any_table(path: str | Path, sheet: Optional[str] = None) -> pd.DataFrame:
    """
    Read either Excel (.xlsx/.xls/.xlsm) or delimited text (.txt/.tsv/.csv).
    For text, auto-sniff encoding & delimiter.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(p, sheet_name=sheet) if sheet else pd.read_excel(p)
    else:
        data = None
        for enc in ENCODINGS:
            try:
                with open(p, "r", encoding=enc, errors="strict") as fh:
                    head = []
                    for _ in range(50):
                        try: head.append(next(fh))
                        except StopIteration: break
                delim = sniff_delim(head)
                data = pd.read_csv(p, sep=delim, encoding=enc)
                break
            except (UnicodeError, FileNotFoundError):
                continue
            except Exception:
                continue
        if data is None:
            data = pd.read_csv(p, sep=None, engine="python")
        return data

def remove_operaphenix_from_name(p: Path) -> Path:
    """
    Remove any 'OperaPhenix' (case-insensitive, with/without space) from filename.
    """
    name = p.name
    new = re.sub(r"(?i)opera[ _-]?phenix", "", name)
    new = re.sub(r"__+", "_", new)
    new = re.sub(r"(_-|-_|--)", "-", new)
    new = new.replace("__", "_")
    new = re.sub(r"(_+\.)", ".", new)  # trailing underscores before extension
    if not Path(new).stem:
        new = "annotated.xlsx"
    return p.with_name(new)


def _candidate_index_names() -> Iterable[str]:
    # Most specific first
    return [
        "indexfile.xlsx", "indexfile.xlsm", "indexfile.xls",
        "indexfile.txt", "indexfile.tsv", "indexfile.csv",
        "index.xlsx", "index.xlsm", "index.xls",
        "index.txt", "index.tsv", "index.csv",
    ]

def find_index_file_adjacent(perwell_xlsx: Path) -> Path:
    """
    Find the index file one level above the per-well xlsx, with common names.
    Priority: xlsx/xlsm/xls > txt/tsv/csv; 'indexfile.*' > 'index.*'
    """
    parent = perwell_xlsx.parent  # Evaluation2/
    search_dir = parent.parent     # one level above
    if not search_dir or not search_dir.exists():
        raise FileNotFoundError(f"Cannot ascend one level above {perwell_xlsx}")

    # 1) exact names (case-insensitive)
    existing = {p.name.lower(): p for p in search_dir.iterdir() if p.is_file()}
    for name in _candidate_index_names():
        if name in existing:
            return existing[name]

    # 2) fallback: anything that looks like 'indexfile' or 'index' with allowed suffixes
    allowed = {".xlsx", ".xlsm", ".xls", ".txt", ".tsv", ".csv"}
    picks = []
    for p in search_dir.iterdir():
        if not p.is_file(): continue
        if p.suffix.lower() not in allowed: continue
        low = p.name.lower()
        if low.startswith("indexfile"):
            picks.append((0, p))  # strongest
        elif low.startswith("index"):
            picks.append((1, p))
    if picks:
        picks.sort(key=lambda t: (t[0], {".xlsx":0, ".xlsm":1, ".xls":2, ".txt":3, ".tsv":4, ".csv":5}[t[1].suffix.lower()]))
        return picks[0][1]

    raise FileNotFoundError(
        f"No index file found one level above {perwell_xlsx}. "
        f"Tried names: {', '.join(_candidate_index_names())}"
    )

# ---------------- stim detection from indexfile ----------------
def detect_stims(index_path: Path,
                 index_sheet: str = "indexfile",
                 time_col: str = "Timepoint",
                 seq_col: str = "Sequence") -> Dict[str, object]:
    """
    Detect stim timepoints from index file.
    Stims = first Timepoint for each Sequence >= 3.
    Baseline = Timepoints where Sequence == 2 (if present).
    """
    is_excel = str(index_path).lower().endswith((".xlsx", ".xls", ".xlsm"))
    df = read_any_table(index_path, sheet=index_sheet if is_excel else None)

    # Column finder (case-insensitive, with aliases)
    def find_col(cands, cols):
        cols_lower = {c.lower(): c for c in cols}
        for c in cands:
            if c.lower() in cols_lower: return cols_lower[c.lower()]
        return None

    time_col_found = find_col([time_col, "Frame", "frame", "timepoint"], df.columns)
    seq_col_found  = find_col([seq_col, "sequence"], df.columns)

    if time_col_found is None or seq_col_found is None:
        raise ValueError(f"Missing columns in index file: need '{time_col}' and '{seq_col}' (case-insensitive)")

    # Mode sequence per timepoint (robust to multiple Fields/Channels)
    gb = df.groupby(time_col_found)[seq_col_found]
    def mode_or_first(s):
        m = s.mode()
        if not m.empty:
            return int(m.iat[0])
        s_num = pd.to_numeric(s, errors="coerce").dropna().astype(int)
        return int(s_num.iat[0]) if not s_num.empty else int(s.iloc[0])

    seq_by_tp = gb.apply(mode_or_first).astype(int).to_dict()
    all_tps = sorted(seq_by_tp.keys())

    # First TP for each sequence
    first_tp_by_seq: Dict[int, int] = {}
    for tp in all_tps:
        seq = seq_by_tp[tp]
        first_tp_by_seq.setdefault(seq, tp)

    stim_timepoints = [tp for seq, tp in sorted(first_tp_by_seq.items()) if seq >= 3]

    baseline_tps = [tp for tp in all_tps if seq_by_tp[tp] == 2]
    baseline_range: Optional[Tuple[int,int]] = (min(baseline_tps), max(baseline_tps)) if baseline_tps else None

    return {
        "timepoints": all_tps,
        "sequence_by_tp": seq_by_tp,
        "stim_timepoints": sorted(stim_timepoints),
        "baseline_range": baseline_range,
    }

# ---------------- labeling ----------------
def label_phase_for_tp(tp: int,
                       stim_tps: List[int],
                       baseline_range: Optional[Tuple[int, int]]) -> str:
    """
    Returns one of: 'pre-baseline', 'baseline', 'post-baseline-pre-stim', 'stim1', 'stim2', ...
    """
    if baseline_range and tp < baseline_range[0]:
        return "pre-baseline"
    if baseline_range and (baseline_range[0] <= tp <= baseline_range[1]):
        return "baseline"
    if not stim_tps:
        return "post-baseline"
    idx = None
    for i, st in enumerate(sorted(stim_tps), start=1):
        if tp >= st: idx = i
        else: break
    if idx is None:
        return "post-baseline-pre-stim"
    return f"stim{idx}"

def build_tp_mapping(df: pd.DataFrame) -> List[int]:
    """
    Infer per-row timepoint order for a per-well sheet.
    Preference:
      1) 'Timepoint' (or alias) numeric
      2) 'Time [s]' (or alias) ascending rank mapped to 1..N
      3) fallback to row order 1..N
    """
    for cand in ["Timepoint", "timepoint", "Frame", "frame"]:
        if cand in df.columns:
            ser = pd.to_numeric(df[cand], errors="coerce")
            if ser.notna().all():
                return ser.astype(int).tolist()

    time_candidates = [c for c in df.columns
                       if c.strip().lower() in {"time [s]", "time (s)", "time_s", "time"}]
    if time_candidates:
        tcol = time_candidates[0]
        ser = pd.to_numeric(df[tcol], errors="coerce")
        order_idx = ser.sort_values(kind="mergesort").index
        ranks = pd.Series(range(1, len(df) + 1), index=order_idx).sort_index()
        return ranks.tolist()

    return list(range(1, len(df) + 1))

# ---------------- per-well annotation ----------------
EXCLUDE_SHEETS = {
    "plate_overview", "plate overview", "overview", "readme", "info",
    "all wells", "all_wells", "summary", "per-well summary", "perwell summary",
    "plots", "graphs", "legend"
}

def should_skip_sheet(name: str, idx: int, skip_first_sheet: bool) -> bool:
    lower = name.strip().lower()
    if skip_first_sheet and idx == 0:
        return True
    if lower in EXCLUDE_SHEETS:
        return True
    return False  # annotate everything else

def annotate_perwell_workbook(perwell_xlsx: Path,
                              out_xlsx: Path,
                              stim_info: Dict[str, object],
                              phase_col_name: str = "phase",
                              skip_first_sheet: bool = True):
    """
    Add a 'phase' column to each sheet except the first (optional) and obvious non-data sheets.
    """
    if perwell_xlsx.suffix.lower() not in {".xlsx", ".xls", ".xlsm"}:
        raise SystemExit(f"--perwell must be an Excel workbook (.xlsx/.xls/.xlsm). Got: {perwell_xlsx}")
    from zipfile import is_zipfile

    if perwell_xlsx.name.startswith("~$"):
        raise SystemExit(f"Refusing to open Excel lockfile: {perwell_xlsx}")

    # Only OOXML .xlsx/.xlsm should be treated as zipped Excel
    if perwell_xlsx.suffix.lower() in {".xlsx", ".xlsm"} and not is_zipfile(perwell_xlsx):
        raise SystemExit(
            f"{perwell_xlsx} looks like .xlsx/.xlsm but is not a valid OOXML zip (maybe a lockfile or corrupted).")

    xls = pd.ExcelFile(perwell_xlsx)
    sheet_names = list(xls.sheet_names)
    sheet_names = list(xls.sheet_names)

    # Read all sheets up-front
    sheets = {name: pd.read_excel(perwell_xlsx, sheet_name=name) for name in sheet_names}

    stim_tps = stim_info["stim_timepoints"]
    baseline_range = stim_info["baseline_range"]

    updated = {}
    annotated_any = False
    annotated_list: List[str] = []

    for idx, name in enumerate(sheet_names):
        df = sheets[name]

        if should_skip_sheet(name, idx, skip_first_sheet):
            eprint(f"[SKIP] {name}")
            updated[name] = df
            continue

        # Try to infer timepoints; if we can, we annotate
        inferred_tp = build_tp_mapping(df)
        if len(inferred_tp) != len(df) or len(df) == 0:
            eprint(f"[WARN] Could not infer timepoints for '{name}' (rows: {len(df)}). Copying unchanged.")
            updated[name] = df
            continue

        labels = [label_phase_for_tp(int(tp), stim_tps, baseline_range) for tp in inferred_tp]

        df_out = df.copy()
        if phase_col_name in df_out.columns:
            df_out[phase_col_name] = labels
        else:
            df_out.insert(len(df_out.columns), phase_col_name, labels)

        # Derive a per-well stim reference time in seconds (first stim onset)
        # Find a time column in seconds
        time_candidates = [
            c for c in df_out.columns
            if str(c).strip().lower() in {"time [s]", "time (s)", "time_s", "time"}
        ]
        stim_time_s = np.nan
        if time_candidates:
            tcol = time_candidates[0]
            tvals = pd.to_numeric(df_out[tcol], errors="coerce")
            # indices where phase label starts with 'stim'
            stim_mask = pd.Series(labels, index=df_out.index).astype(str).str.startswith("stim")
            if stim_mask.any():
                stim_times = tvals[stim_mask].dropna()
                if not stim_times.empty:
                    stim_time_s = float(stim_times.min())
        # Append column 'stim_time_s' (scalar per sheet)
        col_name = "stim_time_s"
        if col_name in df_out.columns:
            df_out[col_name] = stim_time_s
        else:
            df_out.insert(len(df_out.columns), col_name, stim_time_s)

        updated[name] = df_out
        annotated_any = True
        annotated_list.append(name)
        eprint(f"[ANNO] {name}")

    if not annotated_any:
        eprint("[WARN] No sheets were annotated. Check sheet names/contents or adjust EXCLUDE_SHEETS.")

    # Write output preserving sheet order
    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as w:
        for name in sheet_names:
            updated[name].to_excel(w, sheet_name=name, index=False)

    eprint(f"[OK] Wrote annotated workbook: {out_xlsx}")
    if annotated_list:
        eprint("[SUMMARY] Annotated sheets: " + ", ".join(annotated_list))

# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser(description="Detect stim times from index file and annotate per-well Excel with phase labels.")
    ap.add_argument("--index", default=None,
                    help="(Optional) Path to index file. If omitted, the script auto-finds it one level above the per-well workbook.")
    ap.add_argument("--perwell", required=True,
                    help="Path to per-well Excel workbook (.xlsx) to annotate.")
    ap.add_argument("--index-sheet", default="indexfile",
                    help="Sheet name in the index Excel (ignored for text). Default: indexfile")
    ap.add_argument("--phase-col", default="phase",
                    help="Name of the new column to add. Default: phase")
    ap.add_argument("--inplace", action="store_true", default=True,
                    help="Modify the per-well workbook in-place (keeps original filename).")
    ap.add_argument("--out", default=None,
                    help="Explicit output path. If omitted and not --inplace, removes 'OperaPhenix' and appends '_with_stims.xlsx'.")
    ap.add_argument("--no-skip-first", dest="skip_first", action="store_false",
                    help="Do NOT skip the first sheet. Default: skip first.")
    args = ap.parse_args()

    perwell_path = Path(args.perwell).expanduser().resolve()

    # Choose index path (auto if not provided)
    if args.index is None:
        index_path = find_index_file_adjacent(perwell_path)
        eprint(f"[AUTO] Using index file: {index_path}")
    else:
        index_path = Path(args.index).expanduser().resolve()

    if not index_path.exists():
        raise SystemExit(f"Index file not found: {index_path}")
    if not perwell_path.exists():
        raise SystemExit(f"Per-well workbook not found: {perwell_path}")

    # Detect stims
    stim_info = detect_stims(index_path=index_path, index_sheet=args.index_sheet)

    # Choose output path
    if args.inplace:
        out_path = perwell_path
    else:
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
        else:
            cleaned = remove_operaphenix_from_name(perwell_path)
            out_path = cleaned.with_name(f"{cleaned.stem}_with_stims{cleaned.suffix}")

    annotate_perwell_workbook(perwell_xlsx=perwell_path,
                              out_xlsx=out_path,
                              stim_info=stim_info,
                              phase_col_name=args.phase_col,
                              skip_first_sheet=args.skip_first)

    # Console summary of stim detection
    stims = stim_info["stim_timepoints"]
    base  = stim_info["baseline_range"]
    eprint(f"[SUMMARY] Baseline: {base if base else 'N/A'}; Stims at TPs: {stims if stims else 'None'}")

if __name__ == "__main__":
    main()
