# -*- coding: utf-8 -*-
"""
Post-stats filtering for per_object_ff0.py outputs.

Reads an augmented workbook (e.g., *_with_obj.xlsx) and writes an included
workbook containing only objects that pass QC rules.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MATPLOTLIB_OK = True
except ImportError:
    _MATPLOTLIB_OK = False


# ---- helpers ----

def find_col(columns, pattern, ignore_case=False):
    flags = re.I if ignore_case else 0
    for c in columns:
        if re.search(pattern, str(c), flags=flags):
            return c
    return None


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


def pick_baseline_times(t_series, stim_time, baseline_n):
    t_clean = np.sort(pd.unique(pd.to_numeric(t_series, errors="coerce").dropna()))
    if len(t_clean) == 0:
        return np.array([])
    if stim_time is not None and np.isfinite(stim_time):
        pre = t_clean[t_clean < stim_time]
        if len(pre) == 0:
            return t_clean[: max(1, int(baseline_n))]
        return pre[-int(baseline_n) :] if len(pre) > baseline_n else pre
    return t_clean[: max(1, int(baseline_n))]


def detect_time_col(columns):
    return (
        find_col(columns, r"^Time\s*(\[\s*s\s*\]|\(\s*s\s*\))?$")
        or find_col(columns, r"^Timepoint$")
        or find_col(columns, r"^Frame$")
        or find_col(columns, r"^Time$")
        or find_col(columns, r"time", ignore_case=True)
    )


def detect_field_col(columns):
    return find_col(columns, r"^Field$", ignore_case=True)


def detect_object_col(columns):
    return find_col(columns, r"^Object\s*No$", ignore_case=True) or find_col(
        columns, r"^ObjectNo$", ignore_case=True
    )


def is_finite_series(s):
    return np.isfinite(pd.to_numeric(s, errors="coerce"))


def build_output_name(in_path: Path) -> Path:
    date6, ex_num, ev_num = extract_context_from_path(in_path)
    return in_path.parent / f"Experiment_{date6}_{ex_num}_Evaluation{ev_num}_filtered_objects.xlsx"


def main():
    ap = argparse.ArgumentParser(
        description="Post-stats filtering for per_object_ff0.py augmented outputs"
    )
    ap.add_argument("xlsx", help="Path to augmented workbook (e.g., *_with_obj.xlsx)")
    ap.add_argument("--out", help="Output included workbook path")
    ap.add_argument("--expected-n", type=int, default=27, help="Expected timepoints per object")
    ap.add_argument("--min-n", type=int, default=None,
                    help="Minimum timepoints per object (relaxed check: pass if n >= min-n). "
                         "Overrides --expected-n when provided.")
    ap.add_argument("--peak-threshold", type=float, default=2.0, help="Min peak_global_ff0")
    ap.add_argument("--baseline-sigma", type=float, default=2.0, help="+N*SD baseline outlier threshold")
    ap.add_argument("--baseline-abs", type=float, default=1.9, help="Absolute baseline F_corr outlier threshold")
    ap.add_argument(
        "--baseline-outlier-frac",
        type=float,
        default=0.5,
        help="Exclude object if > this fraction of baseline points are outliers",
    )
    ap.add_argument("--max-ff0-nonfinite", type=int, default=2,
                    help="Allow up to this many non-finite F_over_F0 points per object")
    ap.add_argument("--max-ff0-negative", type=int, default=2,
                    help="Allow up to this many negative F_over_F0 points per object")
    ap.add_argument("--max-fcorr-negative", type=int, default=2,
                    help="Allow up to this many negative F_corr points per object")
    ap.add_argument("--baseline-n-default", type=int, default=5, help="Fallback baseline_n")
    ap.add_argument(
        "--baseline-slope-threshold",
        type=float,
        default=0.02,
        help=(
            "Flag baseline as unstable if the relative change between any two consecutive "
            "baseline F/F0 points exceeds this fraction (default 0.02 = 2%%). "
            "Used only for responder-boolean filtering (baseline_slope_outlier)."
        ),
    )
    ap.add_argument(
        "--responders-only",
        action="store_true",
        help="Keep only general responders (obj_responder_general) and still apply QC.",
    )
    ap.add_argument(
        "--lax-qc",
        action="store_true",
        help="Use more permissive QC thresholds (lower peak threshold, higher outlier tolerances).",
    )
    args = ap.parse_args()

    peak_threshold = args.peak_threshold
    baseline_sigma = args.baseline_sigma
    baseline_abs = args.baseline_abs
    baseline_outlier_frac = args.baseline_outlier_frac
    max_ff0_nonfinite = args.max_ff0_nonfinite
    max_ff0_negative = args.max_ff0_negative
    max_fcorr_negative = args.max_fcorr_negative
    baseline_slope_threshold = args.baseline_slope_threshold
    if args.lax_qc:
        peak_threshold = min(peak_threshold, 1.5)
        baseline_sigma = max(baseline_sigma, 3.0)
        baseline_abs = max(baseline_abs, 2.5)
        baseline_outlier_frac = max(baseline_outlier_frac, 0.75)
        max_ff0_nonfinite = max(max_ff0_nonfinite, 5)
        max_ff0_negative = max(max_ff0_negative, 5)
        max_fcorr_negative = max(max_fcorr_negative, 5)

    in_path = Path(args.xlsx).expanduser().resolve()
    if not in_path.is_file():
        raise SystemExit(f"Not found: {in_path}")

    out_path = Path(args.out).expanduser().resolve() if args.out else build_output_name(in_path)

    xls = pd.ExcelFile(in_path)
    sheet_names = xls.sheet_names

    summary_sheets = {
        "Plate_Overview",
        "Object_Stats",
        "Responder_Overview",
        "Responder_Object_Level",
    }

    per_well_sheets = [s for s in sheet_names if s not in summary_sheets]

    keep_objects = set()
    stats_keep_objects = set()
    object_reasons = []   # hard exclusions
    object_flags = []     # soft flags: timepoint warnings for kept objects
    flag_lookup: dict = {}  # (well, fld, oid) -> flag string

    # First pass: compute keep/exclude per object
    per_well_frames = {}
    for well in per_well_sheets:
        df = pd.read_excel(in_path, sheet_name=well)
        if df.empty:
            per_well_frames[well] = df
            continue

        cols = [c.strip() for c in df.columns]
        df.columns = cols
        cols = list(df.columns)

        time_col = detect_time_col(cols)
        field_col = detect_field_col(cols)
        obj_col = detect_object_col(cols)

        if time_col is None or field_col is None or obj_col is None:
            per_well_frames[well] = df
            continue

        df["_time"] = pd.to_numeric(df[time_col], errors="coerce")
        df["_field"] = pd.to_numeric(df[field_col], errors="coerce")
        df["_object"] = pd.to_numeric(df[obj_col], errors="coerce")

        f_corr_col = find_col(cols, r"^F_corr$", ignore_case=True)
        ff0_col = find_col(cols, r"^F_over_F0$", ignore_case=True)
        filt_col = find_col(cols, r"^filter_reason$", ignore_case=True)
        n_total_col = find_col(cols, r"^obj_n_total$", ignore_case=True)
        n_base_col = find_col(cols, r"^obj_n_baseline$", ignore_case=True)
        peak_col = find_col(cols, r"^obj_responder_peak_global$", ignore_case=True) or find_col(
            cols, r"^peak_global_ff0$", ignore_case=True
        )
        stim_col = find_col(cols, r"^stim_time_s$", ignore_case=True)
        general_resp_col = find_col(cols, r"^obj_responder_general$", ignore_case=True)
        phase_col = find_col(cols, r"^phase$", ignore_case=True)

        # Build well baseline distribution (F_over_F0 at baseline points)
        baseline_values = []
        for (fld, oid), sub in df.groupby(["_field", "_object"], dropna=False):
            t = sub["_time"]
            if t.isna().all():
                continue
            stim_val = None
            if stim_col is not None:
                stim_vals = pd.to_numeric(sub[stim_col], errors="coerce").dropna()
                if not stim_vals.empty:
                    stim_val = float(stim_vals.iloc[0])
            baseline_n = None
            if n_base_col is not None:
                n_vals = pd.to_numeric(sub[n_base_col], errors="coerce").dropna()
                if not n_vals.empty:
                    baseline_n = int(n_vals.iloc[0])
            baseline_n = baseline_n if baseline_n is not None else args.baseline_n_default
            baseline_times = pick_baseline_times(t, stim_val, baseline_n)
            if ff0_col is None:
                continue
            base_mask = t.isin(baseline_times)
            vals = pd.to_numeric(sub.loc[base_mask, ff0_col], errors="coerce")
            vals = vals[np.isfinite(vals)]
            if not vals.empty:
                baseline_values.append(vals.to_numpy())

        if baseline_values:
            well_base_vals = np.concatenate(baseline_values).astype(float, copy=False)
        else:
            well_base_vals = np.array([], dtype=float)

        well_base_mean = float(np.nanmean(well_base_vals)) if well_base_vals.size else np.nan
        well_base_std = float(np.nanstd(well_base_vals, ddof=1)) if well_base_vals.size > 1 else 0.0

        for (fld, oid), sub in df.groupby(["_field", "_object"], dropna=False):
            key = (well, int(fld) if np.isfinite(fld) else np.nan, int(oid) if np.isfinite(oid) else np.nan)
            reasons = []
            _tp_flag_parts = []  # soft timepoint warnings (kept but annotated)

            if args.responders_only and general_resp_col is not None:
                gen_vals = pd.to_numeric(sub[general_resp_col], errors="coerce").fillna(0).astype(int)
                if not (gen_vals > 0).any():
                    reasons.append("not_general_responder")

            # filter_reason must be kept for all rows
            if filt_col is not None:
                fr = sub[filt_col].astype(str).str.lower()
                if not (fr == "kept").all():
                    reasons.append("not_kept")
                    # Add specific baseline QC reasons for better flagging.
                    # These originate upstream in per_object_ff0.py and otherwise get collapsed into "not_kept".
                    if fr.str.startswith("baseline_std_>5").any():
                        reasons.append("baseline_std_>5")

            # F_over_F0 non-finite or negative (allow limited bad points)
            if ff0_col is not None:
                ff0 = pd.to_numeric(sub[ff0_col], errors="coerce")
                nonfinite_count = int((~np.isfinite(ff0)).sum())
                neg_count = int((ff0 < 0).sum())
                if nonfinite_count > max_ff0_nonfinite:
                    reasons.append("ff0_nonfinite")
                if neg_count > max_ff0_negative:
                    reasons.append("ff0_negative")
            else:
                reasons.append("ff0_missing")

            # F_corr extremely low (<0)
            if f_corr_col is not None:
                fc = pd.to_numeric(sub[f_corr_col], errors="coerce")
                fc_neg_count = int((fc < 0).sum())
                if fc_neg_count > max_fcorr_negative:
                    reasons.append("fcorr_negative")
            else:
                reasons.append("fcorr_missing")

            # Timepoint check: per-phase minimums are hard exclusions; total mismatch is soft flag only
            if phase_col is not None:
                phase_vals = sub[phase_col].astype(str).str.lower()
                n_base_phase = int((phase_vals == "baseline").sum())
                n_stim1_phase = int(phase_vals.str.contains("stim1", regex=False).sum())
                n_stim2_phase = int(phase_vals.str.contains("stim2", regex=False).sum())
                if n_base_phase < 2:
                    reasons.append(f"too_few_baseline_timepoints:{n_base_phase}")
                if n_stim1_phase < 4:
                    reasons.append(f"too_few_stim1_timepoints:{n_stim1_phase}")
                if n_stim2_phase < 2:
                    reasons.append(f"too_few_stim2_timepoints:{n_stim2_phase}")
                # Soft flag: total count mismatch (not a hard exclusion)
                n_seen = sub["_time"].dropna().nunique()
                if args.min_n is not None:
                    if n_seen < args.min_n:
                        _tp_flag_parts.append(f"missing_timepoints:{n_seen}")
                elif n_seen != args.expected_n:
                    _tp_flag_parts.append(f"missing_timepoints:{n_seen}(expected:{args.expected_n})")
            else:
                # No phase column: use obj_n_baseline for baseline minimum check
                if n_base_col is not None:
                    n_base_vals = pd.to_numeric(sub[n_base_col], errors="coerce").dropna()
                    if not n_base_vals.empty and int(n_base_vals.iloc[0]) < 2:
                        reasons.append(f"too_few_baseline_timepoints:{int(n_base_vals.iloc[0])}")
                # Total count as soft flag only
                if n_total_col is not None:
                    n_vals = pd.to_numeric(sub[n_total_col], errors="coerce").dropna()
                    if not n_vals.empty:
                        n_seen = int(n_vals.iloc[0])
                        if args.min_n is not None:
                            if n_seen < args.min_n:
                                _tp_flag_parts.append(f"missing_timepoints:{n_seen}")
                        elif n_seen != args.expected_n:
                            _tp_flag_parts.append(f"missing_timepoints:{n_seen}(expected:{args.expected_n})")
                else:
                    n_unique = sub["_time"].dropna().nunique()
                    if args.min_n is not None:
                        if n_unique < args.min_n:
                            _tp_flag_parts.append(f"missing_timepoints:{int(n_unique)}")
                    elif n_unique != args.expected_n:
                        _tp_flag_parts.append(f"missing_timepoints:{int(n_unique)}(expected:{args.expected_n})")

            # Peak global threshold
            if peak_col is not None:
                peak_vals = pd.to_numeric(sub[peak_col], errors="coerce").dropna()
                if peak_vals.empty or float(peak_vals.iloc[0]) <= peak_threshold:
                    reasons.append("peak_global_below_threshold")
            else:
                reasons.append("peak_global_missing")

            # Baseline outlier – slope-based criterion (only used for responder Boolean).
            # If the relative change between any two consecutive baseline F/F0 points
            # exceeds baseline_slope_threshold (default 2%), the baseline is unstable
            # and the object is filtered out.
            if ff0_col is not None:
                t = sub["_time"]
                stim_val = None
                if stim_col is not None:
                    stim_vals = pd.to_numeric(sub[stim_col], errors="coerce").dropna()
                    if not stim_vals.empty:
                        stim_val = float(stim_vals.iloc[0])
                baseline_n = None
                if n_base_col is not None:
                    n_vals = pd.to_numeric(sub[n_base_col], errors="coerce").dropna()
                    if not n_vals.empty:
                        baseline_n = int(n_vals.iloc[0])
                baseline_n = baseline_n if baseline_n is not None else args.baseline_n_default
                baseline_times = pick_baseline_times(t, stim_val, baseline_n)
                base_mask = t.isin(baseline_times)
                base_ff0 = pd.to_numeric(sub.loc[base_mask, ff0_col], errors="coerce")
                base_t   = sub.loc[base_mask, "_time"]
                base_df  = pd.DataFrame(
                    {"t": pd.to_numeric(base_t, errors="coerce").values,
                     "ff0": base_ff0.values}
                ).dropna().sort_values("t")
                if len(base_df) >= 2:
                    bv = base_df["ff0"].values
                    slope_outlier = False
                    for _i in range(len(bv) - 1):
                        ref = bv[_i]
                        if abs(ref) > 1e-9:
                            rel_change = abs(bv[_i + 1] - bv[_i]) / abs(ref)
                            if rel_change > baseline_slope_threshold:
                                slope_outlier = True
                                break
                    if slope_outlier:
                        reasons.append("baseline_slope_outlier")

            timepoint_exclusion_tags = (
                "too_few_baseline_timepoints",
                "too_few_stim1_timepoints",
                "too_few_stim2_timepoints",
            )
            timepoint_only_exclusion = bool(reasons) and all(
                any(r == tag or str(r).startswith(tag + ":") for tag in timepoint_exclusion_tags)
                for r in reasons
            )

            if reasons:
                object_reasons.append({
                    "well": well, "Field": fld, "ObjectNo": oid,
                    "reason": ";".join(reasons), "entry_type": "excluded",
                })
                if timepoint_only_exclusion:
                    stats_keep_objects.add(key)
            else:
                keep_objects.add(key)
                stats_keep_objects.add(key)
                if _tp_flag_parts:
                    flag_str = ";".join(_tp_flag_parts)
                    object_flags.append({
                        "well": well, "Field": fld, "ObjectNo": oid,
                        "reason": flag_str, "entry_type": "flagged",
                    })
                    flag_lookup[key] = flag_str

        per_well_frames[well] = df

    # Identify wells where every object was excluded (or the well had no processable data)
    excluded_wells_log = []
    for well in per_well_sheets:
        well_has_kept = any(k[0] == well for k in keep_objects)
        if not well_has_kept:
            excluded_wells_log.append({
                "well": well, "Field": None, "ObjectNo": None,
                "reason": "all_objects_excluded_or_no_data", "entry_type": "excluded_well",
            })

    # Filter per-well sheets
    filtered_well_frames = {}
    for well, df in per_well_frames.items():
        if df.empty:
            filtered_well_frames[well] = df
            continue
        field_col = detect_field_col(df.columns)
        obj_col = detect_object_col(df.columns)
        if field_col is None or obj_col is None:
            filtered_well_frames[well] = df
            continue
        df["_field"] = pd.to_numeric(df[field_col], errors="coerce")
        df["_object"] = pd.to_numeric(df[obj_col], errors="coerce")
        mask_keep = []
        for idx, row in df.iterrows():
            key = (well, int(row["_field"]) if np.isfinite(row["_field"]) else np.nan,
                   int(row["_object"]) if np.isfinite(row["_object"]) else np.nan)
            mask_keep.append(key in keep_objects)
        filtered = df.loc[mask_keep].drop(columns=["_field", "_object"], errors="ignore")
        filtered_well_frames[well] = filtered

    # Stamp timepoint_flag column onto filtered per-well frames for flagged-but-kept objects
    if flag_lookup:
        for well, df in filtered_well_frames.items():
            if df.empty:
                continue
            field_col_w = detect_field_col(df.columns)
            obj_col_w = detect_object_col(df.columns)
            if field_col_w is None or obj_col_w is None:
                continue
            _fld = pd.to_numeric(df[field_col_w], errors="coerce")
            _obj = pd.to_numeric(df[obj_col_w], errors="coerce")
            flags = []
            for f_val, o_val in zip(_fld, _obj):
                k = (well,
                     int(f_val) if pd.notna(f_val) and np.isfinite(f_val) else np.nan,
                     int(o_val) if pd.notna(o_val) and np.isfinite(o_val) else np.nan)
                flags.append(flag_lookup.get(k, ""))
            df = df.copy()
            df["timepoint_flag"] = flags
            filtered_well_frames[well] = df

    # Filter Object_Stats and Responder_Object_Level if present
    filtered_object_stats = None
    if "Object_Stats" in sheet_names:
        obj_stats = pd.read_excel(in_path, sheet_name="Object_Stats")
        if not obj_stats.empty:
            well_col = find_col(obj_stats.columns, r"^well$", ignore_case=True) or find_col(
                obj_stats.columns, r"^Well$", ignore_case=True
            )
            field_col = detect_field_col(obj_stats.columns)
            obj_col = detect_object_col(obj_stats.columns)
            if well_col and field_col and obj_col:
                obj_stats["_well"] = obj_stats[well_col].astype(str)
                obj_stats["_field"] = pd.to_numeric(obj_stats[field_col], errors="coerce")
                obj_stats["_object"] = pd.to_numeric(obj_stats[obj_col], errors="coerce")
                keep_mask = []
                for _, row in obj_stats.iterrows():
                    key = (row["_well"], int(row["_field"]) if np.isfinite(row["_field"]) else np.nan,
                           int(row["_object"]) if np.isfinite(row["_object"]) else np.nan)
                    keep_mask.append(key in stats_keep_objects)
                filtered_object_stats = obj_stats.loc[keep_mask].drop(
                    columns=["_well", "_field", "_object"], errors="ignore"
                )
            else:
                filtered_object_stats = obj_stats

    filtered_responder_obj = None
    if "Responder_Object_Level" in sheet_names:
        resp_obj = pd.read_excel(in_path, sheet_name="Responder_Object_Level")
        if not resp_obj.empty:
            well_col = find_col(resp_obj.columns, r"^well$", ignore_case=True) or find_col(
                resp_obj.columns, r"^Well$", ignore_case=True
            )
            field_col = detect_field_col(resp_obj.columns)
            obj_col = detect_object_col(resp_obj.columns)
            if well_col and field_col and obj_col:
                resp_obj["_well"] = resp_obj[well_col].astype(str)
                resp_obj["_field"] = pd.to_numeric(resp_obj[field_col], errors="coerce")
                resp_obj["_object"] = pd.to_numeric(resp_obj[obj_col], errors="coerce")
                keep_mask = []
                for _, row in resp_obj.iterrows():
                    key = (row["_well"], int(row["_field"]) if np.isfinite(row["_field"]) else np.nan,
                           int(row["_object"]) if np.isfinite(row["_object"]) else np.nan)
                    keep_mask.append(key in stats_keep_objects)
                filtered_responder_obj = resp_obj.loc[keep_mask].drop(
                    columns=["_well", "_field", "_object"], errors="ignore"
                )
            else:
                filtered_responder_obj = resp_obj

    # Rebuild responder overview from filtered object level
    filtered_responder_overview = None
    if filtered_responder_obj is not None and not filtered_responder_obj.empty:
        df = filtered_responder_obj.copy()
        if "Well" not in df.columns and "well" in df.columns:
            df["Well"] = df["well"]
        if "well" not in df.columns and "Well" in df.columns:
            df["well"] = df["Well"]

        overview_rows = []
        for well, sub in df.groupby("well"):
            general_total = sub["is_general_responder"].notna().sum()
            general_resp = int(pd.to_numeric(sub["is_general_responder"], errors="coerce").fillna(0).astype(int).sum())
            stim2_valid = sub["stim2_status"] == "ok"
            stim2_ambig = sub["stim2_status"] == "ambiguous_pre_high"
            stim2_available = stim2_valid | stim2_ambig
            stim2_resp = int(pd.to_numeric(sub.loc[stim2_valid, "is_stim2_responder"], errors="coerce").fillna(0).astype(int).sum())
            stim2_non = int(stim2_valid.sum() - stim2_resp)
            if "is_stim1_responder" in sub.columns:
                stim1_resp = int(pd.to_numeric(sub["is_stim1_responder"], errors="coerce").fillna(0).astype(int).sum())
            else:
                stim1_resp = 0
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
                "stim1_nonresponders": int(sub.shape[0] - stim1_resp),
            })
        filtered_responder_overview = pd.DataFrame(overview_rows)

    # Write output workbook
    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        # Summary sheets first (if present)
        if "Plate_Overview" in sheet_names:
            try:
                pd.read_excel(in_path, sheet_name="Plate_Overview").to_excel(
                    writer, index=False, sheet_name="Plate_Overview"
                )
            except Exception:
                pass

        if filtered_object_stats is not None:
            filtered_object_stats.to_excel(writer, index=False, sheet_name="Object_Stats")

        if filtered_responder_overview is not None:
            filtered_responder_overview.to_excel(writer, index=False, sheet_name="Responder_Overview")

        if filtered_responder_obj is not None:
            filtered_responder_obj.to_excel(writer, index=False, sheet_name="Responder_Object_Level")

        # Per-well sheets after summaries
        for well in per_well_sheets:
            filtered_well_frames[well].to_excel(writer, index=False, sheet_name=well)

        # Included_Object_Log: hard exclusions, well-level exclusions, and soft timepoint flags
        all_log_entries = list(object_reasons)
        all_log_entries.extend(excluded_wells_log)
        all_log_entries.extend(object_flags)
        if all_log_entries:
            log_df = pd.DataFrame(all_log_entries)
            # Add boolean columns for each distinct filter reason (prefix-matched for parameterised reasons)
            _bool_reasons = [
                "not_general_responder",
                "not_kept",
                "ff0_nonfinite",
                "ff0_negative",
                "ff0_missing",
                "fcorr_negative",
                "fcorr_missing",
                "too_few_baseline_timepoints",
                "too_few_stim1_timepoints",
                "too_few_stim2_timepoints",
                "peak_global_below_threshold",
                "peak_global_missing",
                "baseline_slope_outlier",
                "baseline_intensity_iqr_outlier",
                "baseline_std_>5",
                "missing_timepoints",
                "all_objects_excluded_or_no_data",
            ]
            def _has_reason(reason_str, tag):
                if pd.isna(reason_str):
                    return False
                parts = str(reason_str).split(";")
                return any(p == tag or p.startswith(tag + ":") for p in parts)
            for tag in _bool_reasons:
                log_df[tag] = log_df["reason"].apply(lambda r, t=tag: _has_reason(r, t))
            log_df.to_excel(writer, index=False, sheet_name="Included_Object_Log")

    print(f"Included workbook written to: {out_path}")

    # Generate spaghetti plots for excluded objects
    _plot_excluded_traces(object_reasons, per_well_frames, out_path)


def _plot_excluded_traces(object_reasons, per_well_frames, out_path):
    """Create per-well spaghetti plots and individual per-object plots of excluded objects."""
    if not object_reasons or not _MATPLOTLIB_OK:
        if not _MATPLOTLIB_OK and object_reasons:
            print("[WARN] matplotlib not available; skipping excluded-object plots")
        return

    excluded_dir = out_path.parent / "excluded"
    excluded_dir.mkdir(parents=True, exist_ok=True)

    # Build lookup: (well, field, obj) -> reason string
    reason_lookup: dict = {}
    for rec in object_reasons:
        w = rec.get("well")
        fld_val = rec.get("Field")
        obj_val = rec.get("ObjectNo")
        reason = rec.get("reason", "unknown")
        try:
            key = (w, int(float(fld_val)), int(float(obj_val)))
        except (ValueError, TypeError):
            continue
        reason_lookup[key] = reason

    # Group excluded objects by well
    by_well: dict = {}
    for rec in object_reasons:
        w = rec.get("well")
        if w is None:
            continue
        by_well.setdefault(w, []).append((rec.get("Field"), rec.get("ObjectNo"), rec.get("reason", "unknown")))

    plotted_any = False
    for well, exc_objs in by_well.items():
        if well not in per_well_frames:
            continue
        df = per_well_frames[well]
        if df.empty:
            continue

        ff0_col       = find_col(df.columns, r"^F_over_F0$", ignore_case=True)
        time_col      = detect_time_col(df.columns)
        fld_col       = detect_field_col(df.columns)
        obj_col       = detect_object_col(df.columns)
        stim_col_local = find_col(df.columns, r"^stim_time_s$", ignore_case=True)
        thr_col_local  = find_col(df.columns, r"^obj_responder_threshold$", ignore_case=True)

        if not all([ff0_col, time_col, fld_col, obj_col]):
            continue

        df = df.copy()
        df["_t"]   = pd.to_numeric(df[time_col], errors="coerce")
        df["_fld"] = pd.to_numeric(df[fld_col],  errors="coerce")
        df["_obj"] = pd.to_numeric(df[obj_col],  errors="coerce")

        # Detect stim time
        stim_t = None
        if stim_col_local is not None:
            sv = pd.to_numeric(df[stim_col_local], errors="coerce").dropna()
            if not sv.empty:
                stim_t = float(sv.iloc[0])

        # --- Per-well spaghetti overview ---
        fig, ax = plt.subplots(figsize=(7, 4.5), dpi=120)
        ax.axhline(1.0, linestyle=":", linewidth=0.8, alpha=0.6, color="#888888")
        if stim_t is not None and np.isfinite(stim_t):
            ax.axvline(stim_t, linestyle=":", linewidth=1.0, alpha=0.8, color="#0072B2")

        plotted = 0
        for fld_val, obj_val, _reason in exc_objs:
            try:
                fld_num = int(float(fld_val))
                obj_num = int(float(obj_val))
            except (ValueError, TypeError):
                continue
            mask = (df["_fld"] == fld_num) & (df["_obj"] == obj_num)
            sub = df[mask].sort_values("_t")
            if sub.empty:
                continue
            ff0_vals = pd.to_numeric(sub[ff0_col], errors="coerce")
            if ff0_vals.dropna().empty:
                continue
            ax.plot(sub["_t"].values, ff0_vals.values,
                    linewidth=0.8, alpha=0.35, color="#E69F00")
            plotted += 1

        if plotted > 0:
            ax.set_title(f"Well {well} – Excluded Objects (n={plotted})", fontsize=11)
            ax.set_xlabel("Time [s]", fontsize=11)
            ax.set_ylabel("F / F0", fontsize=11)
            ax.margins(x=0.05)
            fig.tight_layout()
            fig.savefig(excluded_dir / f"{well}_excluded.png")
            plotted_any = True
        plt.close(fig)

        # --- Individual per-object plots for excluded objects (2-panel layout) ---
        for fld_val, obj_val, reason in exc_objs:
            try:
                fld_num = int(float(fld_val))
                obj_num = int(float(obj_val))
            except (ValueError, TypeError):
                continue
            mask = (df["_fld"] == fld_num) & (df["_obj"] == obj_num)
            sub = df[mask].sort_values("_t")
            if sub.empty:
                continue
            ff0_vals = pd.to_numeric(sub[ff0_col], errors="coerce")
            if ff0_vals.dropna().empty:
                continue

            # Per-object threshold
            obj_thr = None
            if thr_col_local is not None:
                tv = pd.to_numeric(sub[thr_col_local], errors="coerce").dropna()
                if not tv.empty:
                    obj_thr = float(tv.iloc[0])

            # Split into baseline / stim segments
            if stim_t is not None and np.isfinite(stim_t):
                base_t   = sub["_t"][sub["_t"] < stim_t].values
                base_ff0 = ff0_vals[sub["_t"] < stim_t].values
                stim_t_  = sub["_t"][sub["_t"] >= stim_t].values
                stim_ff0 = ff0_vals[sub["_t"] >= stim_t].values
            else:
                base_t = sub["_t"].values;  base_ff0 = ff0_vals.values
                stim_t_ = np.array([]); stim_ff0 = np.array([])

            reason_text = "; ".join(reason.split(";")) if reason else "unknown"

            fig, (ax_base, ax_stim) = plt.subplots(
                1, 2, figsize=(11, 3.8), dpi=150,
                gridspec_kw={"width_ratios": [1, 2]},
            )
            fig.suptitle(
                f"*** {well} \u203a Field {fld_num} \u203a Obj {obj_num} ***\nExcluded: {reason_text}",
                fontsize=11, color="#CC2200",
            )

            # Baseline panel
            ax_base.axhline(1.0, linestyle=":", linewidth=0.8, alpha=0.6, color="#888888")
            ax_base.plot(base_t.astype(float), base_ff0.astype(float),
                         marker="o", linewidth=1.5, color="#E69F00")
            ax_base.set_title("Baseline", fontsize=11)
            ax_base.set_xlabel("Time [s]", fontsize=11)
            ax_base.set_ylabel("F / F0", fontsize=11)
            ax_base.tick_params(labelsize=10)
            finite_base = base_ff0[np.isfinite(base_ff0.astype(float))]
            if finite_base.size:
                ylo = min(float(finite_base.min()) - 0.01, 0.95)
                yhi = max(float(finite_base.max()) + 0.01, 1.05)
                ax_base.set_ylim(ylo, yhi)
            else:
                ax_base.set_ylim(0.95, 1.05)
            ax_base.margins(x=0.05)

            # Stim panel
            ax_stim.axhline(1.0, linestyle=":", linewidth=0.8, alpha=0.6, color="#888888")
            if obj_thr is not None and np.isfinite(obj_thr):
                ax_stim.axhline(obj_thr, linestyle=":", linewidth=1.1,
                                color="red", alpha=0.85,
                                label=f"threshold = {obj_thr:.2f}")
                ax_stim.legend(fontsize=9, loc="upper right")
            if stim_t is not None and np.isfinite(stim_t):
                ax_stim.axvline(float(stim_t), linestyle=":", linewidth=1.1,
                                alpha=0.9, color="blue")
            ax_stim.plot(stim_t_.astype(float), stim_ff0.astype(float),
                         marker="o", linewidth=1.5, color="#E69F00")
            ax_stim.set_title("Stim 1 + Stim 2", fontsize=11)
            ax_stim.set_xlabel("Time [s]", fontsize=11)
            ax_stim.set_ylabel("F / F0", fontsize=11)
            ax_stim.tick_params(labelsize=10)
            ax_stim.set_ylim(0.5, 7.0)
            ax_stim.margins(x=0.03)

            fig.tight_layout()
            png = excluded_dir / f"{well}_F{fld_num}_obj_{obj_num}_excluded.png"
            fig.savefig(png)
            plt.close(fig)
            plotted_any = True

    if plotted_any:
        print(f"Excluded traces written to: {excluded_dir}")


if __name__ == "__main__":
    main()
