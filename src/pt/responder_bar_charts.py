# -*- coding: utf-8 -*-
"""
Create per-well Stim1 / General Responders (%) bar charts.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from pt.pt_utils import OKABE_ITO


def extract_context_from_path(path: Path) -> Tuple[str, str, str]:
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
        print(f"[info] Using included workbook: {candidate}")
        return candidate
    return xlsx_path


def build_filename_prefix(
    date6: str, ex_num: str, ev_num: str, well: Optional[str] = None
) -> str:
    parts = []
    if date6 != "NA":
        parts.append(date6)
    if ex_num != "NA":
        parts.append(f"Ex{ex_num}")
    if ev_num != "NA":
        parts.append(f"Ev{ev_num}")
    if well:
        parts.append(well)
    return "_".join(parts) if parts else "unknown"


_SUMMARY_SHEETS = {"Plate_Overview", "Object_Stats", "Responder_Overview",
        "Responder_Object_Level", "Included_Object_Log", "Filtered_Object_Log"}


def read_overview_data(xlsx_path: Path) -> tuple[pd.DataFrame, set[str]]:
    """
    Returns (overview_df, tested_wells) where tested_wells is the set of well
    names that have a per-well sheet in the workbook (i.e. were actually
    processed by the pipeline), regardless of whether any objects passed.
    """
    xls = pd.ExcelFile(xlsx_path)
    if "Responder_Overview" not in xls.sheet_names:
        raise ValueError(f"Sheet 'Responder_Overview' not found in {xlsx_path}")

    overview_df = pd.read_excel(xls, sheet_name="Responder_Overview")
    overview_df.columns = [c.strip() for c in overview_df.columns]
    if "Well" not in overview_df.columns:
        raise ValueError("Column 'Well' not found in Responder_Overview sheet.")

    tested_wells = {s for s in xls.sheet_names if s not in _SUMMARY_SHEETS}
    return overview_df, tested_wells


def plot_well_stim1_general_responder_quotient(
    overview_df: pd.DataFrame,
    outdir: Path,
    filename_prefix: str = "",
    figsize: tuple[float, float] = (12, 6),
    tested_wells: "set[str] | None" = None,
):
    """
    Create bar chart per well with Stim1 responders as a percentage of General responders.

    Wells that were processed by the pipeline (present in tested_wells) but produced
    no responder data are included at 0% and marked with '*' in their x-axis label.
    All tested wells are marked with '*' so they are distinguishable from wells that
    simply were not part of the experiment.
    """
    required_cols = {"Well", "stim1_responders", "general_responders"}
    missing = required_cols.difference(overview_df.columns)
    if missing:
        raise ValueError(f"Missing required columns for stim1/general quotient plot: {sorted(missing)}")

    # Add zero-rows for tested wells that never made it into Responder_Overview
    if tested_wells:
        overview_wells = set(overview_df["Well"].astype(str))
        missing_tested = tested_wells - overview_wells
        if missing_tested:
            zero_rows = pd.DataFrame([
                {"Well": w, "stim1_responders": 0, "general_responders": 0,
                 "n_objects": 0, "stim1_nonresponders": 0}
                for w in sorted(missing_tested)
            ])
            overview_df = pd.concat([overview_df, zero_rows], ignore_index=True)

    stim1_resp = pd.to_numeric(overview_df["stim1_responders"], errors="coerce").fillna(0)
    general_resp = pd.to_numeric(overview_df["general_responders"], errors="coerce").fillna(0)
    stim1_over_general_pct = (stim1_resp / general_resp.replace(0, np.nan) * 100).fillna(0)

    wells = overview_df["Well"].astype(str).tolist()
    well_order = (
        pd.Series(wells)
        .str.extract(r"^([A-Za-z])\s*0*([0-9]+)$")
        .rename(columns={0: "row", 1: "col"})
    )
    well_order["row"] = well_order["row"].str.upper()
    well_order["col"] = pd.to_numeric(well_order["col"], errors="coerce")
    well_order["orig_idx"] = np.arange(len(wells))
    well_order["row_rank"] = well_order["row"].map({c: i for i, c in enumerate("ABCDEFGH")})
    well_order = well_order.sort_values(["row_rank", "col", "orig_idx"], na_position="last")
    ordered_idx = well_order["orig_idx"].to_numpy()

    wells = [wells[i] for i in ordered_idx]
    stim1_over_general_pct = stim1_over_general_pct.iloc[ordered_idx].reset_index(drop=True)

    # Build x-axis labels: append '*' to tested wells
    if tested_wells:
        x_labels = [f"{w}*" if w in tested_wells else w for w in wells]
    else:
        x_labels = wells

    fig, ax = plt.subplots(figsize=figsize, dpi=150)
    x_pos = np.arange(len(wells))
    ax.bar(x_pos, stim1_over_general_pct, 0.7, color=OKABE_ITO[2], alpha=0.85)

    ax.set_xlabel("Well", fontsize=12)
    ax.set_ylabel("Percentage (%)", fontsize=12)
    ax.set_title("Stim1 / General Responders (%) per Well", fontsize=13, fontweight="bold")
    ax.set_xticks(x_pos)
    ax.set_xticklabels(x_labels, rotation=90, ha="right")
    ax.set_ylim(0, 100)
    ax.grid(axis="y", alpha=0.3, linestyle="--")

    if tested_wells:
        ax.annotate("* = well was tested", xy=(1, 0), xycoords="axes fraction",
                    fontsize=8, ha="right", va="bottom",
                    xytext=(0, -40), textcoords="offset points", color="dimgray")

    general_resp_counts = (
        pd.to_numeric(overview_df["general_responders"], errors="coerce")
        .fillna(0)
        .astype(int)
        .iloc[ordered_idx]
        .tolist()
    )
    table1 = ax.table(
        cellText=[general_resp_counts],
        rowLabels=["General Responders"],
        cellLoc="center",
        rowLoc="center",
        loc=0,
    )
    table1.auto_set_font_size(True)
    table1.set_fontsize(8)
    table1.scale(1, 1.2)

    plt.tight_layout()
    outdir.mkdir(parents=True, exist_ok=True)
    fname = (
        f"{filename_prefix}_stim1_over_general_percent_per_well.png"
        if filename_prefix
        else "stim1_over_general_percent_per_well.png"
    )
    outpath = outdir / fname
    fig.savefig(outpath, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {outpath}")
    return outpath


def main():
    ap = argparse.ArgumentParser(
        description="Create Stim1 / General Responders (%) bar charts from responder summary data"
    )
    ap.add_argument(
        "xlsx",
        help="Path to Excel file with a Responder_Overview sheet",
    )
    ap.add_argument(
        "--outdir",
        help="Output directory for plots (default: <xlsx_dir>/plots/responder_plots)",
    )

    args = ap.parse_args()

    xlsx_path = Path(args.xlsx).resolve()
    xlsx_path = prefer_filtered_xlsx(xlsx_path)
    if not xlsx_path.is_file():
        raise SystemExit(f"File not found: {xlsx_path}")

    outdir = Path(args.outdir).resolve() if args.outdir else (xlsx_path.parent / "plots" / "responder_plots")

    print(f"Reading responder data from: {xlsx_path}")
    overview_df, tested_wells = read_overview_data(xlsx_path)
    print(f"Found {len(overview_df)} wells in overview data, {len(tested_wells)} tested wells")

    date6, ex_num, ev_num = extract_context_from_path(xlsx_path)
    filename_prefix = build_filename_prefix(date6, ex_num, ev_num)
    print(f"Extracted context: date={date6}, experiment={ex_num}, evaluation={ev_num}")
    print(f"Using filename prefix: {filename_prefix}")

    print("\nCreating per-well Stim1 / General responder % chart...")
    plot_well_stim1_general_responder_quotient(overview_df, outdir, filename_prefix, tested_wells=tested_wells)

    print(f"\nDone! Plot saved to: {outdir}")


if __name__ == "__main__":
    main()
