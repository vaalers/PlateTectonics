# pt_utils.py
# -*- coding: utf-8 -*-
import re, sys, string
from pathlib import Path
from typing import Optional, List, Dict, Tuple
import numpy as np
import pandas as pd
from pt.run_pt_pipeline import run_phenix_for_context, pick_per_well_xlsx, has_raw_eval_txts, build_per_well_xlsx_fallback

def eprint(*a, **k): print(*a, file=sys.stderr, **k)

def mode_or_first(s):
        m = s.mode()
        if not m.empty:
            return int(m.iat[0])
        s_num = pd.to_numeric(s, errors="coerce").dropna().astype(int)
        return int(s_num.iat[0]) if not s_num.empty else int(s.iloc[0])

# =============================================================================
# Palette
# =============================================================================
OKABE_ITO = ['#000000','#E69F00','#56B4E9','#009E73','#F0E442','#0072B2','#D55E00','#CC79A7']
# ---------- Robust table readers ----------


ENCODINGS = ["utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be"]
DELIMS    = ["\t", ",", ";", "|"]


def sniff_delim(lines: List[str]) -> str:
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty: return "\t"
    scores = {}
    for d in DELIMS:
        per = [ln.count(d) for ln in nonempty[:200]]
        scores[d] = float(np.median(per)) if per else 0.0
    return max(scores, key=scores.get)


def read_any_table(path: str | Path, sheet: Optional[str] = None) -> pd.DataFrame:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".xlsx", ".xls", ".xlsm"}:
        return pd.read_excel(p, sheet_name=sheet) if sheet else pd.read_excel(p)
    # text
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

# ----------------------------File Checkers-----------------------------
def ensure_xlsx_for_evaluation(eval_dir: Path, python_bin: str, scripts_dir: Path|None, dry_run: bool) -> None:
    """
    If an evaluation has raw .txt files but no per-well workbook, try to generate one.
    Try roots in order: Experiment -> Analysis -> Date -> Evaluation; if all fail, build locally.
    """
    exp_dir   = eval_dir.parent
    analysis  = exp_dir.parent if exp_dir.parent.name == "Analysis" else None
    date_dir  = analysis.parent if analysis else None

    tried = []
    for root in [exp_dir, analysis, date_dir, eval_dir]:
        if root is None: continue
        eprint(f"[phenix:eval] Attempting generation from root: {root}")
        rc = run_phenix_for_context(root, python_bin, scripts_dir, dry_run)
        tried.append((root, rc))
        if rc == 0:
            break

    # Re-check; if still missing, build locally from raw .txt
    if pick_per_well_xlsx(eval_dir) is None and has_raw_eval_txts(eval_dir):
        eprint("[phenix:eval] phenix route failed; switching to in-script builder…")
        built = build_per_well_xlsx_fallback(eval_dir)
        if built is None:
            eprint("[phenix:eval][WARN] Fallback builder could not produce a workbook.")
    elif pick_per_well_xlsx(eval_dir) is None:
        eprint("[phenix:eval][WARN] No raw .txt pair present; cannot build workbook.")


# ---------- Filename helpers ----------


_WELL_RX = re.compile(r"^([A-H])0?(\d{1,2})$", re.I)


def normalize_well(val: str) -> str:
    s = str(val).strip().upper()
    m = _WELL_RX.match(s)
    if not m:
        return s
    row, col = m.groups()
    return f"{row}{int(col)}"


def normalize_well_column(df: pd.DataFrame, col: str = "Well") -> pd.DataFrame:
    if col not in df.columns:
        return df
    df = df.copy()
    df[col] = df[col].astype(str).map(normalize_well)
    return df


def remove_operaphenix_from_name(p: Path) -> Path:
    name = p.name
    new = re.sub(r"(?i)opera[ _-]?phenix", "", name)
    new = re.sub(r"__+", "_", new)
    new = re.sub(r"(_-|-_|--)", "-", new)
    new = new.replace("__", "_")
    new = re.sub(r"(_+\.)", ".", new)
    if not Path(new).stem:
        new = "annotated.xlsx"
    return p.with_name(new)

def find_col(cols, pattern, ignore_case=True):
    flags = re.I if ignore_case else 0
    for c in cols:
        if re.search(pattern, str(c), flags=flags):
            return c
    return None

def normalize_cols(cols):
    out = []
    for c in cols:
        c = str(c).replace("¬µ", "µ")
        c = re.sub(r"\s+", " ", c).strip()
        out.append(c)
    return out
# ---------- Stim detection + labeling ----------


def stims_from_df(df: pd.DataFrame,
                  phase_cols: List[str] = ["phase","Phase"],
                  time_cols:  List[str] = ["Time [s]","Time (s)","Time","Timepoint"]) -> List[float]:
    d = df.copy()
    d.columns = normalize_cols(d.columns)

    # 1) direct stim columns like "stim", "stim1", "stim_1", "stim_time"
    times: List[float] = []
    for c in d.columns:
        name = str(c).strip().lower()
        # common single-value stim markers
        if (
            re.match(r"^stim(\d+)?(_time)?(_s)?$", name)  # stim, stim1, stim_time, stim_time_s, etc.
            or name.startswith("stim_")                  # stim_1, stim_2, stim_time_s, etc.
            or name in {"stim_time_s"}
            or name.endswith("_start_s") or name.endswith("_end_s")  # stim1_start_s / stim2_end_s from annotated tables
        ):
            vals = pd.to_numeric(d[c], errors="coerce").dropna().unique()
            times.extend([float(v) for v in vals])

    # 2) fallback: use phase + time
    if not times:
        phase_col = next((c for c in phase_cols if c in d.columns), None)
        time_col  = next((c for c in time_cols  if c in d.columns), None)
        if phase_col and time_col:
            phase = d[phase_col].astype(str).str.strip().str.lower()
            t = pd.to_numeric(d[time_col], errors="coerce")
            acc: List[float] = []
            for n in range(1, 16):
                m = phase.str.fullmatch(fr"stim\s*{n}") | phase.str.fullmatch(fr"stim{n}")
                if m.any():
                    tn = t[m].dropna()
                    if not tn.empty:
                        acc.append(float(tn.min()))
            m0 = phase.str.fullmatch(r"stim")
            if m0.any():
                tn = t[m0].dropna()
                if not tn.empty:
                    acc.append(float(tn.min()))
            times = sorted(set(acc))

    return sorted(set(times))



def first_header_like(cols, *patterns):
    for pat in patterns:
        for c in cols:
            if re.search(pat, c, flags=re.I): return c
    return None

def row_to_letter(val) -> str:
    try:
        i = int(val)
        return string.ascii_uppercase[i-1]
    except Exception:
        return str(val)

def label_phase_for_tp(tp: int, stim_tps: List[int], baseline_range: Optional[Tuple[int,int]]) -> str:
    if baseline_range and tp < baseline_range[0]: return "pre-baseline"
    if baseline_range and baseline_range[0] <= tp <= baseline_range[1]: return "baseline"
    if not stim_tps: return "post-baseline"
    idx = None
    for i, st in enumerate(sorted(stim_tps), start=1):
        if tp >= st: idx = i
        else: break
    if idx is None: return "post-baseline-pre-stim"
    return f"stim{idx}"


def build_tp_mapping(df: pd.DataFrame) -> List[int]:
    for cand in ["Timepoint", "timepoint", "Frame", "frame"]:
        if cand in df.columns:
            ser = pd.to_numeric(df[cand], errors="coerce")
            if ser.notna().all(): return ser.astype(int).tolist()
    time_candidates = [c for c in df.columns if c.strip().lower() in {"time [s]", "time (s)", "time_s", "time"}]
    if time_candidates:
        tcol = time_candidates[0]
        ser = pd.to_numeric(df[tcol], errors="coerce")
        order_idx = ser.sort_values(kind="mergesort").index
        ranks = pd.Series(range(1, len(df) + 1), index=order_idx).sort_index()
        return ranks.tolist()
    return list(range(1, len(df)+1))

def first_tp_by_phase(df: pd.DataFrame, time_col: str, seq_col: str) -> dict[int,int]:
    """
    Group by time_col, pick the modal (or first) seq_col value, then
    record the first timepoint at which each sequence appears.
    """
    # Map each timepoint → sequence
    gb = df.groupby(time_col)[seq_col].apply(mode_or_first)
    tp_to_seq = gb.to_dict()
    first_by_seq: dict[int,int] = {}
    for tp in sorted(tp_to_seq):
        seq = tp_to_seq[tp]
        if seq not in first_by_seq:
            first_by_seq[seq] = tp
    return first_by_seq