# -*- coding: utf-8 -*-
import argparse, re, math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pt.pt_utils import find_col, normalize_cols

# ---------- color-blind friendly palette (Okabe–Ito) ----------
OKABE_ITO = ['#000000','#E69F00','#56B4E9','#009E73','#F0E442','#0072B2','#D55E00','#CC79A7']

# ---------- tiny utils ----------

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

def iqr_bounds(series, k=1.5, two_sided=True):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty: return (-np.inf, np.inf)
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    lo = q1 - (k * iqr) if two_sided else -np.inf
    hi = q3 + (k * iqr)
    return lo, hi

def iqr_mask(df, col, by=None, k=1.5, upper_only=False):
    """
    Boolean mask of inliers using Tukey IQR.
    If 'by' is provided (e.g., 't'), compute bounds per group so the join is 1-D.
    If upper_only=True, only apply the upper bound (for 'too intense').
    """
    s = pd.to_numeric(df[col], errors="coerce")

    if by is None:
        q1 = s.quantile(0.25)
        q3 = s.quantile(0.75)
        iqr = q3 - q1
        lo = -np.inf if upper_only else (q1 - k * iqr)
        hi = q3 + k * iqr
        return (s <= hi) if upper_only else s.between(lo, hi)

    q = df.groupby(by)[col].quantile([0.25, 0.75]).unstack(level=-1)
    q.columns = ["q1", "q3"]
    q["iqr"] = q["q3"] - q["q1"]
    q["lo"] = q["q1"] - (0 if upper_only else k * q["iqr"])
    q["hi"] = q["q3"] + k * q["iqr"]

    bounds = q[["lo", "hi"]]
    df2 = df.join(bounds, on=by)
    return (s <= df2["hi"]) if upper_only else s.between(df2["lo"], df2["hi"])

# ---------- F/F0 helpers ----------
def compute_baseline_mask(t, stim_time, baseline_n, baseline_window):
    if stim_time is not None and np.isfinite(stim_time):
        if baseline_window and baseline_window > 0:
            return (t >= stim_time - baseline_window) & (t < stim_time)
        else:
            return t < stim_time
    uniq = np.sort(pd.unique(t.dropna()))
    if len(uniq) == 0: return pd.Series(False, index=t.index)
    cutoff = uniq[min(len(uniq)-1, baseline_n-1)]
    return t <= cutoff

def try_roi_level_ff0(df, time_col, inten_col, id_cols, base_mask):
    if not all(c in df.columns for c in id_cols):
        return None
    sub = df[[time_col, inten_col] + id_cols].copy()
    sub["is_base"] = base_mask
    f0 = (sub[sub["is_base"]]
          .groupby(id_cols)[inten_col].mean().rename("F0"))
    if f0.empty:
        return None
    merged = sub.join(f0, on=id_cols)
    merged["F_over_F0"] = merged[inten_col] / merged["F0"]
    return merged[[time_col, "F_over_F0"] + id_cols].dropna()

# ---------- plotting ----------
def plot_well(
    df,
    well_name,
    outdir,
    size_k=1.5,
    inten_k=1.5,
    scope="timepoint",
    stim_time=None,
    auc_window=30.0,
    baseline_n=3,
    baseline_window=None,
    prefer_roi_baseline=True,
    error_mode="sem",         # NEW: "sem" or "sd"
    yscale="linear",          # NEW: "linear", "log", "symlog"
    ylim=None                 # NEW: (ymin, ymax) or None
):
    df = df.copy()
    df.columns = normalize_cols(df.columns)

    # columns
    time_col = find_col(df.columns, r"^Time\b|Time\s*\[\s*s\s*\]") or "Timepoint"
    bbox_col = find_col(df.columns, r"^Bounding Box$")
    inten_col = None
    for c in df.columns:
        if re.search(r"Intensity.*Region.*Mean", c, flags=re.I) and "Mean per Well" not in c:
            inten_col = c; break
    if inten_col is None:
        inten_col = find_col(df.columns, r"Intensity.*Mean per Well")

    if time_col is None or inten_col is None:
        print(f"  [{well_name}] skipped (missing time or intensity column)")
        return False

    # numeric core
    t  = pd.to_numeric(df.get(time_col), errors="coerce")
    y  = pd.to_numeric(df.get(inten_col), errors="coerce")

    field_series = pd.to_numeric(df["Field"], errors="coerce") if "Field" in df.columns else pd.Series(np.nan, index=df.index)
    obj_series   = (pd.to_numeric(df["Object No"], errors="coerce") if "Object No" in df.columns
                    else pd.to_numeric(df["ObjectNo"], errors="coerce") if "ObjectNo" in df.columns
                    else pd.Series(np.nan, index=df.index))

    dd = pd.DataFrame({"t": t, "y": y, "Field": field_series, "ObjectNo": obj_series})

    # Bounding Box → area (if present)
    if bbox_col is not None:
        dd["area"] = df[bbox_col].apply(parse_bbox_area)
    else:
        dd["area"] = np.nan

    dd = dd.dropna(subset=["t","y"])

    # outlier filtering
    group_key = "t" if scope == "timepoint" else None
    size_mask  = iqr_mask(dd, "area", by=group_key, k=size_k, upper_only=False) if dd["area"].notna().any() else pd.Series(True, index=dd.index)
    inten_mask = iqr_mask(dd, "y",    by=group_key, k=inten_k, upper_only=True)
    keep = (size_mask & inten_mask).fillna(False)
    kept = dd[keep]
    if kept.empty:
        print(f"  [{well_name}] skipped (everything excluded)")
        return False

    # baseline mask
    base_mask = compute_baseline_mask(kept["t"], stim_time, baseline_n, baseline_window)

    # F/F0 (ROI-level if feasible)
    ff0_df = try_roi_level_ff0(kept, "t", "y", ["Field","ObjectNo"], base_mask) if prefer_roi_baseline else None
    if ff0_df is None:
        base_vals = kept.loc[base_mask, "y"]
        if base_vals.empty or not np.isfinite(base_vals).any():
            base_vals = kept["y"].head(baseline_n)
        F0 = base_vals.mean()
        kept = kept.assign(F_over_F0 = kept["y"] / F0)
        ff0 = kept[["t","F_over_F0"]].dropna()
    else:
        ff0 = ff0_df.rename(columns={"F_over_F0":"F_over_F0"})

    # per-timepoint stats (mean, sd, sem)
    grp = ff0.groupby("t")["F_over_F0"].agg(["mean","std","count"]).sort_index()
    grp["sem"] = grp["std"] / np.sqrt(grp["count"]).replace(0, np.nan)

    x = grp.index.values.astype(float)
    ybar = grp["mean"].values

    if error_mode.lower() == "sd":
        yerr = grp["std"].values
        err_label = "Mean ± SD"
    else:
        yerr = grp["sem"].values
        err_label = "Mean ± SEM"

    # for log scale, avoid <= 0
    if yscale == "log":
        eps = 1e-12
        lower = np.clip(ybar - yerr, eps, None)
        upper = np.clip(ybar + yerr, eps, None)
    else:
        lower = ybar - yerr
        upper = ybar + yerr

    # figure
    fig = plt.figure(figsize=(6.5, 4.3), dpi=150)
    ax = plt.gca()

    # set y scale & limits (NEW)
    try:
        ax.set_yscale(yscale)
    except Exception:
        ax.set_yscale("linear")
    if ylim and len(ylim) == 2 and all(v is not None for v in ylim):
        ax.set_ylim(ylim[0], ylim[1])

    # palette & styles
    line_color = OKABE_ITO[5]     # blue
    shade_color = line_color

    # baseline = 1 line
    ax.axhline(1.0, linestyle="--", linewidth=0.9, alpha=0.7, color="#666666", zorder=1)

    # shaded error (NEW) + mean line
    ax.fill_between(x, lower, upper, alpha=0.25, color=shade_color, label=err_label, zorder=2)
    ax.plot(x, ybar, linewidth=1.8, label="Mean", color=line_color, zorder=3)

    # stimulus annotation & AUC shading
    auc_val = np.nan
    if stim_time is not None and auc_window and auc_window > 0:
        t0, t1 = stim_time, stim_time + auc_window
        ax.axvline(t0, linestyle=":", linewidth=1.2, alpha=0.9, color="#444444", zorder=4)
        mask = (x >= t0) & (x <= t1)
        if mask.any():
            ax.fill_between(x[mask], 1.0, ybar[mask], alpha=0.15, color="#999999", zorder=1, label=None)
            auc_val = float(np.trapz(ybar[mask] - 1.0, x[mask]))
            # 30 s bracket
            y0 = ax.get_ylim()[0]
            ax.annotate("", xy=(t0, y0), xytext=(t1, y0),
                        arrowprops=dict(arrowstyle="<->", lw=1, color="#444444"))
            ax.text((t0+t1)/2, y0, f"{int(auc_window)} s", ha="center", va="top", fontsize=8, color="#444444")

    # labels/titles
    ax.set_title(f"Well {well_name}")
    ax.set_xlabel("Time [s]" if "Time" in time_col else "Timepoint")
    ax.set_ylabel("F / F0")
    ax.legend(loc="best", frameon=False)
    ax.margins(x=0.05)
    fig.tight_layout()

    outdir.mkdir(parents=True, exist_ok=True)
    png = outdir / f"{well_name}.png"
    fig.savefig(png)
    plt.close(fig)

    # write summary CSV (includes sd, sem, chosen error)
    summary = grp.copy()
    summary.index.name = "time"
    summary["well"] = well_name
    summary["error_mode"] = error_mode.lower()
    summary["err"] = yerr
    summary["lower"] = lower
    summary["upper"] = upper
    summary["auc_above_baseline"] = auc_val
    (outdir / f"{well_name}__summary.csv").write_text(
        summary.to_csv()
    )

    print(f"  [{well_name}] -> {png.name}  (points={int(grp['count'].sum())}, AUC={auc_val:.3g})")
    return True

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(description="Per-well calcium-like plots (F/F0) with outlier removal")
    ap.add_argument("xlsx", help="Path to Excel from phenix_to_xlsx.py")
    ap.add_argument("--outdir", help="Where to put PNGs (default: <xlsx_dir>/plots/<xlsx_stem>)")
    # REPLACED: --ci  ➜  --error
    ap.add_argument("--error", choices=["sem","sd"], default="sem",
                    help="Error metric for shading around the mean (default: sem)")
    ap.add_argument("--size-iqr-k", type=float, default=1.5, help="Tukey k for size outliers (two-sided)")
    ap.add_argument("--intensity-iqr-k", type=float, default=1.5, help="Tukey k for intensity outliers (upper-only)")
    ap.add_argument("--scope", choices=["timepoint","well"], default="timepoint",
                    help="Compute outlier thresholds per timepoint (default) or across whole well")
    ap.add_argument("--stim", type=float, default=None, help="Stimulus time in seconds (draw vertical line)")
    ap.add_argument("--auc", type=float, default=30.0, help="Shade AUC window length in seconds from --stim (default 30)")
    ap.add_argument("--baseline-n", type=int, default=3, help="If no --stim, use first N unique times as baseline (default 3)")
    ap.add_argument("--baseline-window", type=float, default=None, help="If --stim is set, use seconds before stim for baseline (e.g., 5)")
    ap.add_argument("--no-roi-baseline", action="store_true", help="Force global (not ROI-level) baseline")

    # NEW: y-axis scale and limits
    ap.add_argument("--yscale", choices=["linear","log","symlog"], default="linear",
                    help="Y-axis scale (default: linear)")
    ap.add_argument("--ylim", type=str, default=None,
                    help="Y-limits as 'min,max' (e.g., --ylim 0.5,2.0). Omit for auto.")

    args = ap.parse_args()

    xlsx = Path(args.xlsx).resolve()
    if not xlsx.is_file():
        raise SystemExit(f"Not found: {xlsx}")

    base = xlsx.stem
    outdir = Path(args.outdir).resolve() if args.outdir else (xlsx.parent / "plots" / base)

    # parse ylim if provided
    ylim = None
    if args.ylim:
        try:
            y0, y1 = [float(v.strip()) for v in args.ylim.split(",")]
            ylim = (y0, y1)
        except Exception:
            raise SystemExit("Invalid --ylim format. Use like: --ylim 0.8,1.6")

    xls = pd.ExcelFile(xlsx)
    made = 0
    for sheet in xls.sheet_names:
        if sheet == "Plate_Overview":
            continue
        df = pd.read_excel(xlsx, sheet_name=sheet, dtype=str)
        ok = plot_well(
            df, well_name=sheet, outdir=outdir,
            size_k=args.size_iqr_k, inten_k=args.intensity_iqr_k, scope=args.scope,
            stim_time=args.stim, auc_window=args.auc,
            baseline_n=args.baseline_n, baseline_window=args.baseline_window,
            prefer_roi_baseline=not args.no_roi_baseline,
            error_mode=args.error,
            yscale=args.yscale,
            ylim=ylim
        )
        made += int(ok)

    print(f"Done. Wrote {made} plot(s) to: {outdir}")

if __name__ == "__main__":
    main()
