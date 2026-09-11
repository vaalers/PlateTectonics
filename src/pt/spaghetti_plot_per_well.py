# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Dict, List, Optional, Tuple
import argparse, re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pt.pt_utils import find_col, normalize_cols, OKABE_ITO, stims_from_df

# =============================================================================
# Optional utils (uses pt_utils if present; safe fallbacks otherwise)
# =============================================================================
try:
    # package-style import
    from pt.pt_utils import normalize_well_str as _vw_norm_str  # type: ignore
except Exception:
    try:
        # script-style import
        from pt.pt_utils import normalize_well_str as _vw_norm_str  # type: ignore
    except Exception:
        _vw_norm_str = None

ROW_LETTERS = "ABCDEFGH"

def normalize_well_str(s: str) -> str:
    if _vw_norm_str is not None:
        return _vw_norm_str(s)
    s2 = str(s).strip().upper()
    return re.sub(r"^([A-H])0?(\d{1,2})$", r"\1\2", s2)

def normalize_well_column_inplace(df: pd.DataFrame, col: str) -> None:
    df[col] = df[col].astype(str).map(normalize_well_str)


# =============================================================================
# Context + filename helpers
# =============================================================================

def extract_context_from_path(path: Path) -> tuple[str, str, str]:
    """
    Extract (date6, experiment_number, evaluation_number) from a path.
    - date: first folder name that is exactly 6 digits (e.g., 081825)
    - experiment: Experiment_<date>_<num> or Experiment_<num>
    - evaluation: Evaluation<num>
    Returns ("NA" placeholders when not found).
    """
    parts = list(path.parts)
    date6 = "NA"
    ex_num = "NA"
    ev_num = "NA"

    for p in parts:
        if re.fullmatch(r"\d{6}", str(p)):
            date6 = str(p)
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


def build_filename_prefix(date6: str, ex_num: str, ev_num: str) -> str:
    """
    Build a consistent filename prefix from context.
    Format: {date6}_Ex{ex_num}_Ev{ev_num}
    Falls back to 'unknown' if none are found.
    """
    parts = []
    if date6 != "NA":
        parts.append(date6)
    if ex_num != "NA":
        parts.append(f"Ex{ex_num}")
    if ev_num != "NA":
        parts.append(f"Ev{ev_num}")
    return "_".join(parts) if parts else "unknown"


def prefer_filtered_xlsx(xlsx_path: Path) -> Path:
    if xlsx_path.name.endswith("_filtered_objects.xlsx"):
        return xlsx_path
    date6, ex_num, ev_num = extract_context_from_path(xlsx_path)
    if "NA" in (date6, ex_num, ev_num):
        return xlsx_path
    candidate = xlsx_path.parent / f"Experiment_{date6}_{ex_num}_Evaluation{ev_num}_filtered_objects.xlsx"
    if candidate.exists():
        print(f"[info] Using included workbook: {candidate}")
        return candidate
    return xlsx_path


def well_file_prefix(base_prefix: str, well: str) -> str:
    base = base_prefix if base_prefix else "unknown"
    well_clean = str(well).strip() or "well"
    return f"{base}_{well_clean}"


def mean_png_path(outdir: Path, base_prefix: str, well: str) -> Path:
    return outdir / f"{well_file_prefix(base_prefix, well)}__mean.png"


def objects_png_path(outdir: Path, base_prefix: str, well: str) -> Path:
    return outdir / f"{well_file_prefix(base_prefix, well)}__objects.png"


# =============================================================================
# Small utils
# =============================================================================


def parse_bbox_area(val):
    try:
        nums = re.findall(r"-?\d+\.?\d*", str(val))
        if len(nums) >= 4:
            x1, y1, x2, y2 = map(float, nums[:4])
            w = max(x2 - x1, 0.0)
            h = max(y2 - y1, 0.0)
            area = w * h
            return area if np.isfinite(area) else np.nan
    except Exception:
        pass
    return np.nan

def iqr_mask(df, col, by=None, k=1.5, upper_only=False):
    s = pd.to_numeric(df[col], errors="coerce")
    if by is None:
        q1 = s.quantile(0.25); q3 = s.quantile(0.75); iqr = q3 - q1
        lo = -np.inf if upper_only else (q1 - k*iqr); hi = q3 + k*iqr
        return (s <= hi) if upper_only else s.between(lo, hi)
    q = df.groupby(by)[col].quantile([0.25, 0.75]).unstack(level=-1)
    q.columns = ["q1", "q3"]; q["iqr"] = q["q3"] - q["q1"]
    q["lo"] = q["q1"] - (0 if upper_only else k*q["iqr"]); q["hi"] = q["q3"] + k*q["iqr"]
    bounds = q[["lo","hi"]]; df2 = df.join(bounds, on=by)
    return (s <= df2["hi"]) if upper_only else s.between(df2["lo"], df2["hi"])

# =============================================================================
# Column picking & efficient sheet reads
# =============================================================================
TIME_CAND_PAT = [r"^Time\b", r"Time\s*\[\s*s\s*\]"]
INTEN_PATTERNS = [
    r"Intensity.*Region.*Mean(?!.*Mean per Well)",
    r"Intensity.*Mean(?!.*Mean per Well)",
    r"(?i)\bMean intensity\b",
    r"Intensity",
]
WELL_CANDS = ["Well", "Well Name", "WellName"]

def pick_time_col(cols) -> Optional[str]:
    for pat in TIME_CAND_PAT:
        c = find_col(cols, pat)
        if c: return c
    # fallback common names
    for c in ["Time (s)", "Time", "Timepoint"]:
        if c in cols: return c
    return None

def pick_intensity_col(cols) -> Optional[str]:
    for pat in INTEN_PATTERNS:
        c = find_col(cols, pat)
        if c: return c
    # very defensive fallback
    for c in cols:
        if re.search(r"(?i)Intensity", str(c)) and "Mean per Well" not in str(c):
            return c
    return None

def pick_well_col(cols) -> Optional[str]:
    for c in WELL_CANDS:
        if c in cols: return c
    return None

def read_well_sheet_minimal(xls_path: Path, sheet: str) -> tuple[pd.DataFrame, Optional[str], Optional[str], Optional[str]]:
    # tiny header read
    head = pd.read_excel(xls_path, sheet_name=sheet, dtype=str, nrows=5)
    head.columns = normalize_cols(head.columns)
    time_col = pick_time_col(head.columns)
    inten_col = pick_intensity_col(head.columns)
    well_col  = pick_well_col(head.columns)

    usecols = [c for c in [time_col, "Intensity", well_col, "Field", "Object No", "Bounding Box", "phase"] if c and c in head.columns]
    df = pd.read_excel(xls_path, sheet_name=sheet, dtype=str, usecols=usecols if usecols else None)
    df.columns = normalize_cols(df.columns)

    # normalize well if present; else try to synthesize
    if well_col and well_col in df.columns:
        normalize_well_column_inplace(df, well_col)
    else:
        row_c = next((c for c in ["Row","row"] if c in df.columns), None)
        col_c = next((c for c in ["Column","column","Col","col"] if c in df.columns), None)
        if row_c and col_c:
            def _row_to_letter(v):
                s = str(v).strip().upper()
                if s.isdigit():
                    i = int(s)
                    return ROW_LETTERS[i-1] if 1 <= i <= 26 else s
                return s
            def _col_to_int(v):
                m = re.search(r"\d+", str(v)); return int(m.group()) if m else None
            wv = df[row_c].map(_row_to_letter).astype(str)
            cv = df[col_c].map(_col_to_int)
            if cv.notna().any():
                df["Well"] = (wv.fillna("") + cv.fillna("").astype("Int64").astype(str)).map(normalize_well_str)
                well_col = "Well"
    return df, time_col, inten_col, well_col

# =============================================================================
# Stim-times ingestion (from *_stim_times.xlsx with Phase or stim columns)
# =============================================================================

def _collect_from_phase(df: pd.DataFrame,
                        phase_col_candidates: List[str],
                        time_col_candidates: List[str]) -> List[float]:
    cols = normalize_cols(df.columns)
    df = df.copy()
    df.columns = cols

    phase_col = next((c for c in phase_col_candidates if c in df.columns), None)
    time_col  = next((c for c in time_col_candidates if c in df.columns), None)
    if phase_col is None or time_col is None:
        return []

    phase = df[phase_col].astype(str).str.strip().str.lower()
    t     = pd.to_numeric(df[time_col], errors="coerce")

    stim_times: List[float] = []
    for n in range(1, 16):  # support up to stim15
        mask = phase.str.fullmatch(fr"stim\s*{n}") | phase.str.fullmatch(fr"stim{n}")
        if mask.any():
            tn = t[mask].dropna()
            if not tn.empty:
                stim_times.append(float(tn.min()))
    mask_generic = phase.str.fullmatch(r"stim")
    if mask_generic.any():
        tg = t[mask_generic].dropna()
        if not tg.empty:
            stim_times.append(float(tg.min()))
    return sorted(set(stim_times))


# =============================================================================
# F/F0 helpers
# =============================================================================
def compute_baseline_mask(t, stim_time, baseline_n, baseline_window):
    if stim_time is not None and np.isfinite(stim_time):
        if baseline_window and baseline_window > 0:
            return (t >= stim_time - baseline_window) & (t < stim_time)
        return t < stim_time
    uniq = np.sort(pd.unique(t.dropna()))
    if len(uniq) == 0: return pd.Series(False, index=t.index)
    cutoff = uniq[min(len(uniq)-1, baseline_n-1)]
    return t <= cutoff

def ff0_per_roi(kept_df, baseline_mask, prefer_roi=True, background_map=None, well_name=None):
    """
    Compute F/F0 from intensity values, optionally with background subtraction.
    
    Args:
        kept_df: DataFrame with columns 't', 'y', 'Field', 'ObjectNo'
        baseline_mask: Boolean mask for baseline timepoints
        prefer_roi: If True, compute F0 per ROI (Field+ObjectNo), else use global F0
        background_map: Optional dict mapping (well, field) -> background value
        well_name: Well name for background lookup
    """
    df = kept_df.copy()
    if "Field" not in df.columns:  df["Field"] = np.nan
    if "ObjectNo" not in df.columns:
        if "Object No" in df.columns: df["ObjectNo"] = pd.to_numeric(df["Object No"], errors="coerce")
        elif "ObjectNo" in df.columns: pass
        else: df["ObjectNo"] = np.nan
    df["is_base"] = baseline_mask

    # Apply background subtraction if background_map is provided
    if background_map is not None and well_name is not None:
        df["background"] = df.apply(
            lambda row: background_map.get((well_name, int(row["Field"])), 0.0) if pd.notna(row["Field"]) else 0.0,
            axis=1
        )
        df["y_corr"] = df["y"] - df["background"]
    else:
        df["y_corr"] = df["y"]
    
    # Use background-corrected values for F/F0 calculation
    y_col = "y_corr" if "y_corr" in df.columns else "y"

    if prefer_roi and df["is_base"].any():
        f0_roi = (df[df["is_base"]]
                  .groupby(["Field","ObjectNo"])[y_col].mean()
                  .rename("F0"))
        df = df.join(f0_roi, on=["Field","ObjectNo"])
    else:
        df["F0"] = np.nan

    if df["is_base"].any():
        F0_global = df.loc[df["is_base"], y_col].mean()
    else:
        F0_global = df[y_col].head(3).mean()
    df["F0"] = df["F0"].fillna(F0_global)

    df["F_over_F0"] = df[y_col] / df["F0"]
    df = df.dropna(subset=["t","F_over_F0"])
    return df[["t","F_over_F0","Field","ObjectNo"]]

# =============================================================================
# Per-well preprocessing
# =============================================================================

def _debug_missing(well_name, df, needed):
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"  [{well_name}] missing columns: {missing}")
    else:
        print(f"  [{well_name}] all columns present; "
              f"rows={len(df)}, finite rows={(~df.isna()).all(1).sum()}")

def preprocess_df_for_well(df, *, size_k, inten_k, scope, stim_time, baseline_n, baseline_window):
    df = df.copy()
    df.columns = normalize_cols(df.columns)
    time_col = find_col(df.columns, r"^Time\b|Time\s*\[\s*s\s*\]") or "T"
    bbox_col = find_col(df.columns, r"^Bounding Box$")
    inten_col = next((c for c in df.columns
                      if re.search(r"Intensity", c, flags=re.I) is not None), None)
    if inten_col is None:
        inten_col = find_col(df.columns, r"Intensity")
    if time_col is None or inten_col is None:
        return None, None, None, None

    t  = pd.to_numeric(df.get(time_col), errors="coerce")
    y  = pd.to_numeric(df.get(inten_col), errors="coerce")
    field_series = pd.to_numeric(df["Field"], errors="coerce") if "Field" in df.columns else pd.Series(np.nan, index=df.index)
    obj_series   = (pd.to_numeric(df["Object No"], errors="coerce") if "Object No" in df.columns
                    else pd.to_numeric(df["ObjectNo"], errors="coerce") if "ObjectNo" in df.columns
                    else pd.Series(np.nan, index=df.index))

    dd = pd.DataFrame({"t": t, "y": y, "Field": field_series, "ObjectNo": obj_series})
    dd["area"] = df[bbox_col].apply(parse_bbox_area) if bbox_col is not None else np.nan
    dd = dd.dropna(subset=["t","y"])
    if dd.empty:
        return None, None, None, None

    group_key = "t" if scope == "timepoint" else None
    size_mask  = iqr_mask(dd, "area", by=group_key, k=size_k, upper_only=False) if dd["area"].notna().any() else pd.Series(True, index=dd.index)
    inten_mask = iqr_mask(dd, "y",    by=group_key, k=inten_k, upper_only=True)
    kept = dd[(size_mask & inten_mask).fillna(False)]

    # inside preprocess_df_for_well, just before returning None:
    _needed = ["Well", "ROI", "t", "F"]  # adjust if your canonical time/value names differ
    if kept.empty:
        return None, None, None, None

    base_mask = compute_baseline_mask(kept["t"], stim_time, baseline_n, baseline_window)
    return kept, base_mask, time_col, inten_col

# =============================================================================
# Plotting
# =============================================================================
def _draw_stim_vlines(ax, stim_timepoints: List[float]):
    for i, s in enumerate(sorted(stim_timepoints)):
        color = "#444444" if i == 0 else "#888888"
        ax.axvline(float(s), linestyle=":", linewidth=1.2, alpha=0.95, color=color, zorder=5)

def plot_well_mean_shaded(
    df, well_name, outdir, *,
    size_k, inten_k, scope, stim_time, stim_list, auc_window,
    baseline_n, baseline_window, prefer_roi_baseline,
    error_mode, yscale, ylim,
    background_map=None,
    filename_prefix: str = ""
):
    kept, base_mask, time_col, _ = preprocess_df_for_well(
        df, size_k=size_k, inten_k=inten_k, scope=scope,
        stim_time=(stim_list[0] if stim_list else stim_time),
        baseline_n=baseline_n, baseline_window=baseline_window
    )
    if kept is None:
        print(f"  [{well_name}] skipped (missing columns or all excluded)")
        return False

    ff0 = ff0_per_roi(kept, base_mask, prefer_roi=prefer_roi_baseline, 
                      background_map=background_map, well_name=well_name)
    grp = ff0.groupby("t")["F_over_F0"].agg(["mean","std","count"]).sort_index()
    grp["sem"] = grp["std"] / np.sqrt(grp["count"]).replace(0, np.nan)
    yerr = grp["sem"].values if error_mode.lower() == "sem" else grp["std"].values
    err_label = "Mean ± SEM" if error_mode.lower() == "sem" else "Mean ± SD"

    x = grp.index.values.astype(float)
    ybar = grp["mean"].values
    lower = ybar - yerr; upper = ybar + yerr
    if yscale == "log":
        eps = 1e-12
        lower = np.clip(lower, eps, None); upper = np.clip(upper, eps, None)

    fig = plt.figure(figsize=(6.5,4.3), dpi=150); ax = plt.gca()
    try: ax.set_yscale(yscale)
    except Exception: ax.set_yscale("linear")
    if ylim: ax.set_ylim(*ylim)

    ax.axhline(1.0, linestyle="--", linewidth=0.9, alpha=0.7, color="#666666", zorder=1)
    line_color = OKABE_ITO[5]
    ax.fill_between(x, lower, upper, alpha=0.25, color=line_color, label=err_label, zorder=2)
    ax.plot(x, ybar, linewidth=1.8, label="Mean", color=line_color, zorder=3)

    if stim_list:
        _draw_stim_vlines(ax, stim_list)
    elif stim_time is not None:
        _draw_stim_vlines(ax, [stim_time])

    ax.set_title(f"Well {well_name}", fontsize=12)
    ax.set_xlabel("Time [s]" if "Time" in time_col else "Timepoint", fontsize=12)
    ax.set_ylabel("F / F0", fontsize=12)
    ax.tick_params(labelsize=12)
    ax.legend(loc="best", frameon=False)
    ax.margins(x=0.05); fig.tight_layout()

    outdir.mkdir(parents=True, exist_ok=True)
    fig.savefig(mean_png_path(outdir, filename_prefix, well_name))
    plt.close(fig)
    return True

def plot_well_per_object(
    ff0, time_col, well_name, outdir, *,
    stim_time, stim_list,
    yscale, ylim, per_object_color, alpha_lines, linewidth, overlay_mean,
    filename_prefix: str = ""
):
    # figure
    fig = plt.figure(figsize=(6.5,4.3), dpi=150); ax = plt.gca()
    try: ax.set_yscale(yscale)
    except Exception: ax.set_yscale("linear")
    if ylim: ax.set_ylim(*ylim)

    ax.axhline(1.0, linestyle="--", linewidth=0.9, alpha=0.7, color="#666666", zorder=1)
    # draw stims
    if stim_list:
        _draw_stim_vlines(ax, stim_list)
    elif stim_time is not None:
        _draw_stim_vlines(ax, [stim_time])

    groups = ff0.groupby(["Field","ObjectNo"])
    palette = OKABE_ITO[1:]  # skip black
    color_single = OKABE_ITO[5]

    for i, ((fld, obj), g) in enumerate(groups):
        g = g.sort_values("t")
        color = (palette[i % len(palette)] if per_object_color == "cycle" else color_single)
        ax.plot(g["t"].values, g["F_over_F0"].values,
                linewidth=linewidth, alpha=alpha_lines, color=color, zorder=2)

    if overlay_mean:
        grp = ff0.groupby("t")["F_over_F0"].mean().sort_index()
        ax.plot(grp.index.values.astype(float), grp.values,
                linewidth=2.0, color=OKABE_ITO[2], zorder=3, label="Mean")

    ax.set_title(f"Well {well_name}  (n ROIs = {groups.ngroups})", fontsize=12)
    ax.set_xlabel("Time [s]" if "Time" in time_col else "Timepoint", fontsize=12)
    ax.set_ylabel("F / F0", fontsize=12)
    ax.tick_params(labelsize=12)
    if overlay_mean:
        ax.legend(loc="best", frameon=False)
    ax.margins(x=0.05); fig.tight_layout()

    outdir.mkdir(parents=True, exist_ok=True)
    fig.savefig(objects_png_path(outdir, filename_prefix, well_name))
    plt.close(fig)
    return True


def _prepare_ff0_for_well(
    df, well_name, *,
    size_k, inten_k, scope, stim_time,
    baseline_n, baseline_window, prefer_roi_baseline,
    background_map=None
) -> tuple[pd.DataFrame | None, str | None]:
    kept, base_mask, time_col, _ = preprocess_df_for_well(
        df, size_k=size_k, inten_k=inten_k, scope=scope,
        stim_time=stim_time, baseline_n=baseline_n, baseline_window=baseline_window
    )
    if kept is None:
        print(f"  [{well_name}] skipped (missing columns or all excluded)")
        return None, None

    if not kept["ObjectNo"].notna().any():
        print(f"  [{well_name}] no identifiable objects (Object No).")
        return None, None

    ff0 = ff0_per_roi(kept, base_mask, prefer_roi=prefer_roi_baseline,
                      background_map=background_map, well_name=well_name)
    return ff0, time_col


def _fov_png_path(outdir: Path, base_prefix: str, well: str, fov: int | float) -> Path:
    return outdir / f"{well_file_prefix(base_prefix, well)}__fov_{int(fov)}.png"


def plot_fov_per_object(
    ff0, time_col, well_name, fov, outdir, *,
    stim_time, stim_list,
    yscale, ylim, per_object_color, alpha_lines, linewidth, overlay_mean,
    filename_prefix: str = ""
):
    g = ff0[ff0["Field"] == fov].copy()
    if g.empty:
        return False

    fig = plt.figure(figsize=(6.5,4.3), dpi=150); ax = plt.gca()
    try: ax.set_yscale(yscale)
    except Exception: ax.set_yscale("linear")
    if ylim: ax.set_ylim(*ylim)

    ax.axhline(1.0, linestyle="--", linewidth=0.9, alpha=0.7, color="#666666", zorder=1)
    if stim_list:
        _draw_stim_vlines(ax, stim_list)
    elif stim_time is not None:
        _draw_stim_vlines(ax, [stim_time])

    groups = g.groupby(["Field","ObjectNo"])
    palette = OKABE_ITO[1:]
    color_single = OKABE_ITO[5]
    for i, ((_fld, _obj), sub) in enumerate(groups):
        sub = sub.sort_values("t")
        color = (palette[i % len(palette)] if per_object_color == "cycle" else color_single)
        ax.plot(sub["t"].values, sub["F_over_F0"].values,
                linewidth=linewidth, alpha=alpha_lines, color=color, zorder=2)

    if overlay_mean:
        grp = g.groupby("t")["F_over_F0"].mean().sort_index()
        ax.plot(grp.index.values.astype(float), grp.values,
                linewidth=2.0, color=OKABE_ITO[2], zorder=3, label="Mean")

    ax.set_title(f"Well {well_name} FOV {int(fov)}  (n ROIs = {groups.ngroups})", fontsize=12)
    ax.set_xlabel("Time [s]" if "Time" in time_col else "Timepoint", fontsize=12)
    ax.set_ylabel("F / F0", fontsize=12)
    ax.tick_params(labelsize=12)
    if overlay_mean:
        ax.legend(loc="best", frameon=False)
    ax.margins(x=0.05); fig.tight_layout()

    outdir.mkdir(parents=True, exist_ok=True)
    fig.savefig(_fov_png_path(outdir, filename_prefix, well_name, fov))
    plt.close(fig)
    return True

# =============================================================================
# Global ylim helpers
# =============================================================================
EPS = 1e-8  # for safe division

def safe_ff0(f, f0):
    # clamp tiny/negative F0 after background subtraction
    f0 = np.where(~np.isfinite(f0) | (f0 <= 0), EPS, f0)
    return f / f0

def finite_minmax(values):
    v = pd.Series(values).replace([np.inf, -np.inf], np.nan).dropna()
    if v.empty:
        return None, None
    return float(v.min()), float(v.max())


def _pad_limits(ymin, ymax, pad, yscale):
    if not np.isfinite(ymin) or not np.isfinite(ymax) or ymin == np.inf or ymax == -np.inf:
        return None
    if yscale == "log":
        eps = 1e-12
        ymin = max(ymin, eps)
        logmin, logmax = np.log10(ymin), np.log10(ymax)
        span = max(logmax - logmin, 1e-6)
        logmin -= pad * span
        logmax += pad * span
        return (10 ** logmin, 10 ** logmax)
    else:
        span = max(ymax - ymin, 1e-9)
        return (ymin - pad * span, ymax + pad * span)


def compute_global_ylim(xls_path, sheets, *, mode, error_mode, yscale,
                        size_k, inten_k, scope, stim_time, baseline_n, baseline_window, prefer_roi_baseline,
                        background_map=None):
    y_min, y_max = np.inf, -np.inf
    for sheet in sheets:
        df = pd.read_excel(xls_path, sheet_name=sheet, dtype=str)
        kept, base_mask, _, _ = preprocess_df_for_well(
            df, size_k=size_k, inten_k=inten_k, scope=scope,
            stim_time=stim_time, baseline_n=baseline_n, baseline_window=baseline_window
        )
        if kept is None:
            continue
        well_name = sheet.strip()
        ff0 = ff0_per_roi(kept, base_mask, prefer_roi=prefer_roi_baseline,
                          background_map=background_map, well_name=well_name)
        if mode == "mean":
            grp = ff0.groupby("t")["F_over_F0"].agg(["mean","std","count"]).sort_index()
            grp["sem"] = grp["std"] / np.sqrt(grp["count"]).replace(0, np.nan)
            yerr = grp["sem"] if error_mode == "sem" else grp["std"]
            lower = (grp["mean"] - yerr).min()
            upper = (grp["mean"] + yerr).max()
            y_min = min(y_min, lower)
            y_max = max(y_max, upper)
        else:
            # per-object: use all F/F0
            well_name = sheet.strip()
            ff0 = ff0_per_roi(kept, base_mask, prefer_roi=prefer_roi_baseline,
                              background_map=background_map, well_name=well_name)
            y_min = min(y_min, ff0["F_over_F0"].min())
            y_max = max(y_max, ff0["F_over_F0"].max())
    if y_min == np.inf or y_max == -np.inf:
        return None
    return _pad_limits(y_min, y_max, pad=0.0, yscale=yscale)  # no pad here; we'll add user pad later


# =============================================================================
# Faceting
# =============================================================================
def build_faceted_ppt(png_paths, dest_path):
    try:
        from pptx import Presentation
        from pptx.util import Inches
    except Exception:
        print("  [facet] python-pptx not installed. Install with:  python3 -m pip install python-pptx")
        return False

    prs = Presentation()
    prs.slide_width  = Inches(13.33)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)

    n = len(png_paths)
    if n == 0:
        return False
    rows = int(np.floor(np.sqrt(n)))
    cols = int(np.ceil(n / rows))

    margin = Inches(0.3); hgap = Inches(0.1); vgap = Inches(0.1)
    cell_w = int(round((prs.slide_width  - 2*margin - (cols-1)*hgap) / cols))
    cell_h = int(round((prs.slide_height - 2*margin - (rows-1)*vgap) / rows))

    for idx, p in enumerate(png_paths):
        r, c = divmod(idx, cols)
        left = int(margin + c * (cell_w + hgap))
        top  = int(margin + r * (cell_h + vgap))
        pic = slide.shapes.add_picture(str(p), left, top, width=cell_w)
        if pic.height > cell_h:
            scale = cell_h / float(pic.height)
            new_w = int(round(pic.width * scale))
            pic.height = cell_h
            pic.width  = new_w
            pic.left   = int(left + (cell_w - new_w) / 2)
        pic.top = int(top + (cell_h - pic.height) / 2)
    prs.save(dest_path)
    return True

def build_faceted_png(png_paths, dest_path):
    try:
        from PIL import Image
    except Exception:
        print("  [facet] Pillow not installed. Install with:  python3 -m pip install pillow")
        return False

    n = len(png_paths)
    if n == 0:
        return False
    rows = int(np.floor(np.sqrt(n)))
    cols = int(np.ceil(n / rows))

    tile_w, tile_h = 800, 600
    gap = 10
    W = cols * tile_w + (cols-1)*gap
    H = rows * tile_h + (rows-1)*gap

    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    for idx, p in enumerate(png_paths):
        r, c = divmod(idx, cols)
        x = c * (tile_w + gap); y = r * (tile_h + gap)
        try:
            im = Image.open(p).convert("RGB")
            im.thumbnail((tile_w, tile_h), Image.LANCZOS)
            tw, th = im.size
            cx = x + (tile_w - tw)//2; cy = y + (tile_h - th)//2
            canvas.paste(im, (cx, cy))
        except Exception:
            pass
    canvas.save(dest_path)
    return True

# =============================================================================
# CLI / Main
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Per-well F/F0 plots with outlier removal + stim vlines")
    ap.add_argument("xlsx", help="Path to Excel from phenix_to_xlsx.py (per-well workbook)")
    ap.add_argument("--outdir", help="Where to put PNGs (default: <xlsx_dir>/plots)")
    ap.add_argument("--filename-prefix",
                    help="Override filename prefix; default derives from xlsx path (date + experiment + evaluation)")
    ap.add_argument(
        "--both",
        action="store_true",
        help="Generate plots for both raw and included workbooks (if included exists).",
    )

    # Mode: mean±error vs per-object
    ap.add_argument("--mode", choices=["mean","per-object"], default="per-object")
    # Shared options
    ap.add_argument("--size-iqr-k", type=float, default=1.5)
    ap.add_argument("--intensity-iqr-k", type=float, default=1.5)
    ap.add_argument("--scope", choices=["timepoint","well"], default="timepoint")
    ap.add_argument("--stim", type=float, default=None, help="Deprecated single vline (kept for compatibility)")
    ap.add_argument("--baseline-n", type=int, default=3)
    ap.add_argument("--baseline-window", type=float, default=None)
    ap.add_argument("--no-roi-baseline", action="store_true")
    ap.add_argument("--yscale", choices=["linear","log","symlog"], default="linear")
    ap.add_argument("--ylim", type=str, default="-0.5,7.0")

    # Mean-mode only
    ap.add_argument("--error", choices=["sem","sd"], default="sem")

    # Per-object options
    ap.add_argument("--per-object-color", choices=["single","cycle"], default="cycle")
    ap.add_argument("--alpha-lines", type=float, default=0.35)
    ap.add_argument("--linewidth", type=float, default=0.9)
    ap.add_argument("--overlay-mean", action="store_true")

    # Global y-lims and faceting
    ap.add_argument("--same-ylim", action="store_true")
    ap.add_argument("--ylim-pad", type=float, default=0.05)
    ap.add_argument("--facet", action="store_true")
    ap.add_argument("--no-per-fov", action="store_true",
                    help="Skip per-FOV spaghetti plots (per-object mode only).")
    
    # Background subtraction
    ap.add_argument("--background-csv", type=Path,
                    help="Path to CSV with FOV-specific backgrounds (columns: well, field, background)")
    ap.add_argument("--background", type=float, default=0.0,
                    help="Global background value (used if --background-csv not provided or no match found)")

    args = ap.parse_args()

    raw_xlsx = Path(args.xlsx).resolve()
    if not raw_xlsx.is_file():
        raise SystemExit(f"Not found: {raw_xlsx}")

    base_outdir = Path(args.outdir).resolve() if args.outdir else (raw_xlsx.parent / "plots" )
    
    # Load background map if CSV provided
    background_map = None
    if args.background_csv:
        try:
            from pt.per_object_ff0 import load_background_csv
            bg_path = Path(args.background_csv).expanduser().resolve()
            if bg_path.exists():
                background_map, _seq_bg = load_background_csv(bg_path)
                if not background_map and not _seq_bg:
                    print(f"[WARN] No backgrounds loaded from {bg_path}, using global --background value")
            else:
                print(f"[WARN] Background CSV not found: {bg_path}, using global --background value")
        except Exception as e:
            print(f"[WARN] Failed to load background CSV: {e}, using global --background value")

    run_specs = []
    if args.both:
        filtered_xlsx = prefer_filtered_xlsx(raw_xlsx)
        run_specs.append(("raw", raw_xlsx, base_outdir / "raw"))
        if filtered_xlsx.exists() and filtered_xlsx != raw_xlsx:
            run_specs.append(("included", filtered_xlsx, base_outdir / "included"))
        else:
            print("  [warn] No included workbook found; only raw plots will be generated.")
    else:
        use_xlsx = prefer_filtered_xlsx(raw_xlsx)
        run_specs.append(("default", use_xlsx, base_outdir))

    for label, xlsx, outdir in run_specs:
        date6, ex_num, ev_num = extract_context_from_path(xlsx)
        ctx_prefix = args.filename_prefix.strip() if args.filename_prefix else build_filename_prefix(date6, ex_num, ev_num)
        print(f"[{label}] Using filename prefix: {ctx_prefix}")

        # collect sheets (wells)
        xls = pd.ExcelFile(xlsx)
        sheets = [s for s in xls.sheet_names if s != "Plate_Overview"]

        # parse explicit ylim if provided
        explicit_ylim = None
        if args.ylim:
            try:
                y0, y1 = [float(v.strip()) for v in args.ylim.split(",")]
                explicit_ylim = (y0, y1)
            except Exception:
                raise SystemExit("Invalid --ylim format. Use like: --ylim -0.5,5.0")
        # compute global ylim if requested and not explicitly set (fast/column-minimal read)
        global_ylim = None
        if explicit_ylim is None and args.same_ylim:
            gl = compute_global_ylim(
                xlsx, sheets,
                mode=args.mode, error_mode=args.error, yscale=args.yscale,
                size_k=args.size_iqr_k, inten_k=args.intensity_iqr_k, scope=args.scope,
                stim_time=args.stim, baseline_n=args.baseline_n, baseline_window=args.baseline_window,
                prefer_roi_baseline=not args.no_roi_baseline,
                background_map=background_map
            )
            if gl is not None:
                # add user pad
                ymin, ymax = gl
                pad = args.ylim_pad
                if args.yscale == "log":
                    ymin = max(ymin, 1e-12)
                    yspan = np.log10(ymax) - np.log10(ymin)
                    global_ylim = (10 ** (np.log10(ymin) - pad*yspan),
                                   10 ** (np.log10(ymax) + pad*yspan))
                else:
                    yspan = ymax - ymin
                    global_ylim = (ymin - pad*yspan, ymax + pad*yspan)
            else:
                print("  [warn] Could not compute global y-limits; proceeding with auto per-plot limits.")

        ylim_to_use = explicit_ylim if explicit_ylim is not None else global_ylim

        # plot all wells (read each sheet minimally)
        made = 0
        png_paths = []
        for sheet in sheets:
            df, _, _, _ = read_well_sheet_minimal(xlsx, sheet)
            stim_list = stims_from_df(df)  # <-- from this sheet itself

            if args.mode == "mean":
                ok = plot_well_mean_shaded(
                    df, well_name=sheet, outdir=outdir,
                    size_k=args.size_iqr_k, inten_k=args.intensity_iqr_k, scope=args.scope,
                    stim_time=args.stim, stim_list=stim_list, auc_window=None,
                    baseline_n=args.baseline_n, baseline_window=args.baseline_window,
                    prefer_roi_baseline=not args.no_roi_baseline,
                    error_mode=args.error, yscale=args.yscale, ylim=ylim_to_use,
                    background_map=background_map,
                    filename_prefix=ctx_prefix
                )
                if ok: png_paths.append(mean_png_path(outdir, ctx_prefix, sheet))
            else:
                ff0, time_col = _prepare_ff0_for_well(
                    df, sheet,
                    size_k=args.size_iqr_k, inten_k=args.intensity_iqr_k, scope=args.scope,
                    stim_time=args.stim, baseline_n=args.baseline_n, baseline_window=args.baseline_window,
                    prefer_roi_baseline=not args.no_roi_baseline,
                    background_map=background_map
                )
                if ff0 is None or time_col is None:
                    ok = False
                else:
                    ok = plot_well_per_object(
                        ff0, time_col, sheet, outdir,
                        stim_time=args.stim, stim_list=stim_list,
                        yscale=args.yscale, ylim=ylim_to_use,
                        per_object_color=args.per_object_color, alpha_lines=args.alpha_lines,
                        linewidth=args.linewidth, overlay_mean=args.overlay_mean,
                        filename_prefix=ctx_prefix
                    )
                    if ok:
                        png_paths.append(objects_png_path(outdir, ctx_prefix, sheet))
                    if not args.no_per_fov:
                        for fov in sorted(ff0["Field"].dropna().unique()):
                            plot_fov_per_object(
                                ff0, time_col, sheet, fov, outdir,
                                stim_time=args.stim, stim_list=stim_list,
                                yscale=args.yscale, ylim=ylim_to_use,
                                per_object_color=args.per_object_color, alpha_lines=args.alpha_lines,
                                linewidth=args.linewidth, overlay_mean=args.overlay_mean,
                                filename_prefix=ctx_prefix
                            )
            made += int(ok)

        print(f"[{label}] Done. Wrote {made} plot(s) to: {outdir}")

        # faceted outputs
        if args.facet and png_paths:
            facet_dir = outdir / "faceted"
            facet_dir.mkdir(parents=True, exist_ok=True)
            facet_base = ctx_prefix if ctx_prefix else "faceted"
            ppt_path  = facet_dir / f"{facet_base}_faceted_all_wells_1slide.pptx"
            png_path  = facet_dir / f"{facet_base}_faceted_all_wells.png"
            ok_ppt = build_faceted_ppt(png_paths, ppt_path)
            ok_png = build_faceted_png(png_paths, png_path)
            if ok_ppt:
                print(f"  [facet] PowerPoint saved: {ppt_path}")
            if ok_png:
                print(f"  [facet] Mosaic PNG saved: {png_path}")

if __name__ == "__main__":
    main()
