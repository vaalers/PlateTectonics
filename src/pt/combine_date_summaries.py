# -*- coding: utf-8 -*-
"""
Combine summary sheets across experiments for a given date.

Creates separate summary workbooks for:
  - *_with_obj.xlsx
  - included object workbooks

If wells overlap between experiments (same date), the script will split
into multiple output workbooks to avoid overwriting per-well data.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd
import matplotlib.pyplot as plt

try:
    from pt.pt_utils import OKABE_ITO
except Exception:
    OKABE_ITO = [
        "#000000", "#E69F00", "#56B4E9", "#009E73",
        "#F0E442", "#0072B2", "#D55E00", "#CC79A7",
    ]


INPUT_SHEETS = [
    "Object_Stats",
    "Responder_Overview",
    "Responder_Object_Level",
    "Included_Object_Log",
    "Filtered_Object_Log",
]

OUTPUT_SHEET_MAP = {
    "Responder_Overview": "Responder_well_level",
}

MIN_GENERAL_RESPONDERS_FOR_SCORABLE_WELL = 10
MIN_STIM1_RESPONDERS_FOR_POSITIVE_WELL = 3
DATA_COLOR_NONE = "#FFFFFF"
DATA_COLOR_NOT_SCORABLE = "#D55E00"
DATA_COLOR_SCORABLE = "#BDBDBD"
DATA_COLOR_STIM1_POSITIVE = "#009E73"
DATA_COLOR_ZERO_GENERAL = "#702FA0"


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


def normalize_well_col(df: pd.DataFrame) -> Optional[str]:
    for c in df.columns:
        if str(c).strip().lower() == "well":
            return c
    return None


def extract_wells_from_workbook(xlsx: Path) -> Set[str]:
    """
    Prefer Responder_Overview for well list; fallback to Responder_Object_Level.
    """
    wells: Set[str] = set()
    try:
        xls = pd.ExcelFile(xlsx)
    except Exception:
        return wells

    for sheet in ["Responder_Overview", "Responder_Object_Level"]:
        if sheet not in xls.sheet_names:
            continue
        try:
            df = pd.read_excel(xlsx, sheet_name=sheet)
        except Exception:
            continue
        well_col = normalize_well_col(df)
        if not well_col:
            continue
        vals = (
            df[well_col]
            .astype(str)
            .str.strip()
            .replace({"": pd.NA, "NA": pd.NA, "N/A": pd.NA, "nan": pd.NA})
            .dropna()
            .tolist()
        )
        wells.update(vals)
        if wells:
            break
    return wells


def add_context_columns(df: pd.DataFrame, date6: str, ex_num: str, ev_num: str, source: str) -> pd.DataFrame:
    out = df.copy()
    for col in ["Date", "Experiment #", "Evaluation #", "Source_File"]:
        if col in out.columns:
            out = out.drop(columns=[col])
    out.insert(0, "Source_File", source)
    out.insert(0, "Evaluation #", ev_num)
    out.insert(0, "Experiment #", ex_num)
    out.insert(0, "Date", date6)
    return out


def find_workbooks(date_dir: Path, suffix: str) -> List[Path]:
    files = []
    for p in date_dir.rglob(f"*{suffix}"):
        if p.name.startswith("~$"):
            continue
        files.append(p)
    return sorted(files)


def combine_for_date(date_dir: Path, suffix: str, label: str) -> None:
    is_included = label == "included"
    analysis_dir = date_dir / "Analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    sources = find_workbooks(date_dir, suffix)
    if not sources:
        print(f"[{label}] No inputs found under {date_dir}")
        return

    groups: List[Dict[str, object]] = []
    for src in sources:
        date6, ex_num, ev_num = extract_context_from_path(src)
        wells = extract_wells_from_workbook(src)

        assigned = False
        for g in groups:
            used = g["used_wells"]
            if wells and used.intersection(wells):
                continue
            g["sources"].append(src)
            g["used_wells"].update(wells)
            assigned = True
            break
        if not assigned:
            groups.append({"sources": [src], "used_wells": set(wells)})

    for idx, g in enumerate(groups, start=1):
        sheet_frames: Dict[str, List[pd.DataFrame]] = {s: [] for s in INPUT_SHEETS}
        for src in g["sources"]:
            date6, ex_num, ev_num = extract_context_from_path(src)
            try:
                xls = pd.ExcelFile(src)
            except Exception:
                continue
            for sheet in INPUT_SHEETS:
                if sheet not in xls.sheet_names:
                    continue
                try:
                    df = pd.read_excel(src, sheet_name=sheet)
                except Exception:
                    continue
                if df.empty:
                    continue
                df = add_context_columns(df, date6, ex_num, ev_num, src.name)
                sheet_frames[sheet].append(df)

        out_name = f"{date_dir.name}_{label}_summary"
        if len(groups) > 1:
            out_name = f"{out_name}_plate{idx}"
        out_path = analysis_dir / f"{out_name}.xlsx"

        fov_summary = None
        if sheet_frames.get("Responder_Object_Level"):
            combined_objects = pd.concat(sheet_frames["Responder_Object_Level"], ignore_index=True)
            fov_summary = build_fov_summary(combined_objects)

        combined_included_log = None
        combined_object_stats = None
        with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
            for sheet in INPUT_SHEETS:
                if not sheet_frames[sheet]:
                    continue
                combined = pd.concat(sheet_frames[sheet], ignore_index=True)
                if sheet == "Filtered_Object_Log":
                    out_sheet = "Included_Object_Log"
                else:
                    out_sheet = OUTPUT_SHEET_MAP.get(sheet, sheet)
                combined.to_excel(writer, index=False, sheet_name=out_sheet)
                if sheet in {"Included_Object_Log", "Filtered_Object_Log"}:
                    combined_included_log = combined
                if sheet == "Object_Stats":
                    combined_object_stats = combined
            if fov_summary is not None and not fov_summary.empty:
                fov_summary.to_excel(writer, index=False, sheet_name="Responder_FOV_Level")
            if is_included and combined_included_log is not None:
                pivot = build_filter_fail_pivot(combined_included_log)
                if not pivot.empty:
                    pivot.to_excel(writer, index=False, sheet_name="Filter_Fail_Pivot")

        print(f"[{label}] Wrote: {out_path}")

        if is_included and "Responder_Overview" in sheet_frames and sheet_frames["Responder_Overview"]:
            combined_overview = pd.concat(sheet_frames["Responder_Overview"], ignore_index=True)
            tested_wells = set()
            if combined_included_log is not None and "well" in [str(c).strip().lower() for c in combined_included_log.columns]:
                for c in combined_included_log.columns:
                    if str(c).strip().lower() == "well":
                        tested_wells.update(
                            combined_included_log[c]
                            .astype(str)
                            .str.strip()
                            .replace({"": pd.NA, "NA": pd.NA, "N/A": pd.NA, "nan": pd.NA})
                            .dropna()
                            .tolist()
                        )
                        break
            if combined_object_stats is not None:
                for c in combined_object_stats.columns:
                    if str(c).strip().lower() == "well":
                        tested_wells.update(
                            combined_object_stats[c]
                            .astype(str)
                            .str.strip()
                            .replace({"": pd.NA, "NA": pd.NA, "N/A": pd.NA, "nan": pd.NA})
                            .dropna()
                            .tolist()
                        )
                        break
            _write_stim1_general_plot(
                combined_overview,
                analysis_dir,
                date_dir.name,
                idx,
                g["sources"],
                tested_wells if tested_wells else None,
            )
        if is_included:
            _write_combined_traces_ppt(
                out_path,
                g["sources"],
                analysis_dir,
                idx,
            )


def _well_sort_order(wells: List[str]) -> List[int]:
    well_order = (
        pd.Series(wells)
        .str.extract(r"^([A-Za-z])\s*0*([0-9]+)$")
        .rename(columns={0: "row", 1: "col"})
    )
    well_order["row"] = well_order["row"].str.upper()
    well_order["col"] = pd.to_numeric(well_order["col"], errors="coerce")
    well_order["orig_idx"] = range(len(wells))
    well_order["row_rank"] = well_order["row"].map({c: i for i, c in enumerate("ABCDEFGH")})
    well_order = well_order.sort_values(
        ["row_rank", "col", "orig_idx"],
        na_position="last",
    )
    return well_order["orig_idx"].to_list()


def _all_96_well_plate_wells() -> List[str]:
    return [f"{row}{col:02d}" for row in "ABCDEFGH" for col in range(1, 13)]


def _normalize_well_id(well: str) -> str:
    match = re.match(r"^([A-Ha-h])\s*0*([0-9]+)$", well.strip())
    if not match:
        return well.strip().upper()
    row = match.group(1).upper()
    col = int(match.group(2))
    return f"{row}{col:02d}"


def _find_eval_dir_from_source(source: Path) -> Optional[Path]:
    for p in source.parents:
        if re.fullmatch(r"Evaluation\d+", p.name):
            return p
    if source.parent.name == "individual traces" and source.parent.parent.name == "plots":
        return source.parent.parent.parent
    return None


def _parse_well_from_png_name(name: str) -> Optional[str]:
    match = re.search(r"_([A-H][0-9]{1,2})__objects\.png$", name, flags=re.IGNORECASE)
    if not match:
        return None
    return _normalize_well_id(match.group(1))


def _collect_combined_trace_pngs(
    sources: Iterable[Path],
    trace_kind: str,
) -> Dict[str, Path]:
    """
    Locate combined-trace PNGs for wells from the given source workbooks.
    trace_kind: "raw" or "included"
    """
    wells_to_png: Dict[str, Path] = {}
    for src in sources:
        eval_dir = _find_eval_dir_from_source(src)
        if eval_dir is None:
            continue

        # Pipeline writes spaghetti plots to plots/spaghetti/raw/ and plots/spaghetti/included/.
        # Legacy runs used plots/combined traces/raw| included/.
        spaghetti_base = eval_dir / "plots" / "spaghetti"
        legacy_base    = eval_dir / "plots" / "combined traces"
        base_dir = spaghetti_base if spaghetti_base.is_dir() else legacy_base
        raw_dir = base_dir / "raw"
        filtered_dir = base_dir / "included"
        legacy_filtered_dir = base_dir / "filtered"

        if trace_kind == "raw":
            if raw_dir.is_dir():
                trace_dir = raw_dir
            elif not filtered_dir.is_dir() and base_dir.is_dir():
                trace_dir = base_dir
            else:
                continue
        elif trace_kind == "included":
            if filtered_dir.is_dir():
                trace_dir = filtered_dir
            elif legacy_filtered_dir.is_dir():
                trace_dir = legacy_filtered_dir
            elif not raw_dir.is_dir() and base_dir.is_dir() and src.name.endswith("_filtered_objects.xlsx"):
                trace_dir = base_dir
            else:
                continue
        else:
            continue

        if not trace_dir.is_dir():
            continue
        for png in trace_dir.glob("*__objects.png"):
            well = _parse_well_from_png_name(png.name)
            if not well:
                continue
            if well not in wells_to_png:
                wells_to_png[well] = png
    return wells_to_png


def _format_context_subtitle(summary_xlsx: Path) -> str:
    date6 = summary_xlsx.stem.split("_")[0]
    try:
        df = pd.read_excel(summary_xlsx, sheet_name="Object_Stats")
    except Exception:
        return f"Date: {date6}"

    cols = [str(c).strip().lower() for c in df.columns]
    col_map = {c: i for i, c in enumerate(cols)}
    date_col = df.columns[col_map.get("date")] if "date" in col_map else None
    exp_col = df.columns[col_map.get("experiment #")] if "experiment #" in col_map else None
    eval_col = df.columns[col_map.get("evaluation #")] if "evaluation #" in col_map else None

    def _uniq(col):
        if col is None:
            return []
        return (
            df[col]
            .astype(str)
            .str.strip()
            .replace({"": pd.NA, "NA": pd.NA, "N/A": pd.NA, "nan": pd.NA})
            .dropna()
            .unique()
            .tolist()
        )

    dates = _uniq(date_col) or [date6]
    exps = _uniq(exp_col)
    evals = _uniq(eval_col)

    combos = set()
    if date_col and exp_col and eval_col:
        for _, row in df[[date_col, exp_col, eval_col]].dropna().iterrows():
            d = str(row[date_col]).strip()
            e = str(row[exp_col]).strip()
            v = str(row[eval_col]).strip()
            if d and e and v:
                combos.add(f"{d}/Analysis/Experiment_{d}_{e}/Evaluation{v}")

    date_part = "-".join(sorted(set(dates)))
    if combos:
        path_part = "; ".join(sorted(combos))
        return f"Date: {date_part} | Paths: {path_part}"
    exp_part = "-".join(sorted(set(exps))) if exps else "NA"
    eval_part = "-".join(sorted(set(evals))) if evals else "NA"
    return f"Date: {date_part} | Experiment: {exp_part} | Evaluation: {eval_part}"


def _write_combined_traces_ppt(
    summary_xlsx: Path,
    sources: Iterable[Path],
    outdir: Path,
    plate_idx: int,
) -> Optional[Path]:
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except Exception:
        print("  [ppt] python-pptx not installed; skipping combined traces PPTX.")
        return None

    wells_order = _all_96_well_plate_wells()
    subtitle = _format_context_subtitle(summary_xlsx)

    ex_nums = sorted({extract_context_from_path(s)[1] for s in sources})
    ev_nums = sorted({extract_context_from_path(s)[2] for s in sources})
    ex_part = "-".join([str(e) for e in ex_nums if e != "NA"]) or "NA"
    ev_part = "-".join([str(e) for e in ev_nums if e != "NA"]) or "NA"
    date6 = summary_xlsx.stem.split("_")[0]
    responder_name = f"{date6}_responder_summary_plate{plate_idx}_experiment{ex_part}_eval{ev_part}.png"
    responder_png = outdir / responder_name

    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    if responder_png.exists():
        slide = prs.slides.add_slide(blank)
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.2), Inches(12.5), Inches(0.6))
        title_tf = title_box.text_frame
        title_tf.clear()
        title_run = title_tf.paragraphs[0].add_run()
        title_run.text = "Responder Summary"
        title_run.font.size = Pt(20)
        title_run.font.bold = True

        subtitle_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.75), Inches(12.5), Inches(0.4))
        subtitle_tf = subtitle_box.text_frame
        subtitle_tf.clear()
        subtitle_run = subtitle_tf.paragraphs[0].add_run()
        subtitle_run.text = subtitle
        subtitle_run.font.size = Pt(12)

        pic = slide.shapes.add_picture(str(responder_png), Inches(0.4), Inches(1.1), width=Inches(12.6))
        max_h = Inches(6.2)
        if pic.height > max_h:
            scale = max_h / float(pic.height)
            pic.height = max_h
            pic.width = int(round(pic.width * scale))
            pic.left = int((prs.slide_width - pic.width) / 2)
            pic.top = int(Inches(1.1) + (max_h - pic.height) / 2)

    def add_slide(title: str, trace_kind: str) -> None:
        slide = prs.slides.add_slide(blank)
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.2), Inches(12.5), Inches(0.6))
        title_tf = title_box.text_frame
        title_tf.clear()
        title_run = title_tf.paragraphs[0].add_run()
        title_run.text = title
        title_run.font.size = Pt(20)
        title_run.font.bold = True

        subtitle_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.75), Inches(12.5), Inches(0.4))
        subtitle_tf = subtitle_box.text_frame
        subtitle_tf.clear()
        subtitle_run = subtitle_tf.paragraphs[0].add_run()
        subtitle_run.text = subtitle
        subtitle_run.font.size = Pt(12)

        wells_to_png = _collect_combined_trace_pngs(sources, trace_kind)
        margin_x = Inches(0.3)
        margin_y = Inches(1.2)
        hgap = Inches(0.05)
        vgap = Inches(0.05)
        cols, rows = 12, 8
        cell_w = (prs.slide_width - 2 * margin_x - (cols - 1) * hgap) / cols
        cell_h = (prs.slide_height - margin_y - Inches(0.2) - (rows - 1) * vgap) / rows

        for idx, well in enumerate(wells_order):
            r, c = divmod(idx, cols)
            left = margin_x + c * (cell_w + hgap)
            top = margin_y + r * (cell_h + vgap)
            png = wells_to_png.get(well)
            if png is None or not png.exists():
                continue
            pic = slide.shapes.add_picture(str(png), left, top, width=cell_w)
            if pic.height > cell_h:
                scale = cell_h / float(pic.height)
                pic.height = cell_h
                pic.width = int(round(pic.width * scale))
                pic.left = int(left + (cell_w - pic.width) / 2)
            pic.top = int(top + (cell_h - pic.height) / 2)

    add_slide("Combined Traces (Raw)", "raw")
    add_slide("Combined Traces (Included)", "included")

    out_name = f"{summary_xlsx.stem}_combined_traces_plate{plate_idx}.pptx"
    out_path = outdir / out_name
    prs.save(str(out_path))
    print(f"  [ppt] Saved: {out_path}")
    return out_path


def _write_stim1_general_plot(
    overview_df: pd.DataFrame,
    outdir: Path,
    date6: str,
    plate_idx: int,
    sources: Iterable[Path],
    tested_wells: Optional[Set[str]] = None,
) -> None:
    required_cols = {"Well", "stim1_responders", "general_responders"}
    if not required_cols.issubset(set(overview_df.columns)):
        return

    df = overview_df.copy()
    df["Well"] = df["Well"].astype(str).str.strip()
    df = df[df["Well"].notna() & (df["Well"] != "")]

    df["Well"] = df["Well"].map(_normalize_well_id)
    df["stim1_responders"] = pd.to_numeric(df["stim1_responders"], errors="coerce").fillna(0)
    df["general_responders"] = pd.to_numeric(df["general_responders"], errors="coerce").fillna(0)
    df = (
        df.groupby("Well", dropna=True)[["stim1_responders", "general_responders"]]
        .sum()
        .reset_index()
    )

    all_wells = _all_96_well_plate_wells()
    df = df.set_index("Well").reindex(all_wells, fill_value=0).reset_index()
    if df.empty:
        return

    stim1_resp = df["stim1_responders"].astype("Float64").fillna(0)
    general_resp = df["general_responders"].astype("Float64").fillna(0)
    stim1_over_general_pct = (stim1_resp / general_resp.replace(0, pd.NA) * 100).fillna(0)

    wells = df["Well"].astype(str).tolist()
    general_counts = general_resp.astype(int).tolist()
    stim1_counts = stim1_resp.astype(int).tolist()

    # Data-state colors: green = Stim1 positive, gray = scorable, red = used but not scorable, white = no data.
    # Prefer explicit tested_wells (derived from Object_Stats / Included_Object_Log); otherwise fall back
    # to "present in overview input".
    if tested_wells:
        has_data = {_normalize_well_id(w) for w in tested_wells}
    else:
        # wells that existed in the input overview before reindexing to all 96
        has_data = set(overview_df["Well"].astype(str).str.strip().map(_normalize_well_id).dropna().tolist())
    data_row = [""] * len(wells)

    data_colors = []
    for well, general_count, stim1_count in zip(wells, general_counts, stim1_counts):
        if well not in has_data:
            data_colors.append(DATA_COLOR_NONE)
        elif general_count == 0:
            data_colors.append(DATA_COLOR_ZERO_GENERAL)
        elif general_count < MIN_GENERAL_RESPONDERS_FOR_SCORABLE_WELL:
            data_colors.append(DATA_COLOR_NOT_SCORABLE)
        elif stim1_count > MIN_STIM1_RESPONDERS_FOR_POSITIVE_WELL:
            data_colors.append(DATA_COLOR_STIM1_POSITIVE)
        else:
            data_colors.append(DATA_COLOR_SCORABLE)

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    x_pos = range(len(wells))
    ax.bar(x_pos, stim1_over_general_pct, width=0.7, color=data_colors, alpha=0.85)
    ax.set_ylabel("Percentage (%)", fontsize=12)
    ax.set_title("Stim1 / General Responders (%) per Well", fontsize=13, fontweight="bold")
    ax.set_xticks(list(x_pos))
    ax.set_xticklabels([""] * len(wells))
    ax.tick_params(axis="x", length=0)
    ax.set_xlim(-0.5, len(wells) - 0.5)
    ax.margins(x=0)
    ax.set_ylim(0, 100)
    ax.grid(False)

    col_widths = [1.0 / max(1, len(wells))] * len(wells)
    table = ax.table(
        cellText=[wells, general_counts, stim1_counts, data_row],
        rowLabels=[" ", "General", "Stim1", "Data"],
        cellLoc="center",
        rowLoc="center",
        colWidths=col_widths,
        bbox=[0.0, -0.42, 1.0, 0.42],
    )
    table.auto_set_font_size(True)
    table.scale(1, 1)
    for (row, col), cell in table.get_celld().items():
        if row in (0, 1, 2) and col >= 0:
            cell.get_text().set_rotation(90)
            cell.set_height(cell.get_height() * 1.4)
            cell.set_linewidth(0)
        if row == 3 and col >= 0:
            cell.set_facecolor(data_colors[col] if 0 <= col < len(data_colors) else DATA_COLOR_NONE)
            cell.set_linewidth(0.4)
            cell.set_edgecolor("#DDDDDD")
        if col == -1:
            cell.set_linewidth(0)
    plt.subplots_adjust(bottom=0.46)
    cell.set_linewidth(0)

    ex_nums = sorted({extract_context_from_path(s)[1] for s in sources})
    ev_nums = sorted({extract_context_from_path(s)[2] for s in sources})
    ex_part = "-".join([str(e) for e in ex_nums if e != "NA"]) or "NA"
    ev_part = "-".join([str(e) for e in ev_nums if e != "NA"]) or "NA"

    out_name = f"{date6}_responder_summary_plate{plate_idx}_experiment{ex_part}_eval{ev_part}.png"
    out_path = outdir / out_name
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [plot] Saved: {out_path}")

    _write_responder_annotation_heatmap(
        wells, data_colors, outdir, date6, plate_idx, ex_part, ev_part
    )


def _write_responder_annotation_heatmap(
    wells: List[str],
    data_colors: List[str],
    outdir: Path,
    date6: str,
    plate_idx: int,
    ex_part: str,
    ev_part: str,
) -> None:
    color_map = dict(zip(wells, data_colors))

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    ax.set_title(f"Responder Annotation Heatmap — {date6}", fontsize=13, fontweight="bold")

    for r, row_name in enumerate("ABCDEFGH"):
        for c in range(12):
            well = f"{row_name}{c + 1:02d}"
            color = color_map.get(well, DATA_COLOR_NONE)
            ax.add_patch(
                plt.Rectangle((c, 7 - r), 1, 1, facecolor=color, edgecolor="#DDDDDD", linewidth=0.7)
            )

    ax.set_xlim(0, 12)
    ax.set_ylim(0, 8)
    ax.set_xticks([i + 0.5 for i in range(12)])
    ax.set_yticks([i + 0.5 for i in range(8)])
    ax.set_xticklabels([str(i) for i in range(1, 13)])
    ax.set_yticklabels(list("HGFEDCBA"))
    ax.set_aspect("equal")
    ax.tick_params(length=0)
    ax.set_xlabel("Column", fontsize=12)
    ax.set_ylabel("Row", fontsize=12)

    from matplotlib.patches import Patch

    ax.legend(
        handles=[
            Patch(facecolor=DATA_COLOR_STIM1_POSITIVE, edgecolor="#444444", label="responder well"),
            Patch(facecolor=DATA_COLOR_SCORABLE, edgecolor="#444444", label="scorable well"),
            Patch(facecolor=DATA_COLOR_NOT_SCORABLE, edgecolor="#444444", label="Not scorable"),
            Patch(facecolor=DATA_COLOR_ZERO_GENERAL, edgecolor="#444444", label="Zero general responders"),
            Patch(facecolor=DATA_COLOR_NONE, edgecolor="#444444", label="No data"),
        ],
        loc="center left",
        bbox_to_anchor=(1, 0.5),
        frameon=False,
    )

    heatmap_name = f"{date6}_responder_annotation_heatmap_plate{plate_idx}_experiment{ex_part}_eval{ev_part}.png"
    heatmap_path = outdir / heatmap_name
    fig.savefig(heatmap_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  [plot] Saved: {heatmap_path}")


def build_fov_summary(object_df: pd.DataFrame) -> pd.DataFrame:
    df = object_df.copy()
    if "Well" not in df.columns and "well" in df.columns:
        df["Well"] = df["well"]
    if "well" not in df.columns and "Well" in df.columns:
        df["well"] = df["Well"]
    if "Field" not in df.columns and "field" in df.columns:
        df["Field"] = df["field"]

    needed = {"well", "Field", "is_general_responder", "is_stim2_responder", "is_stim1_responder", "stim2_status"}
    if not needed.issubset(set(df.columns)):
        return pd.DataFrame()

    rows = []
    for (well, field), sub in df.groupby(["well", "Field"]):
        general_total = sub["is_general_responder"].notna().sum()
        general_resp = int(pd.to_numeric(sub["is_general_responder"], errors="coerce").fillna(0).astype(int).sum())
        stim2_valid = sub["stim2_status"] == "ok"
        stim2_ambig = sub["stim2_status"] == "ambiguous_pre_high"
        stim2_available = stim2_valid | stim2_ambig
        stim2_resp = int(pd.to_numeric(sub.loc[stim2_valid, "is_stim2_responder"], errors="coerce").fillna(0).astype(int).sum())
        stim2_non = int(stim2_valid.sum() - stim2_resp)
        stim1_resp = int(pd.to_numeric(sub["is_stim1_responder"], errors="coerce").fillna(0).astype(int).sum())
        rows.append({
            "Well": well,
            "Field": field,
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
    return pd.DataFrame(rows)


def build_filter_fail_pivot(included_log: pd.DataFrame) -> pd.DataFrame:
    df = included_log.copy()
    well_col = None
    for c in df.columns:
        if str(c).strip().lower() == "well":
            well_col = c
            break
    reason_col = None
    for c in df.columns:
        if str(c).strip().lower() == "reason":
            reason_col = c
            break
    if well_col is None or reason_col is None:
        return pd.DataFrame()

    df = df[[well_col, reason_col]].rename(columns={well_col: "Well", reason_col: "Reason"})
    df["Well"] = df["Well"].astype(str).str.strip()
    df["Reason"] = df["Reason"].astype(str)
    df = df[(df["Well"] != "") & df["Reason"].notna()]
    if df.empty:
        return pd.DataFrame()

    df["Reason"] = df["Reason"].str.split(";")
    df = df.explode("Reason")
    df["Reason"] = df["Reason"].astype(str).str.strip()
    df = df[df["Reason"] != ""]
    if df.empty:
        return pd.DataFrame()

    pivot = (
        df.groupby("Reason", dropna=True)["Well"]
        .nunique()
        .reset_index(name="Well_Count")
        .sort_values(["Well_Count", "Reason"], ascending=[False, True])
    )
    return pivot


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Combine summary sheets across experiments for one or more dates."
    )
    ap.add_argument("dates", nargs="+", help="Date folder(s), e.g., 081825")
    ap.add_argument("--root", default=".", help="Root containing date folders (default: .)")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    for d in args.dates:
        date_dir = (root / d).resolve()
        if not date_dir.is_dir():
            print(f"[warn] Date folder not found: {date_dir}")
            continue
        combine_for_date(date_dir, "_with_obj.xlsx", "with_obj")
        combine_for_date(date_dir, "_filtered_objects.xlsx", "included")


if __name__ == "__main__":
    main()
