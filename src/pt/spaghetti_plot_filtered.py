# -*- coding: utf-8 -*-
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import argparse
import re

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from pt.spaghetti_plot_per_well import (
    extract_context_from_path,
    build_filename_prefix,
    well_file_prefix,
    read_well_sheet_minimal,
    preprocess_df_for_well,
    ff0_per_roi,
    _draw_stim_vlines,
)
from pt.pt_utils import OKABE_ITO, stims_from_df


@dataclass
class ContextLabel:
    date6: str
    ex_num: str
    ev_num: str

    def title_prefix(self) -> str:
        parts = []
        if self.date6 != "NA":
            parts.append(self.date6)
        if self.ex_num != "NA":
            parts.append(f"Ex{self.ex_num}")
        if not parts:
            return "Unknown"
        return " ".join(parts)


def _stim_window_mask(t: pd.Series, stim2: float | None, stim3: float | None) -> pd.Series:
    if stim2 is None or not np.isfinite(stim2):
        return pd.Series(False, index=t.index)
    if stim3 is not None and np.isfinite(stim3):
        return (t >= stim2) & (t < stim3)
    return t >= stim2


def _compute_baseline_bounds(ff0: pd.DataFrame, stim1: float | None, sd_k: float) -> tuple[float | None, float | None]:
    if stim1 is None or not np.isfinite(stim1):
        return None, None
    base = ff0[ff0["t"] < stim1]["F_over_F0"].dropna()
    if base.empty:
        return None, None
    mu = float(base.mean())
    sd = float(base.std(ddof=0))
    if not np.isfinite(sd):
        return None, None
    return mu - sd_k * sd, mu + sd_k * sd


def filter_traces(
    ff0: pd.DataFrame,
    *,
    stim1: float | None,
    stim2: float | None,
    stim3: float | None,
    sd_k: float,
    require_full: bool,
    stim2_neg_frac: float,
    stim2_below_value: float,
) -> pd.DataFrame:
    if ff0.empty:
        return ff0

    all_times = np.sort(ff0["t"].dropna().unique())
    stim2_mask_all = _stim_window_mask(ff0["t"], stim2, stim3)

    keep_keys = []
    for (fld, obj), g in ff0.groupby(["Field", "ObjectNo"]):
        g = g.dropna(subset=["t", "F_over_F0"])
        if g.empty:
            continue

        if stim1 is not None and np.isfinite(stim1):
            if g["t"].min() >= stim1:
                continue
            if not (g["t"] < stim1).any():
                continue

        if require_full:
            g_times = np.sort(g["t"].unique())
            if len(g_times) != len(all_times) or not np.array_equal(g_times, all_times):
                continue

        if stim1 is not None and np.isfinite(stim1):
            base_g = g[g["t"] < stim1]["F_over_F0"].dropna()
            if base_g.empty:
                continue
            obj_mu = float(base_g.mean())
            obj_sd = float(base_g.std(ddof=0))
            if np.isfinite(obj_sd):
                obj_lower = obj_mu - sd_k * obj_sd
                obj_upper = obj_mu + sd_k * obj_sd
                if ((base_g < obj_lower) | (base_g > obj_upper)).any():
                    continue

        if stim2 is not None and np.isfinite(stim2):
            stim2_g = g[stim2_mask_all]["F_over_F0"]
            if stim2_g.empty:
                continue
            neg_frac = float((stim2_g < stim2_below_value).mean())
            if neg_frac > stim2_neg_frac:
                continue

        keep_keys.append((fld, obj))

    if not keep_keys:
        return ff0.iloc[0:0]
    key_df = pd.DataFrame(keep_keys, columns=["Field", "ObjectNo"])
    out = ff0.merge(key_df, on=["Field", "ObjectNo"], how="inner")
    return out


def _plot_spaghetti(
    ff0: pd.DataFrame,
    *,
    title: str,
    outpath: Path,
    stim_list: list[float],
    yscale: str,
    ylim: tuple[float, float] | None,
    per_object_color: str,
    alpha_lines: float,
    linewidth: float,
):
    fig = plt.figure(figsize=(6.5, 4.3), dpi=150)
    ax = plt.gca()
    try:
        ax.set_yscale(yscale)
    except Exception:
        ax.set_yscale("linear")
    if ylim:
        ax.set_ylim(*ylim)

    ax.axhline(1.0, linestyle="--", linewidth=0.9, alpha=0.7, color="#666666", zorder=1)
    if stim_list:
        _draw_stim_vlines(ax, stim_list)

    groups = ff0.groupby(["Field", "ObjectNo"])
    palette = OKABE_ITO[1:]
    color_single = OKABE_ITO[5]
    for i, ((_fld, _obj), g) in enumerate(groups):
        g = g.sort_values("t")
        color = (palette[i % len(palette)] if per_object_color == "cycle" else color_single)
        ax.plot(g["t"].values, g["F_over_F0"].values,
                linewidth=linewidth, alpha=alpha_lines, color=color, zorder=2)

    ax.set_title(title, fontsize=12)
    ax.set_xlabel("Time [s]", fontsize=12)
    ax.set_ylabel("F / F0", fontsize=12)
    ax.tick_params(labelsize=12)
    ax.margins(x=0.05)
    fig.tight_layout()

    outpath.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(outpath)
    plt.close(fig)


def _title_for_well(ctx: ContextLabel, well: str) -> str:
    return f"{ctx.title_prefix()} Well {well}"


def _title_for_fov(ctx: ContextLabel, well: str, fov: int | float) -> str:
    return f"{ctx.title_prefix()} Well {well} FOV {fov}"


def _objects_png_path(outdir: Path, base_prefix: str, well: str) -> Path:
    return outdir / f"{well_file_prefix(base_prefix, well)}__objects_included.png"


def _fov_png_path(outdir: Path, base_prefix: str, well: str, fov: int | float) -> Path:
    return outdir / f"{well_file_prefix(base_prefix, well)}__fov_{int(fov)}_included.png"


def _load_responder_keys(xlsx: Path) -> dict[str, set[tuple[int, int]]] | None:
    """
    Load (Field, ObjectNo) pairs for Stim1 or Stim2 responders from the workbook.

    Reads the Responder_Object_Level sheet (if present) and returns a dict of
    {well_name: {(field, obj), ...}} containing only objects that are Stim1
    and/or Stim2 responders.  Returns None if the sheet is absent or unusable.
    """
    try:
        xls = pd.ExcelFile(xlsx)
        if "Responder_Object_Level" not in xls.sheet_names:
            return None
        df = pd.read_excel(xlsx, sheet_name="Responder_Object_Level")
    except Exception:
        return None

    df.columns = [c.strip() for c in df.columns]
    cols = list(df.columns)

    well_col  = next((c for c in cols if re.search(r"^well$", c, re.I)), None)
    field_col = next((c for c in cols if re.search(r"^field$", c, re.I)), None)
    obj_col   = next((c for c in cols if re.search(r"^object\s*no$|^objectno$", c, re.I)), None)
    stim1_col = next((c for c in cols if re.search(r"^is_stim1_responder$", c, re.I)), None)
    stim2_col = next((c for c in cols if re.search(r"^is_stim2_responder$", c, re.I)), None)

    if not (well_col and field_col and obj_col):
        return None
    if stim1_col is None and stim2_col is None:
        return None

    responder_keys: dict[str, set[tuple[int, int]]] = {}
    for _, row in df.iterrows():
        well_val = str(row[well_col])
        try:
            fld = int(float(row[field_col]))
            obj = int(float(row[obj_col]))
        except (ValueError, TypeError):
            continue

        stim1_ok = False
        stim2_ok = False
        if stim1_col is not None:
            v = pd.to_numeric(row[stim1_col], errors="coerce")
            stim1_ok = bool(v == 1)
        if stim2_col is not None:
            v = pd.to_numeric(row[stim2_col], errors="coerce")
            stim2_ok = bool(v == 1)

        if stim1_ok or stim2_ok:
            responder_keys.setdefault(well_val, set()).add((fld, obj))

    return responder_keys if responder_keys else None


def _apply_responder_filter(
    ff0: pd.DataFrame,
    responder_keys: set[tuple[int, int]] | None,
) -> pd.DataFrame:
    """Keep only rows whose (Field, ObjectNo) pair is a known responder."""
    if ff0.empty or not responder_keys:
        return ff0.iloc[0:0]
    mask = [
        (int(r["Field"]), int(r["ObjectNo"])) in responder_keys
        for _, r in ff0[["Field", "ObjectNo"]].iterrows()
    ]
    return ff0[mask]


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Included spaghetti plots per well and per FOV.")
    ap.add_argument("xlsx", help="Path to per-well workbook")
    ap.add_argument("--outdir", help="Output directory (default: <xlsx_dir>/plots/included traces)")
    ap.add_argument("--filename-prefix", help="Override filename prefix for outputs")

    ap.add_argument("--size-iqr-k", type=float, default=1.5)
    ap.add_argument("--intensity-iqr-k", type=float, default=1.5)
    ap.add_argument("--scope", choices=["timepoint", "well"], default="timepoint")
    ap.add_argument("--no-roi-baseline", action="store_true")

    ap.add_argument("--baseline-sd-k", type=float, default=1.0,
                    help="Baseline SD threshold (mean ± k*SD before stim1).")
    ap.add_argument("--allow-gaps", action="store_true",
                    help="Allow traces missing timepoints (default: no gaps).")
    ap.add_argument("--stim2-neg-frac", type=float, default=0.5,
                    help="Drop trace if fraction of stim2 window below 0 exceeds this value.")
    ap.add_argument("--stim2-below", type=float, default=1.0,
                    help="Threshold for stim2 'negative' response (default: 1.0).")

    ap.add_argument("--yscale", choices=["linear", "log", "symlog"], default="linear")
    ap.add_argument("--ylim", type=str, default=None)
    ap.add_argument("--per-object-color", choices=["single", "cycle"], default="cycle")
    ap.add_argument("--alpha-lines", type=float, default=0.35)
    ap.add_argument("--linewidth", type=float, default=0.9)

    ap.add_argument("--no-per-object", action="store_true",
                    help="Skip per-well per-object spaghetti plots.")
    ap.add_argument("--no-per-fov", action="store_true",
                    help="Skip per-FOV spaghetti plots.")

    args = ap.parse_args()

    xlsx = Path(args.xlsx).resolve()
    if not xlsx.is_file():
        raise SystemExit(f"Not found: {xlsx}")
    xlsx = prefer_filtered_xlsx(xlsx)

    date6, ex_num, ev_num = extract_context_from_path(xlsx)
    ctx = ContextLabel(date6, ex_num, ev_num)
    ctx_prefix = args.filename_prefix.strip() if args.filename_prefix else build_filename_prefix(date6, ex_num, ev_num)

    base = xlsx.stem
    outdir = Path(args.outdir).resolve() if args.outdir else (xlsx.parent / "plots" / "included traces")

    explicit_ylim = None
    if args.ylim:
        try:
            y0, y1 = [float(v.strip()) for v in args.ylim.split(",")]
            explicit_ylim = (y0, y1)
        except Exception:
            raise SystemExit("Invalid --ylim format. Use like: --ylim -0.5,7.0")

    xls = pd.ExcelFile(xlsx)
    _SUMMARY_SHEETS = {"Plate_Overview", "Object_Stats", "Responder_Overview",
                       "Responder_Object_Level", "Included_Object_Log", "Filtered_Object_Log"}
    sheets = [s for s in xls.sheet_names if s not in _SUMMARY_SHEETS]

    # Load responder status once for all wells
    all_responder_keys = _load_responder_keys(xlsx)
    if all_responder_keys is None:
        print("[info] No Responder_Object_Level sheet found; included plots will use trace-quality filters only.")

    for sheet in sheets:
        df, _, _, _ = read_well_sheet_minimal(xlsx, sheet)
        stim_list = stims_from_df(df)
        stim1 = stim_list[0] if len(stim_list) >= 1 else None
        stim2 = stim_list[1] if len(stim_list) >= 2 else None
        stim3 = stim_list[2] if len(stim_list) >= 3 else None

        kept, base_mask, _time_col, _ = preprocess_df_for_well(
            df,
            size_k=args.size_iqr_k,
            inten_k=args.intensity_iqr_k,
            scope=args.scope,
            stim_time=stim1,
            baseline_n=1,
            baseline_window=None,
        )
        if kept is None:
            print(f"  [{sheet}] skipped (missing columns or all excluded)")
            continue

        ff0 = ff0_per_roi(kept, base_mask, prefer_roi=not args.no_roi_baseline)
        ff0 = filter_traces(
            ff0,
            stim1=stim1,
            stim2=stim2,
            stim3=stim3,
            sd_k=args.baseline_sd_k,
            require_full=not args.allow_gaps,
            stim2_neg_frac=args.stim2_neg_frac,
            stim2_below_value=args.stim2_below,
        )
        if ff0.empty:
            print(f"  [{sheet}] all traces removed by filters.")
            continue

        # Restrict to Stim1 and/or Stim2 responders if responder data is available
        if all_responder_keys is not None:
            well_keys = all_responder_keys.get(sheet)
            ff0 = _apply_responder_filter(ff0, well_keys)
            if ff0.empty:
                print(f"  [{sheet}] no Stim1/Stim2 responders remain after filtering.")
                continue

        if not args.no_per_object:
            outpath = _objects_png_path(outdir, ctx_prefix, sheet)
            title = _title_for_well(ctx, sheet)
            _plot_spaghetti(
                ff0,
                title=title,
                outpath=outpath,
                stim_list=stim_list,
                yscale=args.yscale,
                ylim=explicit_ylim,
                per_object_color=args.per_object_color,
                alpha_lines=args.alpha_lines,
                linewidth=args.linewidth,
            )

        if not args.no_per_fov:
            for fld, g in ff0.groupby("Field"):
                if pd.isna(fld):
                    continue
                outpath = _fov_png_path(outdir, ctx_prefix, sheet, fld)
                title = _title_for_fov(ctx, sheet, fld)
                _plot_spaghetti(
                    g,
                    title=title,
                    outpath=outpath,
                    stim_list=stim_list,
                    yscale=args.yscale,
                    ylim=explicit_ylim,
                    per_object_color=args.per_object_color,
                    alpha_lines=args.alpha_lines,
                    linewidth=args.linewidth,
                )

    print(f"Done. Wrote included plots to: {outdir}")


if __name__ == "__main__":
    main()
