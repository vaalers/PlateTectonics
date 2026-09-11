# -*- coding: utf-8 -*-
import argparse, re, math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pt.pt_utils import find_col, normalize_cols

try:
    from pptx import Presentation
    from pptx.util import Inches
    PPTX_OK = True
except Exception:
    PPTX_OK = False

# ---------- tiny utils ----------


def parse_bbox_area(val):
    try:
        nums = re.findall(r"-?\d+\.?\d*", str(val))
        if len(nums) >= 4:
            x1, y1, x2, y2 = map(float, nums[:4])
            w = max(x2 - x1, 0.0)
            h = max(y2 - y1, 0.0)
            return w * h if np.isfinite(w*h) else np.nan
    except Exception:
        pass
    return np.nan

def iqr_mask(df, col, by=None, k=1.5, upper_only=False):
    s = pd.to_numeric(df[col], errors="coerce")
    if by is None:
        q1 = s.quantile(0.25); q3 = s.quantile(0.75); iqr = q3 - q1
        lo = -np.inf if upper_only else (q1 - k * iqr); hi = q3 + k * iqr
        return (s <= hi) if upper_only else s.between(lo, hi)
    q = df.groupby(by)[col].quantile([0.25, 0.75]).unstack(level=-1)
    q.columns = ["q1","q3"]; q["iqr"] = q["q3"] - q["q1"]
    q["lo"] = q["q1"] - (0 if upper_only else k*q["iqr"]); q["hi"] = q["q3"] + k*q["iqr"]
    bounds = q[["lo","hi"]]; df2 = df.join(bounds, on=by)
    return (s <= df2["hi"]) if upper_only else s.between(df2["lo"], df2["hi"])

# ---------- path context parsing ----------
def extract_context_from_path(path: Path):
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

    # date: any 6-digit folder
    for p in parts:
        m = re.fullmatch(r"\d{6}", str(p))
        if m:
            date6 = m.group(0)
            break

    # experiment
    for p in parts:
        m = re.match(rf"Experiment_(?:{date6}_)?(\d+)$", str(p))
        if m:
            ex_num = m.group(1)
            break

    # evaluation
    for p in parts:
        m = re.match(r"Evaluation(\d+)$", str(p))
        if m:
            ev_num = m.group(1)
            break
    print(f"extracting context from {date6} and {ex_num} and {ev_num}")
    return date6, ex_num, ev_num

def prefix_from_ctx(date6, ex_num, ev_num, well):
    d = date6 if date6 != "NA" else "NA"
    ex = f"Ex{ex_num}" if ex_num != "NA" else "ExNA"
    ev = f"Ev{ev_num}" if ev_num != "NA" else "EvNA"
    return f"{d}_{ex}_{ev}_{well}"

# ---------- baseline/stat helpers ----------
def pick_baseline_times(t_series, stim_time, baseline_n):
    t_clean = np.sort(pd.unique(pd.to_numeric(t_series, errors="coerce").dropna()))
    if len(t_clean) == 0: return np.array([])
    if stim_time is not None and np.isfinite(stim_time):
        pre = t_clean[t_clean < stim_time]
        if len(pre) == 0: return t_clean[:max(1, int(baseline_n))]
        return pre[-int(baseline_n):] if len(pre) > baseline_n else pre
    return t_clean[:max(1, int(baseline_n))]

def window_mask(t, stim_time, stats_window):
    if stim_time is None or not np.isfinite(stim_time) or not stats_window or stats_window <= 0:
        return np.ones_like(t, dtype=bool)
    t0, t1 = float(stim_time), float(stim_time) + float(stats_window)
    return (t >= t0) & (t <= t1)

_trapz = getattr(np, "trapezoid", None) or np.trapz


def safe_auc(x, y, baseline=1.0):
    if len(x) < 2: return np.nan
    return float(_trapz(y - baseline, x))

def safe_slope(x, y):
    if len(x) < 2: return np.nan
    x = np.asarray(x, dtype=float); y = np.asarray(y, dtype=float)
    A = np.vstack([x, np.ones_like(x)]).T
    m, _ = np.linalg.lstsq(A, y, rcond=None)[0]
    return float(m)


# ---------- background loading helper ----------
def load_background_csv(csv_path: Path) -> "tuple[dict, dict]":
    """
    Load background CSV.

    Returns (bg_map, seq_bg_map) where:
      bg_map     : (well, field) -> background value
                   (used as fallback when no per-sequence data exist)
      seq_bg_map : (well, field, sequence) -> background value
                   (non-empty only when the CSV has a 'sequence' column,
                    as produced by compute_fov_background_avg.py)

    Required CSV columns: well, field, background
    Optional column:      sequence  (integer: 2=Baseline, 3=Stim1, 4=Stim2)
    """
    try:
        bg_df = pd.read_csv(csv_path)
        bg_df.columns = [c.strip().lower() for c in bg_df.columns]

        well_col  = next((c for c in bg_df.columns if c in ["well", "well_name"]), None)
        field_col = next((c for c in bg_df.columns if c in ["field", "fov"]), None)
        bg_col    = next((c for c in bg_df.columns if c in ["background", "bg", "background_value"]), None)
        seq_col   = next((c for c in bg_df.columns if c in ["sequence"]), None)

        if not all([well_col, field_col, bg_col]):
            print(f"[WARN] Background CSV missing required columns (well, field, background). "
                  f"Found: {list(bg_df.columns)}")
            return {}, {}

        bg_map = {}
        seq_bg_map = {}
        for _, row in bg_df.iterrows():
            well = str(row[well_col]).strip()
            try:
                field = int(float(row[field_col]))
            except (ValueError, TypeError):
                continue
            try:
                bg_val = float(row[bg_col])
            except (ValueError, TypeError):
                continue

            # Per-sequence map
            if seq_col is not None:
                try:
                    seq = int(float(row[seq_col]))
                    seq_bg_map[(well, field, seq)] = bg_val
                except (ValueError, TypeError):
                    pass

            # Fallback: use Baseline (seq 2) row, or last row wins
            if seq_col is None:
                bg_map[(well, field)] = bg_val
            else:
                # Prefer the Baseline entry as the fallback background
                try:
                    seq = int(float(row[seq_col]))
                    if seq == 2 or (well, field) not in bg_map:
                        bg_map[(well, field)] = bg_val
                except (ValueError, TypeError):
                    bg_map[(well, field)] = bg_val

        if seq_bg_map:
            print(f"[background] Loaded {len(seq_bg_map)} well/field/sequence backgrounds "
                  f"from {csv_path.name}")
        else:
            print(f"[background] Loaded {len(bg_map)} well/field backgrounds from {csv_path.name}")
        return bg_map, seq_bg_map
    except Exception as e:
        print(f"[WARN] Failed to load background CSV {csv_path}: {e}")
        return {}, {}


# ---------- main per-well worker ----------
def plot_well_objects(
    df, well_name, outdir, file_prefix, stim_summary=None,
    background=0.0, baseline_n=3,
    background_map=None,       # dict (well, field) -> background value
    seq_background_map=None,   # dict (well, field, sequence) -> background value
    size_k=1.5, inten_k=1.5, scope="timepoint",
    disable_iqr_filters=False,
    stim_time=None, stats_window=30.0,
    ylim=(-0.5, 5.0), min_points=2,
    per_page=20, ncols=5,
    ppt_width=13.33, ppt_height=7.5,
    no_single_pngs=False, write_pptx=False,
    write_object_csvs=True,
    return_augmented=False,
    make_plots=True,
    stim_ref_col=None
):
    df = df.copy()
    df.columns = normalize_cols(df.columns)
    stim2_time = None

    # Robust time column detection: Time, Time [s], Time (s), Timepoint, Frame, any 'time' substring
    time_col = (
        find_col(df.columns, r"^Time\s*(\[\s*s\s*\]|\(\s*s\s*\))?$")
        or find_col(df.columns, r"^Timepoint$")
        or find_col(df.columns, r"^Frame$")
        or find_col(df.columns, r"^Time$")
        or find_col(df.columns, r"time", ignore_case=True)
    )
    # bounding box column detection (more forgiving)
    bbox_col = find_col(df.columns, r"^Bounding Box$") or find_col(df.columns, r"Bounding\s*Box", ignore_case=True)
    inten_col = None
    # Prefer object-level intensity columns; exclude any aggregate like "per well" (case-insensitive)
    candidates = [
        c for c in df.columns
        if (
            re.search(r"Intensity.*Region", c, flags=re.I) and re.search(r"Mean", c, flags=re.I)
        ) and not re.search(r"per\s*well|mean\s*per\s*well", c, flags=re.I)
    ]
    if candidates:
        inten_col = candidates[0]
    else:
        # Fallbacks: any non-aggregate intensity metric columns
        fallbacks = [
            c for c in df.columns
            if (
                re.search(r"Intensity|Signal", c, flags=re.I)
                and (re.search(r"Mean|Median|Average|Integrated|Sum", c, flags=re.I) or re.fullmatch(r"Intensity", c, flags=re.I))
            ) and not re.search(r"per\s*well|mean\s*per\s*well", c, flags=re.I)
        ]
        # Prefer exact 'Intensity' if present, else first fallback
        exact_intensity = next((c for c in fallbacks if re.fullmatch(r"Intensity", c, flags=re.I)), None)
        inten_col = exact_intensity or (fallbacks[0] if fallbacks else None)
    if time_col is None or inten_col is None:
        print(f"  [{well_name}] skipped (missing time or intensity)")
        # debug: show normalized headers once per skipped well
        try:
            print(f"    headers: {list(df.columns)}")
        except Exception:
            pass
        return 0, pd.DataFrame(), [], None, pd.DataFrame()

    t  = pd.to_numeric(df.get(time_col), errors="coerce")
    y  = pd.to_numeric(df.get(inten_col), errors="coerce")

    # fallback: derive stim2_time from per-row phase labels if present
    if stim2_time is None and "phase" in df.columns:
        try:
            phase_ser = df["phase"].astype(str)
            stim2_mask = phase_ser.str.contains("stim2", case=False, na=False)
            if stim2_mask.any():
                stim2_times = pd.to_numeric(t[stim2_mask], errors="coerce").dropna()
                if not stim2_times.empty:
                    stim2_time = float(stim2_times.min())
        except Exception:
            pass
    field_series = pd.to_numeric(df.get("Field"), errors="coerce") if "Field" in df.columns else pd.Series(np.nan, index=df.index)
    obj_series = (pd.to_numeric(df.get("Object No"), errors="coerce") if "Object No" in df.columns
                  else pd.to_numeric(df.get("ObjectNo"), errors="coerce") if "ObjectNo" in df.columns
                  else pd.Series(np.nan, index=df.index))

    dd = pd.DataFrame({"t": t, "y": y, "Field": field_series, "ObjectNo": obj_series})

    # Prepare augmented output with filter reasons
    invalid_keys_mask = dd[["t","y","Field","ObjectNo"]].isna().any(axis=1)
    # Drop invalid rows before type coercion to avoid casting NaNs to int
    dd = dd.dropna(subset=["t","y","Field","ObjectNo"])
    dd["Field"] = dd["Field"].astype(int); dd["ObjectNo"] = dd["ObjectNo"].astype(int)
    if return_augmented:
        # initialize filter_reason on a full copy of original shape (created shortly below)
        pass
    if dd.empty:
        print(f"  [{well_name}] skipped (no valid Field+ObjectNo)")
        return 0, pd.DataFrame(), [], None, pd.DataFrame()

    dd["area"] = df.loc[dd.index, bbox_col].apply(parse_bbox_area) if bbox_col is not None else np.nan

    # determine effective stim time
    # Priority: (1) phase column, (2) stim_time_s column, (3) stim_summary, (4) CLI --stim
    stim_time_eff = None
    stim1_time = None
    stim2_time = None

    def _first_numeric(val):
        ser = pd.to_numeric(pd.Series([val]), errors="coerce").dropna()
        return float(ser.iloc[0]) if not ser.empty else None

    # 1) phase column (set by detect_stim_times): highest priority
    if "phase" in df.columns:
        try:
            phase_ser = df["phase"].astype(str)
            s1 = pd.to_numeric(t[phase_ser.str.contains("stim1", case=False, na=False)], errors="coerce").dropna()
            s2 = pd.to_numeric(t[phase_ser.str.contains("stim2", case=False, na=False)], errors="coerce").dropna()
            if not s1.empty:
                stim1_time = float(s1.min())
            if not s2.empty:
                stim2_time = float(s2.min())
            if stim1_time is not None:
                stim_time_eff = stim1_time
        except Exception:
            pass

    # 2) stim_time_s column (also set by detect_stim_times)
    if stim_time_eff is None:
        if "stim_time_s" in df.columns:
            try:
                val = pd.to_numeric(df["stim_time_s"], errors="coerce").dropna()
                if not val.empty:
                    stim_time_eff = float(val.iloc[0])
                    if stim1_time is None:
                        stim1_time = stim_time_eff
            except Exception:
                pass

    # 3) stim_summary table
    if stim_time_eff is None and stim_summary is not None:
        try:
            if "Well" in stim_summary.columns and well_name in stim_summary["Well"].values:
                row = stim_summary[stim_summary["Well"] == well_name].iloc[0]
                # choose column
                candidate_cols = []
                if stim_ref_col and stim_ref_col in row.index:
                    candidate_cols = [stim_ref_col]
                else:
                    # prefer stim1_start_s, then any *_start_s, else baseline_end_s
                    if "stim1_start_s" in row.index:
                        candidate_cols = ["stim1_start_s"]
                    else:
                        start_cols = [c for c in row.index if str(c).endswith("_start_s")]
                        start_cols = sorted(start_cols)
                        candidate_cols = start_cols or (["baseline_end_s"] if "baseline_end_s" in row.index else [])
                for c in candidate_cols:
                    val = pd.to_numeric(pd.Series([row[c]]), errors="coerce").dropna()
                    if not val.empty:
                        stim_time_eff = float(val.iloc[0])
                        if stim1_time is None:
                            stim1_time = stim_time_eff
                        break
        except Exception:
            pass

    # 4) CLI --stim: final fallback when no per-row annotation exists
    if stim_time_eff is None and stim_time is not None and np.isfinite(float(stim_time)):
        stim_time_eff = float(stim_time)

    # attempt to get stim2 start from stim_summary regardless of stim_time_eff
    if stim_summary is not None and stim2_time is None:
        try:
            if "Well" in stim_summary.columns and well_name in stim_summary["Well"].values:
                row = stim_summary[stim_summary["Well"] == well_name].iloc[0]
                stim2_candidates = [c for c in row.index if re.search(r"stim2", str(c), flags=re.I) and re.search(r"start", str(c), flags=re.I)]
                if stim2_candidates:
                    stim2_time = _first_numeric(row[stim2_candidates[0]])
                else:
                    start_cols = [c for c in row.index if str(c).endswith("_start_s")]
                    start_cols = sorted(start_cols)
                    if len(start_cols) >= 2:
                        stim2_time = _first_numeric(row[start_cols[1]])
        except Exception:
            pass
    # final fallback: try to read stim2 start from the well sheet itself (normalized headers)
    if stim2_time is None:
        try:
            stim2_sheet_cols = [c for c in df.columns if re.search(r"stim2", str(c), flags=re.I) and re.search(r"start", str(c), flags=re.I)]
            if stim2_sheet_cols:
                stim2_time = _first_numeric(df[stim2_sheet_cols[0]].iloc[0])
            else:
                start_cols_df = [c for c in df.columns if str(c).endswith("_start_s")]
                start_cols_df = sorted(start_cols_df)
                if len(start_cols_df) >= 2:
                    stim2_time = _first_numeric(df[start_cols_df[1]].iloc[0])
        except Exception:
            pass
    group_key = "t" if scope == "timepoint" else None
    if disable_iqr_filters:
        print(f"  [{well_name}] IQR filtering disabled for size and intensity")
        size_mask = pd.Series(True, index=dd.index)
        inten_mask = pd.Series(True, index=dd.index)
    else:
        size_mask  = iqr_mask(dd, "area", by=group_key, k=size_k, upper_only=False) if dd["area"].notna().any() else pd.Series(True, index=dd.index)
        inten_mask = iqr_mask(dd, "y",    by=group_key, k=inten_k, upper_only=False)
    # responder tuning windows (seconds)
    stim2_pre_window = 15.0
    stim2_post_window = stats_window if stats_window else 30.0
    stim2_min_pre_points = 2

    # Classify IQR failures for annotation
    def classify_iqr_failures(df_mask_base, col, upper_only, by_key):
        reasons = pd.Series("", index=df_mask_base.index)
        if by_key is None:
            s = pd.to_numeric(df_mask_base[col], errors="coerce")
            q1 = s.quantile(0.25); q3 = s.quantile(0.75); iqr = q3 - q1
            lo = -np.inf if upper_only else (q1 - float(size_k if col=="area" else inten_k) * iqr)
            hi = q3 + float(size_k if col=="area" else inten_k) * iqr
            if upper_only:
                reasons[(~df_mask_base.index.isin([])) & (s > hi)] = f"{col}_high_outlier"
            else:
                reasons[s < lo] = f"{col}_low_outlier"
                reasons[s > hi] = f"{col}_high_outlier"
        else:
            # compute bounds per group
            s = pd.to_numeric(df_mask_base[col], errors="coerce")
            q = df_mask_base.groupby(by_key)[col].quantile([0.25, 0.75]).unstack(level=-1)
            q.columns = ["q1","q3"]; q["iqr"] = q["q3"] - q["q1"]
            k_val = (size_k if col=="area" else inten_k)
            q["lo"] = q["q1"] - (0 if upper_only else k_val*q["iqr"]); q["hi"] = q["q3"] + k_val*q["iqr"]
            bounds = q[["lo","hi"]]
            df2 = df_mask_base.join(bounds, on=by_key)
            if upper_only:
                reasons[s > df2["hi"]] = f"{col}_high_outlier"
            else:
                reasons[s < df2["lo"]] = f"{col}_low_outlier"
                reasons[s > df2["hi"]] = f"{col}_high_outlier"
        return reasons

    area_reason = pd.Series("", index=dd.index)
    inten_reason = pd.Series("", index=dd.index)
    if not disable_iqr_filters:
        if dd["area"].notna().any():
            area_reason = classify_iqr_failures(dd, "area", upper_only=False, by_key=group_key)
        inten_reason = classify_iqr_failures(dd, "y", upper_only=True, by_key=group_key)

    # Track IQR failures per row but keep all objects for stats and excluded plots.
    # IQR-flagged objects are plotted in an "excluded" subfolder.
    iqr_inten_fail_rows = ~inten_mask.fillna(False)
    iqr_area_fail_rows = ~size_mask.fillna(False)
    kept = dd.copy()
    if kept.empty:
        print(f"  [{well_name}] skipped (no valid rows)")
        return 0, pd.DataFrame(), [], None, pd.DataFrame()

    # Apply background correction.
    # Priority: (1) per-sequence FOV background, (2) per-FOV background, (3) global scalar.
    if seq_background_map:
        # Assign sequence number per row based on resolved stim times.
        # seq 2 = Baseline (before stim1), seq 3 = Stim1, seq 4 = Stim2.
        t_vals = kept["t"].values.astype(float)
        seq_vals = np.full(len(kept), 2, dtype=int)  # default: Baseline
        if stim_time_eff is not None and np.isfinite(stim_time_eff):
            seq_vals[t_vals >= stim_time_eff] = 3    # Stim1
        if stim2_time is not None and np.isfinite(stim2_time):
            seq_vals[t_vals >= stim2_time] = 4       # Stim2
        kept["_seq"] = seq_vals

        def _seq_bg(row):
            fld = int(row["Field"])
            seq = int(row["_seq"])
            # Try per-sequence, fall back to per-FOV, then global
            v = seq_background_map.get((well_name, fld, seq))
            if v is None and background_map:
                v = background_map.get((well_name, fld))
            return v if v is not None else float(background)

        kept["background"] = kept.apply(_seq_bg, axis=1)
        kept.drop(columns=["_seq"], inplace=True)
        kept["F_corr"] = kept["y"] - kept["background"]
        # Log per-FOV per-sequence backgrounds used
        grp_log = kept.groupby(["Field", kept["t"].apply(
            lambda tv: (2 if (stim_time_eff is None or tv < stim_time_eff)
                        else (4 if (stim2_time is not None and tv >= stim2_time) else 3))
        )])["background"].first()
        print(f"  [{well_name}] Per-sequence backgrounds (field, seq): "
              + ", ".join(f"F{int(f)}/seq{s}={v:.1f}" for (f, s), v in grp_log.items()))
    elif background_map is not None:
        kept["background"] = kept.apply(
            lambda row: background_map.get((well_name, int(row["Field"])), float(background)),
            axis=1
        )
        kept["F_corr"] = kept["y"] - kept["background"]
        unique_bgs = kept["background"].unique()
        if len(unique_bgs) > 1:
            print(f"  [{well_name}] Using FOV-specific backgrounds: "
                  f"{dict(kept.groupby('Field')['background'].first())}")
        else:
            print(f"  [{well_name}] Using background: "
                  f"{float(unique_bgs[0]) if len(unique_bgs) > 0 else float(background)}")
    else:
        kept["background"] = float(background)
        kept["F_corr"] = kept["y"] - float(background)

    # folders — all CSVs flat in outdir/csv/, facets flat in outdir/facets/
    csv_dir = outdir / "csv"
    facets_dir = outdir / "facets"
    csv_dir.mkdir(parents=True, exist_ok=True)
    if make_plots:
        facets_dir.mkdir(parents=True, exist_ok=True)

    facet_items = []           # (Field, ObjectNo, sub_df) – objects that passed all filters
    excluded_facet_items = []  # (Field, ObjectNo, sub_df) – IQR-flagged objects
    well_stats_rows = []
    responder_rows = []
    made = 0

    # Prepare structures for optional augmented output
    augmented_df = None
    if return_augmented:
        augmented_df = df.copy()
        # Initialize columns; we'll fill values only for valid rows
        float_cols = [
            "F_corr","F_over_F0","F0_per_object",
            "background","background_baseline","background_stim1","background_stim2",
            "obj_mean_ff0_win","obj_std_ff0_win","obj_auc_above_1_win",
            "obj_peak_ff0_win","obj_t_peak_abs","obj_t_peak_rel",
            "obj_slope_ff0_win","obj_n_total","obj_n_baseline",
            "obj_baseline_mean_ff0","obj_baseline_std_ff0","obj_responder_threshold",
            "obj_responder_peak_global","obj_responder_peak_after_stim2",
            "obj_baseline_slope_ff0",
            "obj_area"
        ]
        object_cols = [
            "obj_responder_general","obj_responder_stim2","obj_responder_stim1",
            "obj_responder_stim2_status","obj_iqr_flagged"
        ]
        for col in float_cols:
            if col not in augmented_df.columns:
                augmented_df[col] = np.nan
        for col in object_cols:
            if col not in augmented_df.columns:
                augmented_df[col] = pd.Series([np.nan]*len(augmented_df), dtype="object")
            else:
                augmented_df[col] = augmented_df[col].astype("object")
        # Initialize filter_reason column
        if "filter_reason" not in augmented_df.columns:
            augmented_df["filter_reason"] = pd.Series([np.nan]*len(augmented_df), dtype="object")
        else:
            augmented_df["filter_reason"] = augmented_df["filter_reason"].astype("object")

        # 1) invalid keys (pre-dropna)
        orig_invalid = invalid_keys_mask.reindex(augmented_df.index).fillna(True)
        augmented_df.loc[orig_invalid, "filter_reason"] = augmented_df.loc[orig_invalid, "filter_reason"].fillna("invalid_time_or_intensity_or_ids")

        # Populate obj_area for all valid rows (IQR reasoning handled per-object below)
        augmented_df.loc[dd.index, "obj_area"] = dd["area"]

    object_cache = {}
    baseline_ff0_values = []
    for (fld, oid), sub in kept.groupby(["Field","ObjectNo"], sort=True):
        sub = sub.sort_values("t").copy()

        # Determine per-object IQR flags (any row in this object failing IQR -> object is flagged)
        obj_inten_fail = bool(iqr_inten_fail_rows.reindex(sub.index, fill_value=False).any())
        obj_area_fail  = bool(iqr_area_fail_rows.reindex(sub.index, fill_value=False).any())
        obj_iqr_flagged = obj_inten_fail or obj_area_fail
        exclusion_reasons = []
        iqr_flag_parts = []
        if obj_inten_fail:
            _ir = inten_reason.reindex(sub.index, fill_value="intensity_iqr_outlier")
            _first = next((r for r in _ir if r), "intensity_iqr_outlier")
            iqr_flag_parts.append(_first)
        if obj_area_fail:
            _ar = area_reason.reindex(sub.index, fill_value="area_iqr_outlier")
            _first = next((r for r in _ar if r), "area_iqr_outlier")
            iqr_flag_parts.append(_first)
        if iqr_flag_parts:
            exclusion_reasons.extend(iqr_flag_parts)
        iqr_flag_reason = ";".join(iqr_flag_parts) if iqr_flag_parts else ""

        if sub.shape[0] < min_points:
            exclusion_reasons.append("object_min_points")

        if "phase" in sub.columns:
            base_mask = sub["phase"].astype(str).str.lower() == "baseline"
        else:
            baseline_times = pick_baseline_times(sub["t"], stim_time_eff, baseline_n)
            base_mask = sub["t"].isin(baseline_times)

        F0 = pd.to_numeric(sub.loc[base_mask, "F_corr"], errors="coerce").mean()
        valid_f0 = np.isfinite(F0) and F0 != 0
        if not valid_f0:
            exclusion_reasons.append("object_invalid_F0")

        # Per-object baseline Tukey upper fence on raw intensity (requires > 2 baseline points)
        base_inten = pd.to_numeric(sub.loc[base_mask, "y"], errors="coerce").dropna()
        if len(base_inten) <= 2:
            exclusion_reasons.append("baseline_intensity_iqr_outlier")
        else:
            _q1b = base_inten.quantile(0.25); _q3b = base_inten.quantile(0.75)
            _upper_fence_b = _q3b + 1.5 * (_q3b - _q1b)
            if (base_inten > _upper_fence_b).any():
                exclusion_reasons.append("baseline_intensity_iqr_outlier")

            # Per-object baseline stability: std must be < 5% of mean raw intensity
            _base_mean = float(base_inten.mean()); _base_std = float(base_inten.std(ddof=1))
            if _base_mean != 0 and (_base_std / abs(_base_mean)) >= 0.05:
                _cv_pct = (_base_std / abs(_base_mean)) * 100.0
                exclusion_reasons.append(f"baseline_std_>5:cv={_cv_pct:.1f}pct")

        if valid_f0:
            sub["F_over_F0"] = sub["F_corr"] / F0
        else:
            sub["F_over_F0"] = np.nan

        base_vals_ff0 = sub.loc[base_mask, "F_over_F0"].values.astype(float)
        base_mean_ff0 = float(np.nanmean(base_vals_ff0)) if base_vals_ff0.size else np.nan
        base_std_ff0 = float(np.nanstd(base_vals_ff0, ddof=1)) if base_vals_ff0.size > 1 else 0.0
        if base_vals_ff0.size:
            finite_vals = base_vals_ff0[np.isfinite(base_vals_ff0)]
            if finite_vals.size:
                baseline_ff0_values.append(finite_vals)

        # De-duplicate reasons while preserving order for downstream logging.
        filter_reason = ";".join(dict.fromkeys(exclusion_reasons)) if exclusion_reasons else "kept"
        plot_excluded = filter_reason != "kept"
        object_cache[(int(fld), int(oid))] = {
            "sub": sub,
            "F0": float(F0) if np.isfinite(F0) else np.nan,
            "base_mask": base_mask,
            "base_mean_ff0": base_mean_ff0,
            "base_std_ff0": base_std_ff0,
            "obj_iqr_flagged": obj_iqr_flagged,
            "iqr_flag_reason": iqr_flag_reason,
            "filter_reason": filter_reason,
            "plot_excluded": plot_excluded,
        }

    if baseline_ff0_values:
        well_base_vals = np.concatenate(baseline_ff0_values).astype(float, copy=False)
    else:
        well_base_vals = np.array([], dtype=float)
    well_base_mean = float(np.nanmean(well_base_vals)) if well_base_vals.size else np.nan
    well_base_std = float(np.nanstd(well_base_vals, ddof=1)) if well_base_vals.size > 1 else 0.0

    for (fld, oid), obj in object_cache.items():
        sub = obj["sub"]
        F0 = obj["F0"]
        base_mask = obj["base_mask"]
        base_mean_ff0 = obj["base_mean_ff0"]
        base_std_ff0 = obj["base_std_ff0"]
        obj_iqr_flagged = obj["obj_iqr_flagged"]
        iqr_flag_reason = obj["iqr_flag_reason"]
        filter_reason = obj["filter_reason"]
        plot_excluded = obj["plot_excluded"]
        # Use a single per-well baseline mean/std for responder thresholds.
        baseline_threshold = (base_mean_ff0 + 2 * base_std_ff0) if np.isfinite(base_mean_ff0) else np.nan
        peak_global = float(np.nanmax(sub["F_over_F0"].values.astype(float))) if sub["F_over_F0"].notna().any() else np.nan
        if np.isfinite(baseline_threshold) and np.isfinite(peak_global):
            is_general_responder = (peak_global >= baseline_threshold) and (peak_global >= 1.5)
        else:
            is_general_responder = np.nan

        # Stim1 responder: general responder + stim1 peak >= 2.0 + baseline mean within 1 std of well baseline mean.
        stim1_peak = np.nan
        stim1_window_used = False
        if stim1_time is not None and stim2_time is not None and np.isfinite(stim1_time) and np.isfinite(stim2_time):
            stim1_mask = (sub["t"] >= float(stim1_time)) & (sub["t"] < float(stim2_time))
            stim1_vals = sub.loc[stim1_mask, "F_over_F0"].values.astype(float)
            if stim1_vals.size > 0:
                stim1_peak = float(np.nanmax(stim1_vals))
                stim1_window_used = True

        if not stim1_window_used and stim_time_eff is not None and np.isfinite(stim_time_eff):
            if stim2_time is not None and np.isfinite(stim2_time) and stim2_time > stim_time_eff:
                window_end = stim2_time
            else:
                window_end = stim_time_eff + 60.0
            if window_end > stim_time_eff:
                stim1_mask = (sub["t"] >= float(stim_time_eff)) & (sub["t"] < float(window_end))
                stim1_vals = sub.loc[stim1_mask, "F_over_F0"].values.astype(float)
                if stim1_vals.size > 0:
                    stim1_peak = float(np.nanmax(stim1_vals))
                    stim1_window_used = True

        stim1_peak_ok = np.isfinite(stim1_peak) and stim1_peak >= 2.0
        if not pd.isna(is_general_responder) and np.isfinite(base_mean_ff0):
            is_stim1_responder = bool(is_general_responder) and stim1_peak_ok
        else:
            is_stim1_responder = np.nan

        # Stim2-specific responder detection with pre-window sanity check
        stim2_status = "no_stim2"
        is_stim2_responder = np.nan
        peak_after_stim2 = np.nan
        if stim2_time is not None and np.isfinite(stim2_time):
            pre_mask = (sub["t"] >= (stim2_time - stim2_pre_window)) & (sub["t"] < stim2_time)
            post_mask = (sub["t"] >= stim2_time) & (sub["t"] <= (stim2_time + stim2_post_window))
            pre_vals = sub.loc[pre_mask, "F_over_F0"].values.astype(float)
            post_vals = sub.loc[post_mask, "F_over_F0"].values.astype(float)
            if post_vals.size > 0 and pre_vals.size >= stim2_min_pre_points:
                stim2_status = "ok"
                pre_mean = float(np.nanmean(pre_vals)) if pre_vals.size else np.nan
                pre_std = float(np.nanstd(pre_vals, ddof=1)) if pre_vals.size > 1 else 0.0
                pre_thr = (pre_mean + 2 * pre_std) if np.isfinite(pre_mean) else np.nan
                peak_after_stim2 = float(np.nanmax(post_vals)) if post_vals.size else np.nan
                pre_high_frac = float(np.mean(pre_vals >= baseline_threshold)) if np.isfinite(baseline_threshold) and pre_vals.size else 0.0
                if pre_high_frac >= 0.5:
                    stim2_status = "ambiguous_pre_high"
                is_stim2_responder = (
                    np.isfinite(peak_after_stim2)
                    and np.isfinite(baseline_threshold)
                    and peak_after_stim2 >= baseline_threshold
                    and np.isfinite(pre_thr)
                    and peak_after_stim2 >= pre_thr
                )
            elif post_vals.size > 0:
                stim2_status = "pre_window_too_short"
            else:
                stim2_status = "post_window_empty"

        # stats
        mask_win = window_mask(sub["t"].values.astype(float), stim_time_eff, stats_window)
        t_win = sub.loc[mask_win,"t"].values.astype(float)
        ff0_win = sub.loc[mask_win,"F_over_F0"].values.astype(float)

        if len(ff0_win) > 0:
            idx_peak = int(np.nanargmax(ff0_win))
            peak_ff0 = float(ff0_win[idx_peak]); t_peak_abs = float(t_win[idx_peak])
            t_peak_rel = (t_peak_abs - float(stim_time_eff)) if (stim_time_eff is not None and np.isfinite(stim_time_eff)) else np.nan
        else:
            peak_ff0 = np.nan; t_peak_abs = np.nan; t_peak_rel = np.nan

        auc_val = safe_auc(t_win, ff0_win, baseline=1.0)
        slope_val = safe_slope(t_win, ff0_win)
        # Baseline slope (used by downstream exclusion criteria); computed on baseline segment only.
        t_base_slope = sub.loc[base_mask, "t"].values.astype(float)
        ff0_base_slope = sub.loc[base_mask, "F_over_F0"].values.astype(float)
        baseline_slope_ff0 = safe_slope(t_base_slope, ff0_base_slope)
        mean_ff0 = float(np.nanmean(ff0_win)) if len(ff0_win) else np.nan
        std_ff0  = float(np.nanstd(ff0_win, ddof=1)) if len(ff0_win) > 1 else np.nan
        n_total  = int(sub.shape[0]); n_base = int(base_mask.sum())

        # Compute bg_val for this object (used in CSV stats row and well_stats).
        # With per-sequence backgrounds the value varies by timepoint; report the
        # baseline-phase background (seq 2) for the stats summary.
        # Also compute per-phase backgrounds for Baseline, Stim1, Stim2.
        bg_baseline = np.nan
        bg_stim1 = np.nan
        bg_stim2 = np.nan
        if "background" in sub.columns and sub["background"].notna().any():
            if stim_time_eff is not None and np.isfinite(stim_time_eff):
                base_bg = sub.loc[sub["t"] < stim_time_eff, "background"]
                bg_val = float(base_bg.iloc[0]) if not base_bg.empty else float(sub["background"].iloc[0])
                bg_baseline = bg_val
                if stim2_time is not None and np.isfinite(stim2_time):
                    s1_bg = sub.loc[(sub["t"] >= stim_time_eff) & (sub["t"] < stim2_time), "background"]
                    bg_stim1 = float(s1_bg.iloc[0]) if not s1_bg.empty else np.nan
                    s2_bg = sub.loc[sub["t"] >= stim2_time, "background"]
                    bg_stim2 = float(s2_bg.iloc[0]) if not s2_bg.empty else np.nan
                else:
                    s1_bg = sub.loc[sub["t"] >= stim_time_eff, "background"]
                    bg_stim1 = float(s1_bg.iloc[0]) if not s1_bg.empty else np.nan
            else:
                bg_val = float(sub["background"].iloc[0])
                bg_baseline = bg_val
        elif background_map is not None and (well_name, int(fld)) in background_map:
            bg_val = background_map[(well_name, int(fld))]
            bg_baseline = bg_val
        else:
            bg_val = float(background)
            bg_baseline = bg_val

        # per-object CSV (optional)
        if write_object_csvs:
            out_csv = csv_dir / f"{file_prefix}_F{int(fld)}_obj_{int(oid)}.csv"
            out_df = sub[["t","y","F_corr","F_over_F0"]].copy()
            out_df.columns = ["time","raw_intensity","F_corr","F_over_F0"]
            out_df.insert(2,"background", bg_val); out_df.insert(3,"F0", float(F0))
        stats_row = {
            "time":"STATS","raw_intensity":np.nan,"background":bg_val,"F0":float(F0),
            "F_corr":np.nan,"F_over_F0":np.nan,"n_total":n_total,"n_baseline":n_base,
            "mean_ff0_win":mean_ff0,"std_ff0_win":std_ff0,"auc_above_1_win":auc_val,
            "peak_ff0_win":peak_ff0,"t_peak_abs":t_peak_abs,"t_peak_rel":t_peak_rel,
            "slope_ff0_win":slope_val,
            "baseline_slope_ff0": baseline_slope_ff0,
            "baseline_mean_ff0": base_mean_ff0, "baseline_std_ff0": base_std_ff0,
            "baseline_threshold_ff0": baseline_threshold, "peak_global_ff0": peak_global,
            "peak_after_stim2_ff0": peak_after_stim2,
            "is_general_responder": bool(is_general_responder) if not pd.isna(is_general_responder) else np.nan,
            "is_stim2_responder": bool(is_stim2_responder) if not pd.isna(is_stim2_responder) else np.nan,
            "is_stim1_responder": bool(is_stim1_responder) if not pd.isna(is_stim1_responder) else np.nan,
            "stim2_status": stim2_status,
            "filter_reason": filter_reason,
        }
        if write_object_csvs:
            out_df = pd.concat([out_df, pd.DataFrame([stats_row])], ignore_index=True)
            out_df.to_csv(out_csv, index=False)

        # Fill augmented columns for the rows corresponding to this object's indices
        if return_augmented and augmented_df is not None:
            idx = sub.index
            augmented_df.loc[idx, "F_corr"] = sub["F_corr"].values
            augmented_df.loc[idx, "F_over_F0"] = sub["F_over_F0"].values
            augmented_df.loc[idx, "F0_per_object"] = float(F0)
            if "background" in sub.columns:
                augmented_df.loc[idx, "background"] = sub["background"].values
            augmented_df.loc[idx, "background_baseline"] = bg_baseline
            augmented_df.loc[idx, "background_stim1"] = bg_stim1
            augmented_df.loc[idx, "background_stim2"] = bg_stim2
            augmented_df.loc[idx, "obj_mean_ff0_win"] = mean_ff0
            augmented_df.loc[idx, "obj_std_ff0_win"] = std_ff0
            augmented_df.loc[idx, "obj_auc_above_1_win"] = auc_val
            augmented_df.loc[idx, "obj_peak_ff0_win"] = peak_ff0
            augmented_df.loc[idx, "obj_t_peak_abs"] = t_peak_abs
            augmented_df.loc[idx, "obj_t_peak_rel"] = t_peak_rel
            augmented_df.loc[idx, "obj_slope_ff0_win"] = slope_val
            augmented_df.loc[idx, "obj_baseline_slope_ff0"] = baseline_slope_ff0
            augmented_df.loc[idx, "obj_n_total"] = n_total
            augmented_df.loc[idx, "obj_n_baseline"] = n_base
            augmented_df.loc[idx, "obj_baseline_mean_ff0"] = base_mean_ff0
            augmented_df.loc[idx, "obj_baseline_std_ff0"] = base_std_ff0
            augmented_df.loc[idx, "obj_responder_threshold"] = baseline_threshold
            augmented_df.loc[idx, "obj_responder_peak_global"] = peak_global
            augmented_df.loc[idx, "obj_responder_peak_after_stim2"] = peak_after_stim2
            augmented_df.loc[idx, "obj_responder_general"] = bool(is_general_responder) if not pd.isna(is_general_responder) else np.nan
            augmented_df.loc[idx, "obj_responder_stim2"] = bool(is_stim2_responder) if not pd.isna(is_stim2_responder) else np.nan
            if "obj_responder_stim1" not in augmented_df.columns:
                augmented_df["obj_responder_stim1"] = pd.Series([np.nan]*len(augmented_df), dtype="object")
            augmented_df.loc[idx, "obj_responder_stim1"] = bool(is_stim1_responder) if not pd.isna(is_stim1_responder) else np.nan
            augmented_df.loc[idx, "obj_responder_stim2_status"] = str(stim2_status) if stim2_status is not None else np.nan
            augmented_df.loc[idx, "obj_iqr_flagged"] = obj_iqr_flagged
            # filter_reason: IQR-flagged objects get IQR reason; others are "kept"
            augmented_df.loc[idx, "filter_reason"] = augmented_df.loc[idx, "filter_reason"].fillna(filter_reason)

        # single PNG (optional) — two-panel: left=baseline only, right=full trace
        if make_plots and not no_single_pngs and sub["F_over_F0"].notna().any() and not plot_excluded:
            title_suffix = " [IQR-excl]" if obj_iqr_flagged else ""
            line_color = "#E69F00" if obj_iqr_flagged else None
            plot_kwargs = {} if line_color is None else {"color": line_color}

            t_all = sub["t"].values.astype(float)
            ff0_all = sub["F_over_F0"].values.astype(float)
            t_base = sub.loc[base_mask, "t"].values.astype(float)
            ff0_base = sub.loc[base_mask, "F_over_F0"].values.astype(float)

            fig, (ax_base, ax_full) = plt.subplots(1, 2, figsize=(11.0, 3.8), dpi=150)

            # Left: baseline only
            ax_base.plot(t_base, ff0_base, marker="o", linewidth=1.5, **plot_kwargs)
            ax_base.axhline(1.0, linestyle="--", linewidth=1.0, color="gray", alpha=0.7)
            ax_base.set_title(f"{well_name} • F{fld} • Obj{oid}{title_suffix}\nBaseline", fontsize=10)
            ax_base.set_xlabel("Time [s]", fontsize=10)
            ax_base.set_ylabel("F / F0", fontsize=10)
            ax_base.tick_params(labelsize=9)
            if obj_iqr_flagged:
                ax_base.set_ylim(*excluded_ylim)
            elif ylim and len(ylim) == 2:
                ax_base.set_ylim(float(ylim[0]), float(ylim[1]))
            ax_base.margins(x=0.05)

            # Right: full trace (all timepoints)
            ax_full.plot(t_all, ff0_all, marker="o", linewidth=1.5, **plot_kwargs)
            ax_full.axhline(1.5, linestyle="--", linewidth=1.0, color="red", alpha=0.7)
            ax_full.axhline(1.0, linestyle="--", linewidth=1.0, color="gray", alpha=0.7)
            ax_full.set_title("Full trace", fontsize=10)
            ax_full.set_xlabel("Time [s]", fontsize=10)
            ax_full.set_ylabel("F / F0", fontsize=10)
            ax_full.tick_params(labelsize=9)
            if obj_iqr_flagged:
                ax_full.set_ylim(*excluded_ylim)
            elif ylim and len(ylim) == 2:
                ax_full.set_ylim(float(ylim[0]), float(ylim[1]))
            ax_full.margins(x=0.03)

            fig.tight_layout()
            if obj_iqr_flagged:
                _excl_png_dir = outdir / "excluded"
                _excl_png_dir.mkdir(parents=True, exist_ok=True)
                png = _excl_png_dir / f"{file_prefix}_F{int(fld)}_obj_{int(oid)}.png"
            else:
                png = outdir / f"{file_prefix}_F{int(fld)}_obj_{int(oid)}.png"
            fig.savefig(png); plt.close(fig)

        if sub["F_over_F0"].notna().any() and not plot_excluded:
            facet_items.append((int(fld), int(oid), sub[["t","F_over_F0"]].copy()))
            made += 1
        obj_area_mean = float(sub["area"].mean()) if sub["area"].notna().any() else np.nan
        well_stats_rows.append({
            "well": well_name, "Field": int(fld), "ObjectNo": int(oid),
            "iqr_flagged": obj_iqr_flagged,
            "filter_reason": filter_reason,
            "obj_area": obj_area_mean,
            "area_mean": obj_area_mean,
            "background_baseline": bg_baseline,
            "background_stim1": bg_stim1,
            "background_stim2": bg_stim2,
            "F0": float(F0), "n_total": n_total, "n_baseline": n_base,
            "mean_ff0_win": mean_ff0, "std_ff0_win": std_ff0,
            "auc_above_1_win": auc_val, "peak_ff0_win": peak_ff0,
            "t_peak_abs": t_peak_abs, "t_peak_rel": t_peak_rel,
            "slope_ff0_win": slope_val,
            "baseline_slope_ff0": baseline_slope_ff0,
            "baseline_mean_ff0": base_mean_ff0, "baseline_std_ff0": base_std_ff0,
            "baseline_threshold_ff0": baseline_threshold, "peak_global_ff0": peak_global,
            "peak_after_stim2_ff0": peak_after_stim2,
            "is_general_responder": bool(is_general_responder) if not pd.isna(is_general_responder) else np.nan,
            "is_stim2_responder": bool(is_stim2_responder) if not pd.isna(is_stim2_responder) else np.nan,
            "is_stim1_responder": bool(is_stim1_responder) if not pd.isna(is_stim1_responder) else np.nan,
            "stim2_status": stim2_status
        })
        responder_rows.append({
            "well": well_name, "Field": int(fld), "ObjectNo": int(oid),
            "filter_reason": filter_reason,
            "baseline_threshold_ff0": baseline_threshold,
            "peak_global_ff0": peak_global,
            "peak_after_stim2_ff0": peak_after_stim2,
            "baseline_slope_ff0": baseline_slope_ff0,
            "is_general_responder": bool(is_general_responder) if not pd.isna(is_general_responder) else np.nan,
            "is_stim2_responder": bool(is_stim2_responder) if not pd.isna(is_stim2_responder) else np.nan,
            "is_stim1_responder": bool(is_stim1_responder) if not pd.isna(is_stim1_responder) else np.nan,
            "stim2_status": stim2_status
        })

    # well-level summary (named with prefix)
    well_stats_df = pd.DataFrame(well_stats_rows)
    if not well_stats_df.empty:
        well_stats_df.to_csv(csv_dir / f"{file_prefix}__object_stats.csv", index=False)
    responder_df = pd.DataFrame(responder_rows)
    excluded_ylim = (-1.0, 7.0)

    # Faceted pages (PPT-sized); return list of created page PNGs
    page_pngs = []
    if make_plots and facet_items:
        per_page = max(1, int(per_page)); ncols = max(1, int(ncols))
        for page_idx, start in enumerate(range(0, len(facet_items), per_page), start=1):
            chunk = facet_items[start:start+per_page]
            nplots = len(chunk); nrows = int(math.ceil(nplots / ncols))

            fig = plt.figure(figsize=(float(ppt_width), float(ppt_height)), dpi=150)
            for i, (fld, oid, sub) in enumerate(chunk, start=1):
                ax = fig.add_subplot(nrows, ncols, i)
                if stim_time is not None and np.isfinite(stim_time):
                    ax.axvline(float(stim_time), linestyle=":", linewidth=0.8, alpha=0.9)
                # same multi-phase logic for facets
                if stim_summary is not None and well_name in stim_summary['Well'].values:
                    row = stim_summary[stim_summary['Well'] == well_name].iloc[0]
                    for col in sorted(row.index):
                        if col.endswith('_start_s') or col.endswith('_end_s'):
                            t0 = row[col]
                            if pd.notna(t0):
                                ax.axvline(float(t0), linestyle=":", linewidth=0.8, alpha=0.8,
                                            color = 'red' if 'end' in col else 'blue')
                elif stim_time is not None and np.isfinite(stim_time):
                    ax.axvline(float(stim_time), linestyle=":", linewidth=0.8, alpha=0.9)
                ax.plot(sub["t"].values.astype(float), sub["F_over_F0"].values.astype(float), marker="o", linewidth=1.0, markersize=2.8)
                ax.set_title(f"F{fld} Obj{oid}", fontsize=12)
                if ylim and len(ylim)==2: ax.set_ylim(float(ylim[0]), float(ylim[1]))
                ax.tick_params(labelsize=12, length=2)
                if i <= (nrows-1)*ncols: ax.set_xlabel("")
                else: ax.set_xlabel("Time [s]", fontsize=12)
                if (i-1) % ncols != 0: ax.set_ylabel("")
                else: ax.set_ylabel("F/F0", fontsize=12)
                ax.margins(x=0.05)
            fig.tight_layout()

            page_png = (outdir / well_name / "facets" / f"{file_prefix}_page_{page_idx:03d}.png")
            fig.savefig(page_png, dpi=150); plt.close(fig)
            page_pngs.append(page_png)

        # Optional PPTX
        if write_pptx:
            if not PPTX_OK:
                print("  [warn] python-pptx not installed; skipping PPTX.")
            else:
                prs = Presentation()
                # 13.33 x 7.5 inches
                prs.slide_width  = int(13.33 * 914400)
                prs.slide_height = int(7.50 * 914400)
                blank = prs.slide_layouts[6]
                for img in page_pngs:
                    slide = prs.slides.add_slide(blank)
                    slide.shapes.add_picture(str(img), Inches(0), Inches(0), Inches(13.33), Inches(7.5))
                pptx_path = outdir / well_name / f"{file_prefix}.pptx"
                prs.save(str(pptx_path))

    # Excluded objects faceted pages (IQR-flagged objects)
    if make_plots and excluded_facet_items:
        _excl_facets_dir = outdir / "excluded"
        _excl_facets_dir.mkdir(parents=True, exist_ok=True)
        _ep = max(1, int(per_page)); _ec = max(1, int(ncols))
        for page_idx, start in enumerate(range(0, len(excluded_facet_items), _ep), start=1):
            chunk = excluded_facet_items[start:start+_ep]
            nplots = len(chunk); nrows = int(math.ceil(nplots / _ec))
            fig = plt.figure(figsize=(float(ppt_width), float(ppt_height)), dpi=150)
            for i, (fld, oid, sub) in enumerate(chunk, start=1):
                ax = fig.add_subplot(nrows, _ec, i)
                if stim_summary is not None and well_name in stim_summary['Well'].values:
                    row = stim_summary[stim_summary['Well'] == well_name].iloc[0]
                    for col in sorted(row.index):
                        if col.endswith('_start_s') or col.endswith('_end_s'):
                            t0 = row[col]
                            if pd.notna(t0):
                                ax.axvline(float(t0), linestyle=":", linewidth=0.8, alpha=0.8,
                                           color='red' if 'end' in col else 'blue')
                elif stim_time is not None and np.isfinite(stim_time):
                    ax.axvline(float(stim_time), linestyle=":", linewidth=0.8, alpha=0.9)
                ax.plot(sub["t"].values.astype(float), sub["F_over_F0"].values.astype(float),
                        marker="o", linewidth=1.0, markersize=2.8, color="#E69F00")
                ax.set_title(f"F{fld} Obj{oid} [excl]", fontsize=12)
                ax.set_ylim(*excluded_ylim)
                ax.tick_params(labelsize=12, length=2)
                if i <= (nrows-1)*_ec: ax.set_xlabel("")
                else: ax.set_xlabel("Time [s]", fontsize=12)
                if (i-1) % _ec != 0: ax.set_ylabel("")
                else: ax.set_ylabel("F/F0", fontsize=12)
                ax.margins(x=0.05)
            fig.tight_layout()
            excl_png = _excl_facets_dir / f"{file_prefix}_{well_name}_excluded_page_{page_idx:03d}.png"
            fig.savefig(excl_png, dpi=150); plt.close(fig)

    print(f"  [{well_name}] -> {made} kept object(s); {len(excluded_facet_items)} IQR-excluded; pages={len(page_pngs)}")
    return made, well_stats_df, page_pngs, augmented_df, responder_df

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(
        description="Per-object F/F0 fluorescence with background subtraction + stats + faceted PPT-size pages + filename context"
    )
    ap.add_argument("xlsx", help="Path to Excel from phenix_to_xlsx.py")
    ap.add_argument("--outdir", help="Outputs root (default: <xlsx_dir>/plots/individual traces)")

    # naming / context
    ap.add_argument("--context", nargs=3, metavar=("DATE6","EX","EV"),
                    help="Override parsed context, e.g. 081825 2 3")

    # analysis
    ap.add_argument("--background", type=float, default=0.0,
                    help="Global background value (used if --background-csv not provided or no match found)")
    ap.add_argument("--background-csv", type=Path,
                    help="Path to CSV with FOV-specific backgrounds (columns: well, field, background)")
    ap.add_argument("--baseline-n", type=int, default=5)
    ap.add_argument("--size-iqr-k", type=float, default=1.5)
    ap.add_argument("--intensity-iqr-k", type=float, default=1.5)
    ap.add_argument("--no-iqr-filter", action="store_true",
                    help="Disable both size and intensity IQR filtering before per-object F/F0 analysis")
    ap.add_argument("--scope", choices=["timepoint","well"], default="timepoint")
    ap.add_argument("--stim", type=float, default=None)
    ap.add_argument("--stats-window", type=float, default=30.0)
    ap.add_argument("--ylim", nargs=2, type=float, default=[-0.5, 5.0])
    ap.add_argument("--min-points", type=int, default=2)

    # faceting / ppt
    ap.add_argument("--per-page", type=int, default=20)
    ap.add_argument("--ncols", type=int, default=5)
    ap.add_argument("--ppt-width", type=float, default=13.33)
    ap.add_argument("--ppt-height", type=float, default=7.5)
    ap.add_argument("--no-single-pngs", action="store_true")
    ap.add_argument("--pptx", action="store_true",
                    help="Also build a PPTX with one slide per facet page")
    ap.add_argument("--stim-times",
                    help="Annotated stim-times Excel (w/ 'Well', 'baseline_end_s', 'stim1_start_s', etc.)")
    ap.add_argument("--stim-ref-col",
                    help="Column name in --stim-times to use as reference time (e.g., 'stim1_start_s'); if absent, script picks a sensible default per well")
    ap.add_argument("--no-plots", action="store_true",
                    help="Skip all plotting (no single PNGs, no faceted PNGs, no PPTX)")
    ap.add_argument("--xlsx-out",
                    help="If set, write an augmented workbook (copy) with added per-row F_corr/F_over_F0 and a combined Object_Stats sheet")
    ap.add_argument("--no-object-csv", action="store_true",
                    help="Do not write per-object CSV files (useful when --xlsx-out is provided)")

    args = ap.parse_args()

    # --- FIXED: handle the single xlsx path correctly and skip Excel lockfiles ---
    xlsx = Path(args.xlsx).expanduser().resolve()
    if xlsx.name.startswith("~$"):
        raise SystemExit(f"Refusing to open Excel lockfile: {xlsx}")
    if not xlsx.is_file():
        raise SystemExit(f"Not found: {xlsx}")

    base = xlsx.stem
    outdir = Path(args.outdir).expanduser().resolve() if args.outdir else (xlsx.parent / "plots" / "individual")
    outdir.mkdir(parents=True, exist_ok=True)

    # parse context from path or override
    date6, ex_num, ev_num = extract_context_from_path(xlsx)
    if args.context:
        date6, ex_num, ev_num = args.context  # trust user override


    # optional: load stim-times table if provided
    stim_df = None
    if args.stim_times:
        try:
            stim_df = pd.read_excel(Path(args.stim_times).expanduser().resolve())
        except Exception as e:
            print(f"[WARN] Failed to read --stim-times '{args.stim_times}': {e}")
    
    # optional: load background CSV if provided
    background_map = None
    seq_background_map = None
    if args.background_csv:
        bg_path = Path(args.background_csv).expanduser().resolve()
        if bg_path.exists():
            background_map, seq_background_map = load_background_csv(bg_path)
            if not background_map and not seq_background_map:
                print(f"[WARN] No backgrounds loaded from {bg_path}, using global --background value")
        else:
            print(f"[WARN] Background CSV not found: {bg_path}, using global --background value")

    # open workbook and iterate data sheets
    xls = pd.ExcelFile(xlsx)
    made_total = 0
    all_stats = []
    augmented_sheets = {}
    all_responder_rows = []

    for sheet in xls.sheet_names:
        if sheet == "Plate_Overview":
            continue

        well = sheet.strip()
        file_prefix = prefix_from_ctx(date6, ex_num, ev_num, well)

        try:
            df = pd.read_excel(xlsx, sheet_name=sheet, dtype=str)
        except Exception as e:
            print(f"[WARN] Failed to read sheet '{sheet}': {e}")
            continue

        made, well_stats_df, _, augmented_df, responder_df = plot_well_objects(
            df, well_name=well, outdir=outdir, file_prefix=file_prefix,
            background=args.background, baseline_n=args.baseline_n, stim_summary=stim_df,
            background_map=background_map,
            seq_background_map=seq_background_map,
            size_k=args.size_iqr_k, inten_k=args.intensity_iqr_k, scope=args.scope,
            disable_iqr_filters=args.no_iqr_filter,
            stim_time=args.stim, stats_window=args.stats_window,
            ylim=args.ylim, min_points=args.min_points,
            per_page=args.per_page, ncols=args.ncols,
            ppt_width=args.ppt_width, ppt_height=args.ppt_height,
            no_single_pngs=(args.no_plots or args.no_single_pngs), write_pptx=(False if args.no_plots else args.pptx),
            write_object_csvs=(not args.no_object_csv),
            return_augmented=True,
            make_plots=(not args.no_plots),
            stim_ref_col=args.stim_ref_col
        )
        made_total += made
        if well_stats_df is not None and not well_stats_df.empty:
            all_stats.append(well_stats_df)
        if augmented_df is not None:
            augmented_sheets[well] = augmented_df
        if responder_df is not None and not responder_df.empty:
            all_responder_rows.append(responder_df)

    # Decide output workbook path: default into individual traces folder if not provided
    out_xlsx = Path(args.xlsx_out).expanduser().resolve() if args.xlsx_out else (outdir / f"{base}_with_obj.xlsx").resolve()

    # Write augmented workbook copy
    if out_xlsx:
        try:
            with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
                # 1) summary sheets first
                if all_responder_rows:
                    resp_df = pd.concat(all_responder_rows, ignore_index=True)
                    # per-well overview counts
                    overview_rows = []
                    for well, sub in resp_df.groupby("well"):
                        general_total = sub["is_general_responder"].notna().sum()
                        general_resp = int(pd.to_numeric(sub["is_general_responder"], errors="coerce").fillna(0).astype(int).sum())
                        stim2_valid = sub["stim2_status"] == "ok"
                        stim2_ambig = sub["stim2_status"] == "ambiguous_pre_high"
                        stim2_available = stim2_valid | stim2_ambig
                        stim2_resp = int(pd.to_numeric(sub.loc[stim2_valid, "is_stim2_responder"], errors="coerce").fillna(0).astype(int).sum())
                        stim2_non = int(stim2_valid.sum() - stim2_resp)
                        stim1_col = "is_stim1_responder"
                        stim1_resp = int(pd.to_numeric(sub[stim1_col], errors="coerce").fillna(0).astype(int).sum())
                        overview_rows.append({
                            "Well": well,
                            "n_objects": int(sub.shape[0]),
                            "general_responders": general_resp,
                            "general_nonresponders": int(general_total - general_resp),
                            "stim2_responders": stim2_resp,
                            "stim2_nonresponders": stim2_non,
                            "stim2_ambiguous": int(stim2_ambig.sum()),
                            "stim2_unavailable": int(sub.shape[0] - stim2_available.sum()),
                            "stim1_responders": stim1_resp,
                            "stim1_nonresponders": int(sub.shape[0] - stim1_resp)
                        })
                    overview_df = pd.DataFrame(overview_rows)
                    overview_df.to_excel(writer, index=False, sheet_name="Responder_Overview")
                    resp_df.to_excel(writer, index=False, sheet_name="Responder_Object_Level")

                if all_stats:
                    stats_df = pd.concat(all_stats, ignore_index=True)
                    stats_df.to_excel(writer, index=False, sheet_name="Object_Stats")

                # 2) write augmented per-well sheets (or original if augmentation unavailable)
                for sheet in xls.sheet_names:
                    if sheet == "Plate_Overview":
                        # pass-through overview sheet
                        try:
                            pd.read_excel(xlsx, sheet_name=sheet).to_excel(writer, index=False, sheet_name=sheet)
                        except Exception:
                            pass
                        continue
                    if sheet in augmented_sheets:
                        augmented_sheets[sheet].to_excel(writer, index=False, sheet_name=sheet)
                    else:
                        # fallback: write original content untouched
                        try:
                            pd.read_excel(xlsx, sheet_name=sheet).to_excel(writer, index=False, sheet_name=sheet)
                        except Exception:
                            pass
        except Exception as e:
            print(f"[WARN] Failed writing augmented workbook '{out_xlsx}': {e}")

    print(f"Done. Wrote {made_total} object(s) to: {outdir}")

if __name__ == "__main__": main()

