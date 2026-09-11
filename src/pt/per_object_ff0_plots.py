# -*- coding: utf-8 -*-
"""
Plot per-object F/F0 traces from an included workbook.

This expects an augmented/included Excel file with per-well sheets that
include Time, Field, ObjectNo, and F_over_F0 columns.
"""

from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

try:
    from pptx import Presentation
    from pptx.util import Inches
    PPTX_OK = True
except Exception:
    PPTX_OK = False


def find_col(columns, pattern, ignore_case=False):
    flags = re.I if ignore_case else 0
    for c in columns:
        if re.search(pattern, str(c), flags=flags):
            return c
    return None


def extract_context_from_path(path: Path):
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


def prefix_from_ctx(date6, ex_num, ev_num, well):
    d = date6 if date6 != "NA" else "NA"
    ex = f"Ex{ex_num}" if ex_num != "NA" else "ExNA"
    ev = f"Ev{ev_num}" if ev_num != "NA" else "EvNA"
    return f"{d}_{ex}_{ev}_{well}"


def prefer_time_col(columns):
    return (
        find_col(columns, r"^Time\s*(\[\s*s\s*\]|\(\s*s\s*\))?$")
        or find_col(columns, r"^Timepoint$")
        or find_col(columns, r"^Frame$")
        or find_col(columns, r"^Time$")
        or find_col(columns, r"time", ignore_case=True)
    )


def load_stim_summary(stim_path: str | None):
    if not stim_path:
        return None
    try:
        return pd.read_excel(Path(stim_path).expanduser().resolve())
    except Exception as e:
        print(f"[WARN] Failed to read --stim-times '{stim_path}': {e}")
        return None


def main():
    ap = argparse.ArgumentParser(
        description="Plot per-object F/F0 traces from included workbook"
    )
    ap.add_argument("xlsx", help="Path to included workbook")
    ap.add_argument("--outdir", help="Outputs root (default: <xlsx_dir>/plots/included plots)")
    ap.add_argument("--stim", type=float, default=None)
    ap.add_argument("--stats-window", type=float, default=30.0)
    ap.add_argument("--ylim", nargs=2, type=float, default=[-0.5, 5.0])
    ap.add_argument("--per-page", type=int, default=20)
    ap.add_argument("--ncols", type=int, default=5)
    ap.add_argument("--ppt-width", type=float, default=13.33)
    ap.add_argument("--ppt-height", type=float, default=7.5)
    ap.add_argument("--no-single-pngs", action="store_true")
    ap.add_argument("--pptx", action="store_true")
    ap.add_argument("--stim-times",
                    help="Annotated stim-times Excel (w/ 'Well', 'baseline_end_s', 'stim1_start_s', etc.)")
    ap.add_argument("--stim-ref-col",
                    help="Column name in --stim-times to use as reference time")

    args = ap.parse_args()

    xlsx = Path(args.xlsx).expanduser().resolve()
    if not xlsx.is_file():
        raise SystemExit(f"Not found: {xlsx}")

    outdir = Path(args.outdir).expanduser().resolve() if args.outdir else (xlsx.parent / "plots" / "individual" / "included")
    outdir.mkdir(parents=True, exist_ok=True)

    date6, ex_num, ev_num = extract_context_from_path(xlsx)

    stim_df = load_stim_summary(args.stim_times)

    xls = pd.ExcelFile(xlsx)
    for sheet in xls.sheet_names:
        if sheet == "Plate_Overview":
            continue

        well = sheet.strip()
        file_prefix = prefix_from_ctx(date6, ex_num, ev_num, well)
        df = pd.read_excel(xlsx, sheet_name=sheet)
        if df.empty:
            continue

        df.columns = [c.strip() for c in df.columns]
        time_col = prefer_time_col(df.columns)
        field_col = find_col(df.columns, r"^Field$", ignore_case=True)
        obj_col = find_col(df.columns, r"^Object\s*No$", ignore_case=True) or find_col(
            df.columns, r"^ObjectNo$", ignore_case=True
        )
        ff0_col = find_col(df.columns, r"^F_over_F0$", ignore_case=True)
        if time_col is None or field_col is None or obj_col is None or ff0_col is None:
            print(f"  [{well}] skipped (missing time/field/object/F_over_F0)")
            continue

        stim_time_s_col = find_col(df.columns, r"^stim_time_s$", ignore_case=True)
        threshold_col = find_col(df.columns, r"^obj_responder_threshold$", ignore_case=True)
        flag_col = find_col(df.columns, r"^timepoint_flag$", ignore_case=True)

        t = pd.to_numeric(df[time_col], errors="coerce")
        ff0 = pd.to_numeric(df[ff0_col], errors="coerce")
        fld = pd.to_numeric(df[field_col], errors="coerce")
        oid = pd.to_numeric(df[obj_col], errors="coerce")
        extra = {}
        if stim_time_s_col is not None:
            extra["stim_time_s"] = pd.to_numeric(df[stim_time_s_col], errors="coerce")
        if threshold_col is not None:
            extra["threshold"] = pd.to_numeric(df[threshold_col], errors="coerce")
        if flag_col is not None:
            extra["timepoint_flag"] = df[flag_col].astype(str).replace("nan", "")
        subdf = pd.DataFrame({"t": t, "F_over_F0": ff0, "Field": fld, "ObjectNo": oid, **extra}).dropna(
            subset=["t", "F_over_F0", "Field", "ObjectNo"]
        )
        if subdf.empty:
            continue

        # stim reference time — priority: stim_time_s column > stim_df > args.stim
        stim_time = None
        if stim_time_s_col is not None:
            sv = subdf["stim_time_s"].dropna() if "stim_time_s" in subdf.columns else pd.Series([], dtype=float)
            if not sv.empty:
                stim_time = float(sv.iloc[0])
        if stim_time is None and stim_df is not None and "Well" in stim_df.columns and well in stim_df["Well"].values:
            row = stim_df[stim_df["Well"] == well].iloc[0]
            candidate_cols = []
            if args.stim_ref_col and args.stim_ref_col in row.index:
                candidate_cols = [args.stim_ref_col]
            else:
                if "stim1_start_s" in row.index:
                    candidate_cols = ["stim1_start_s"]
                else:
                    start_cols = [c for c in row.index if str(c).endswith("_start_s")]
                    start_cols = sorted(start_cols)
                    candidate_cols = start_cols or (["baseline_end_s"] if "baseline_end_s" in row.index else [])
            for c in candidate_cols:
                val = pd.to_numeric(pd.Series([row[c]]), errors="coerce").dropna()
                if not val.empty:
                    stim_time = float(val.iloc[0])
                    break
        if stim_time is None and args.stim is not None:
            stim_time = float(args.stim)

        # outdir is flat — no per-well subfolders; file_prefix already encodes well identity

        # per-object plots
        facet_items = []
        for (f, o), g in subdf.groupby(["Field", "ObjectNo"], sort=True):
            g = g.sort_values("t")

            # per-object threshold and timepoint flag (if available)
            obj_threshold = None
            if "threshold" in g.columns:
                thr_vals = g["threshold"].dropna()
                if not thr_vals.empty:
                    obj_threshold = float(thr_vals.iloc[0])

            obj_tp_flag = ""
            if "timepoint_flag" in g.columns:
                fv = g["timepoint_flag"].replace("", np.nan).dropna()
                if not fv.empty:
                    obj_tp_flag = str(fv.iloc[0]).strip()

            if not args.no_single_pngs:
                # Split data into baseline (< stim_time) and full trace (all timepoints).
                # If no stim_time is available, baseline panel shows all points.
                if stim_time is not None and np.isfinite(stim_time):
                    base_g = g[g["t"] < stim_time]
                else:
                    base_g = g

                fig, (ax_base, ax_full) = plt.subplots(1, 2, figsize=(11, 3.8), dpi=150)
                _flag_note = f"\n(!) {obj_tp_flag}" if obj_tp_flag else ""
                fig.suptitle(
                    f"{well} \u203a Field {int(f)} \u203a Obj {int(o)}{_flag_note}",
                    fontsize=12, color="black" if not obj_tp_flag else "#884400",
                )

                # --- Baseline panel ---
                ax_base.axhline(1.5, linestyle="--", linewidth=1.0, alpha=0.7, color="gray")
                if not base_g.empty:
                    ax_base.plot(base_g["t"].values.astype(float),
                                 base_g["F_over_F0"].values.astype(float),
                                 marker="o", linewidth=1.5)
                ax_base.set_title("Baseline", fontsize=11)
                ax_base.set_xlabel("Time [s]", fontsize=11)
                ax_base.set_ylabel("F / F0", fontsize=11)
                ax_base.tick_params(labelsize=10)
                # autoscale bounded: always show at least 0.95–1.05
                if not base_g.empty:
                    bv = base_g["F_over_F0"].dropna()
                    if not bv.empty:
                        ylo = min(float(bv.min()) - 0.01, 0.95)
                        yhi = max(float(bv.max()) + 0.01, 1.05)
                        ax_base.set_ylim(ylo, yhi)
                    else:
                        ax_base.set_ylim(0.95, 1.05)
                else:
                    ax_base.set_ylim(0.95, 1.05)
                ax_base.margins(x=0.05)

                # --- Full trace panel (all timepoints) ---
                ax_full.axhline(1.5, linestyle="--", linewidth=1.0, color="red", alpha=0.7)
                if obj_threshold is not None and np.isfinite(obj_threshold):
                    ax_full.axhline(
                        obj_threshold,
                        linestyle=":",
                        linewidth=1.1,
                        color="red",
                        alpha=0.85,
                        label=f"threshold = {obj_threshold:.2f}",
                    )
                    ax_full.legend(fontsize=9, loc="upper right")
                # vertical event lines from stim_df
                if stim_df is not None and "Well" in stim_df.columns and well in stim_df["Well"].values:
                    row = stim_df[stim_df["Well"] == well].iloc[0]
                    for col in sorted(row.index):
                        if col.endswith("_start_s") or col.endswith("_end_s"):
                            t0 = row[col]
                            if pd.notna(t0):
                                ax_full.axvline(
                                    float(t0),
                                    linestyle=":",
                                    linewidth=1.1,
                                    alpha=0.8,
                                    color="red" if "end" in col else "blue",
                                )
                elif stim_time is not None and np.isfinite(stim_time):
                    ax_full.axvline(float(stim_time), linestyle=":", linewidth=1.1, alpha=0.9, color="blue")

                ax_full.plot(
                    g["t"].values.astype(float),
                    g["F_over_F0"].values.astype(float),
                    marker="o",
                    linewidth=1.5,
                )
                ax_full.set_title("Full trace", fontsize=11)
                ax_full.set_xlabel("Time [s]", fontsize=11)
                ax_full.set_ylabel("F / F0", fontsize=11)
                ax_full.tick_params(labelsize=10)
                if args.ylim and len(args.ylim) == 2:
                    ax_full.set_ylim(float(args.ylim[0]), float(args.ylim[1]))
                ax_full.margins(x=0.03)

                fig.tight_layout()
                png = outdir / f"{file_prefix}_F{int(f)}_obj_{int(o)}.png"
                fig.savefig(png)
                plt.close(fig)

            facet_items.append((int(f), int(o), g[["t", "F_over_F0"]].copy()))

        # Faceted pages
        if facet_items:
            per_page = max(1, int(args.per_page))
            ncols = max(1, int(args.ncols))
            page_pngs = []
            for page_idx, start in enumerate(range(0, len(facet_items), per_page), start=1):
                chunk = facet_items[start:start + per_page]
                nplots = len(chunk)
                nrows = int(math.ceil(nplots / ncols))

                fig = plt.figure(figsize=(float(args.ppt_width), float(args.ppt_height)), dpi=150)
                for i, (f, o, g) in enumerate(chunk, start=1):
                    ax = fig.add_subplot(nrows, ncols, i)
                    if stim_time is not None and np.isfinite(stim_time):
                        ax.axvline(float(stim_time), linestyle=":", linewidth=0.8, alpha=0.9)
                    if stim_df is not None and "Well" in stim_df.columns and well in stim_df["Well"].values:
                        row = stim_df[stim_df["Well"] == well].iloc[0]
                        for col in sorted(row.index):
                            if col.endswith("_start_s") or col.endswith("_end_s"):
                                t0 = row[col]
                                if pd.notna(t0):
                                    ax.axvline(float(t0), linestyle=":", linewidth=0.8, alpha=0.8,
                                               color="red" if "end" in col else "blue")
                    ax.plot(g["t"].values.astype(float), g["F_over_F0"].values.astype(float),
                            marker="o", linewidth=1.0, markersize=2.8)
                    ax.set_title(f"F{f} Obj{o}", fontsize=12)
                    if args.ylim and len(args.ylim) == 2:
                        ax.set_ylim(float(args.ylim[0]), float(args.ylim[1]))
                    ax.tick_params(labelsize=12, length=2)
                    if i <= (nrows - 1) * ncols:
                        ax.set_xlabel("")
                    else:
                        ax.set_xlabel("Time [s]", fontsize=12)
                    if (i - 1) % ncols != 0:
                        ax.set_ylabel("")
                    else:
                        ax.set_ylabel("F/F0", fontsize=12)
                    ax.margins(x=0.05)
                fig.tight_layout()

                page_png = outdir / f"{file_prefix}_page_{page_idx:03d}.png"
                fig.savefig(page_png, dpi=150)
                plt.close(fig)
                page_pngs.append(page_png)

            if args.pptx:
                if not PPTX_OK:
                    print("  [warn] python-pptx not installed; skipping PPTX.")
                else:
                    prs = Presentation()
                    prs.slide_width = int(13.33 * 914400)
                    prs.slide_height = int(7.50 * 914400)
                    blank = prs.slide_layouts[6]
                    for img in page_pngs:
                        slide = prs.slides.add_slide(blank)
                        slide.shapes.add_picture(str(img), Inches(0), Inches(0), Inches(13.33), Inches(7.5))
                    pptx_path = outdir / f"{file_prefix}.pptx"
                    prs.save(str(pptx_path))

        print(f"  [{well}] plotted {len(facet_items)} object(s)")

    print(f"Done. Wrote plots to: {outdir}")


if __name__ == "__main__":
    main()
