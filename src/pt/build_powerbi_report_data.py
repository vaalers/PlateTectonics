#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

from pt.build_plate_manifest import (
    assign_test_well_ids,
    build_manifest,
    load_existing_test_well_ids,
)
from pt.spaghetti_plot_per_well import normalize_well_str
from pt.pt_utils import find_col, normalize_cols


PLOT_RE = re.compile(
    r"^(?:(?P<date>\d{6})_Ex(?P<experiment>\d+)_Ev(?P<evaluation>\d+)_)?"
    r"(?P<well>[A-H]\d{1,2})__(?P<kind>mean|objects|fov_\d+)\.png$",
    re.IGNORECASE,
)

OBJECT_ASSET_RE = re.compile(
    r"^(?P<date>\d{6})_Ex(?P<experiment>\d+)_Ev(?P<evaluation>\d+)_"
    r"(?P<well>[A-H]\d{1,2})_F(?P<field>\d+)_obj_(?P<object>\d+)\.(?P<ext>csv|png)$",
    re.IGNORECASE,
)


def _clean_text(series: pd.Series) -> pd.Series:
    return (
        series.fillna("")
        .astype(str)
        .str.strip()
        .replace({"nan": "", "None": "", "NaN": ""})
    )


def _mmddyy_to_iso(series: pd.Series) -> pd.Series:
    text = _clean_text(series)
    parsed = pd.to_datetime(text, format="%m%d%y", errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").fillna("")


def _to_bool(series: pd.Series) -> pd.Series:
    text = _clean_text(series).str.lower()
    return text.isin({"true", "1", "yes", "y"})


def _stringify_list(values: Iterable[object]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return " | ".join(out)


def _slug_col(name: object) -> str:
    text = str(name).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _find_manifest_sheet(path: Path) -> str | int:
    xls = pd.ExcelFile(path)
    for sheet in xls.sheet_names:
        if str(sheet).strip().lower() == "manifest":
            return sheet
    return xls.sheet_names[0]


def _normalize_key_columns(
    df: pd.DataFrame, well_col: str | None, field_col: str | None, object_col: str | None
) -> pd.DataFrame:
    out = df.copy()
    if well_col and well_col in out.columns:
        out["well_id"] = _clean_text(out[well_col]).map(normalize_well_str)
    else:
        out["well_id"] = ""
    if field_col and field_col in out.columns:
        out["field"] = pd.to_numeric(out[field_col], errors="coerce").astype("Int64")
    else:
        out["field"] = pd.Series(pd.array([pd.NA] * len(out), dtype="Int64"))
    if object_col and object_col in out.columns:
        out["object_no"] = pd.to_numeric(out[object_col], errors="coerce").astype("Int64")
    else:
        out["object_no"] = pd.Series(pd.array([pd.NA] * len(out), dtype="Int64"))
    return out


def _select_object_sheet_columns(df: pd.DataFrame) -> tuple[str | None, str | None, str | None]:
    cols = normalize_cols(df.columns)
    well_col = find_col(cols, r"^well$", ignore_case=True) or find_col(cols, r"^Well$", ignore_case=True)
    field_col = find_col(cols, r"^field$", ignore_case=True)
    object_col = (
        find_col(cols, r"^object\s*no$", ignore_case=True)
        or find_col(cols, r"^objectno$", ignore_case=True)
        or find_col(cols, r"^object$", ignore_case=True)
    )
    return well_col, field_col, object_col


def _read_sheet_or_empty(xls: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
    if sheet_name not in xls.sheet_names:
        return pd.DataFrame()
    df = pd.read_excel(xls, sheet_name=sheet_name)
    df.columns = normalize_cols(df.columns)
    return df


def load_manifest_table(path: Path) -> pd.DataFrame:
    sheet = _find_manifest_sheet(path)
    return pd.read_excel(path, sheet_name=sheet)


def build_manifest_table_from_roots(
    layout_root: Path,
    supplemental_layout_root: "Path | None",
    analysis_root: Path,
    existing_manifest: Path | None,
) -> pd.DataFrame:
    manifest, _, _ = build_manifest(layout_root, analysis_root, supplemental_layout_root)
    existing_map = load_existing_test_well_ids(existing_manifest) if existing_manifest else {}
    return assign_test_well_ids(manifest, existing_map)


def normalize_manifest(manifest: pd.DataFrame) -> pd.DataFrame:
    df = manifest.copy()
    legacy_included_col = " ".join(["FILTERED", "ANALYSIS", "PATH"])
    included_col = "INCLUDED ANALYSIS PATH" if "INCLUDED ANALYSIS PATH" in df.columns else legacy_included_col
    required = [
        "SAMPLE ID",
        "TEST WELL",
        "DATE",
        "PLATE ID",
        "WELL ID",
        "WELL CELL TYPE",
        "WELL CONDITION",
        "WELL CONDITION 2",
        "ANALYSIS PATH",
        "WITH_OBJ ANALYSIS PATH",
        included_col,
        "Number of total ROIs",
        "Number of scorable ROIs",
        "Number of general responders",
        "Number of ROIs responding to STIM 1",
        "Calculate: Meets criteria to use for data?",
        "Calculate: Meets criteria for positive response?",
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise SystemExit(f"Manifest is missing required columns: {', '.join(missing)}")

    df["sample_id"] = _clean_text(df["SAMPLE ID"])
    df["test_well"] = _clean_text(df["TEST WELL"])
    df["date_code"] = _clean_text(df["DATE"])
    df["analysis_date"] = _mmddyy_to_iso(df["DATE"])
    df["plate_id"] = _clean_text(df["PLATE ID"])
    df["well_id"] = _clean_text(df["WELL ID"]).map(normalize_well_str)
    df["cell_type"] = _clean_text(df["WELL CELL TYPE"])
    df["stim1_condition"] = _clean_text(df["WELL CONDITION"])
    df["stim2_condition"] = _clean_text(df["WELL CONDITION 2"])
    df["analysis_path"] = _clean_text(df["ANALYSIS PATH"])
    df["with_obj_analysis_path"] = _clean_text(df["WITH_OBJ ANALYSIS PATH"])
    df["included_analysis_path"] = _clean_text(df[included_col])
    df["n_total_rois"] = pd.to_numeric(df["Number of total ROIs"], errors="coerce").fillna(0).astype(int)
    df["n_scorable_rois"] = pd.to_numeric(df["Number of scorable ROIs"], errors="coerce").fillna(0).astype(int)
    df["n_general_responders"] = (
        pd.to_numeric(df["Number of general responders"], errors="coerce").fillna(0).astype(int)
    )
    df["n_stim1_responders"] = (
        pd.to_numeric(df["Number of ROIs responding to STIM 1"], errors="coerce").fillna(0).astype(int)
    )
    df["usable"] = _to_bool(df["Calculate: Meets criteria to use for data?"])
    df["stim1_positive"] = _to_bool(df["Calculate: Meets criteria for positive response?"])
    df["analysis_instance_id"] = df["test_well"]
    missing_ids = df["analysis_instance_id"] == ""
    df.loc[missing_ids, "analysis_instance_id"] = (
        df.loc[missing_ids, "date_code"]
        + "|"
        + df.loc[missing_ids, "plate_id"]
        + "|"
        + df.loc[missing_ids, "well_id"]
        + "|"
        + df.loc[missing_ids, "sample_id"]
    )
    return df


def parse_plot_file(path: Path | str) -> dict[str, str] | None:
    path = Path(path)
    match = PLOT_RE.match(path.name)
    if not match:
        return None
    groups = match.groupdict()
    kind = groups["kind"].lower()
    plot_kind = "fov" if kind.startswith("fov_") else kind
    fov = kind.split("_", 1)[1] if plot_kind == "fov" and "_" in kind else ""
    return {
        "plot_path": str(path.resolve()),
        "plot_name": path.name,
        "plot_kind": plot_kind,
        "date_code": groups.get("date") or "",
        "experiment_number": groups.get("experiment") or "",
        "evaluation_number": groups.get("evaluation") or "",
        "well_id": normalize_well_str(groups["well"]),
        "fov": fov,
    }


def parse_object_asset_file(path: Path | str) -> dict[str, str] | None:
    path = Path(path)
    match = OBJECT_ASSET_RE.match(path.name)
    if not match:
        return None
    groups = match.groupdict()
    ext = groups["ext"].lower()
    return {
        "asset_path": str(path.resolve()),
        "asset_name": path.name,
        "asset_type": "object_trace_csv" if ext == "csv" else "object_trace_png",
        "date_code": groups["date"],
        "experiment_number": groups["experiment"],
        "evaluation_number": groups["evaluation"],
        "well_id": normalize_well_str(groups["well"]),
        "field": groups["field"],
        "object_no": groups["object"],
    }


def scan_plot_index(analysis_root: Path) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for pattern in ("*__mean.png", "*__objects.png", "*__fov_*.png"):
        for path in analysis_root.rglob(pattern):
            parsed = parse_plot_file(path)
            if parsed is not None:
                rows.append(parsed)
    columns = [
        "plot_path",
        "plot_name",
        "plot_kind",
        "date_code",
        "experiment_number",
        "evaluation_number",
        "well_id",
        "fov",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    return (
        pd.DataFrame(rows)
        .drop_duplicates()
        .sort_values(["date_code", "well_id", "plot_kind", "experiment_number", "evaluation_number", "plot_name"])
        .reset_index(drop=True)
    )


def scan_object_asset_index(analysis_root: Path) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for pattern in ("*_obj_*.csv", "*_obj_*.png"):
        for path in analysis_root.rglob(pattern):
            parsed = parse_object_asset_file(path)
            if parsed is not None:
                rows.append(parsed)
    columns = [
        "asset_path",
        "asset_name",
        "asset_type",
        "date_code",
        "experiment_number",
        "evaluation_number",
        "well_id",
        "field",
        "object_no",
    ]
    if not rows:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(rows).drop_duplicates()
    out["field"] = pd.to_numeric(out["field"], errors="coerce").astype("Int64")
    out["object_no"] = pd.to_numeric(out["object_no"], errors="coerce").astype("Int64")
    return out.sort_values(
        ["date_code", "well_id", "field", "object_no", "asset_type", "asset_name"]
    ).reset_index(drop=True)


def attach_plot_candidates(well_df: pd.DataFrame, plot_df: pd.DataFrame) -> pd.DataFrame:
    out = well_df.copy()
    if plot_df.empty:
        out["candidate_mean_plot_count"] = 0
        out["candidate_object_plot_count"] = 0
        out["candidate_mean_plot_paths"] = ""
        out["candidate_object_plot_paths"] = ""
        return out

    join_cols = ["date_code", "well_id"]
    grouped = plot_df.groupby(join_cols + ["plot_kind"], dropna=False)["plot_path"].agg(list).reset_index()
    mean_df = grouped[grouped["plot_kind"] == "mean"][join_cols + ["plot_path"]].rename(
        columns={"plot_path": "mean_paths"}
    )
    obj_df = grouped[grouped["plot_kind"] == "objects"][join_cols + ["plot_path"]].rename(
        columns={"plot_path": "object_paths"}
    )
    out = out.merge(mean_df, on=join_cols, how="left")
    out = out.merge(obj_df, on=join_cols, how="left")
    out["candidate_mean_plot_count"] = out["mean_paths"].apply(lambda x: len(x) if isinstance(x, list) else 0)
    out["candidate_object_plot_count"] = out["object_paths"].apply(lambda x: len(x) if isinstance(x, list) else 0)
    out["candidate_mean_plot_paths"] = out["mean_paths"].apply(
        lambda x: _stringify_list(x) if isinstance(x, list) else ""
    )
    out["candidate_object_plot_paths"] = out["object_paths"].apply(
        lambda x: _stringify_list(x) if isinstance(x, list) else ""
    )
    return out.drop(columns=["mean_paths", "object_paths"], errors="ignore")


def build_well_analysis_table(manifest: pd.DataFrame, plot_index: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "analysis_instance_id",
        "test_well",
        "sample_id",
        "date_code",
        "analysis_date",
        "plate_id",
        "well_id",
        "cell_type",
        "stim1_condition",
        "stim2_condition",
        "usable",
        "stim1_positive",
        "n_total_rois",
        "n_scorable_rois",
        "n_general_responders",
        "n_stim1_responders",
        "analysis_path",
        "with_obj_analysis_path",
        "included_analysis_path",
    ]
    well_df = manifest[cols].copy().sort_values(["analysis_date", "date_code", "plate_id", "well_id", "sample_id"])
    well_df = attach_plot_candidates(well_df, plot_index)
    return well_df.reset_index(drop=True)


def build_sample_summary(well_df: pd.DataFrame) -> pd.DataFrame:
    grouped = well_df.groupby("sample_id", dropna=False)
    summary = grouped.agg(
        total_analyses=("analysis_instance_id", "count"),
        unique_dates=("date_code", "nunique"),
        unique_cell_types=("cell_type", "nunique"),
        usable_analyses=("usable", "sum"),
        stim1_positive_analyses=("stim1_positive", "sum"),
    ).reset_index()
    date_bounds = (
        well_df.assign(analysis_date=well_df["analysis_date"].replace("", pd.NA))
        .groupby("sample_id", dropna=False)["analysis_date"]
        .agg(["min", "max"])
        .rename(columns={"min": "first_analysis_date", "max": "last_analysis_date"})
        .reset_index()
        .fillna("")
    )
    cell_types = grouped["cell_type"].agg(_stringify_list).reset_index(name="cell_types")
    stim1_conditions = grouped["stim1_condition"].agg(_stringify_list).reset_index(name="stim1_conditions")
    stim2_conditions = grouped["stim2_condition"].agg(_stringify_list).reset_index(name="stim2_conditions")
    plates = grouped["plate_id"].agg(_stringify_list).reset_index(name="plates")
    summary = summary.merge(date_bounds, on="sample_id", how="left")
    summary = summary.merge(cell_types, on="sample_id", how="left")
    summary = summary.merge(stim1_conditions, on="sample_id", how="left")
    summary = summary.merge(stim2_conditions, on="sample_id", how="left")
    summary = summary.merge(plates, on="sample_id", how="left")
    return summary.sort_values(["sample_id"]).reset_index(drop=True)


def build_sample_cell_type_bridge(well_df: pd.DataFrame) -> pd.DataFrame:
    return (
        well_df.loc[well_df["cell_type"] != "", ["sample_id", "cell_type"]]
        .drop_duplicates()
        .sort_values(["sample_id", "cell_type"])
        .reset_index(drop=True)
    )


def build_stim_condition_summary(well_df: pd.DataFrame) -> pd.DataFrame:
    return (
        well_df.groupby(["cell_type", "stim1_condition", "stim2_condition"], dropna=False)
        .agg(
            analyses=("analysis_instance_id", "count"),
            unique_samples=("sample_id", "nunique"),
            usable_analyses=("usable", "sum"),
            stim1_positive_analyses=("stim1_positive", "sum"),
            total_stim1_responders=("n_stim1_responders", "sum"),
        )
        .reset_index()
        .sort_values(["cell_type", "stim1_condition", "stim2_condition"])
        .reset_index(drop=True)
    )


def _sheet_to_object_level(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["well_id", "field", "object_no"])
    work = df.copy()
    well_col, field_col, object_col = _select_object_sheet_columns(work)
    work = _normalize_key_columns(work, well_col, field_col, object_col)
    renamed = {}
    for col in work.columns:
        if col in {"well_id", "field", "object_no"}:
            continue
        renamed[col] = _slug_col(col)
    work = work.rename(columns=renamed)
    return work


def _merge_object_level_tables(resp_df: pd.DataFrame, stats_df: pd.DataFrame) -> pd.DataFrame:
    left = _sheet_to_object_level(resp_df)
    right = _sheet_to_object_level(stats_df)
    join_cols = ["well_id", "field", "object_no"]
    if left.empty and right.empty:
        return pd.DataFrame(columns=join_cols)
    if left.empty:
        return right
    if right.empty:
        return left
    right_keep = [c for c in right.columns if c not in left.columns or c in join_cols]
    return left.merge(right[right_keep], on=join_cols, how="outer")


def _pick_analysis_workbook(row: pd.Series) -> str:
    for col in ("included_analysis_path", "with_obj_analysis_path", "analysis_path"):
        path = str(row.get(col, "")).strip()
        if path:
            return path
    return ""


def build_object_analysis_table(manifest: pd.DataFrame, object_assets: pd.DataFrame) -> pd.DataFrame:
    cache: dict[str, pd.DataFrame] = {}
    rows: list[pd.DataFrame] = []

    for _, row in manifest.iterrows():
        workbook_path = _pick_analysis_workbook(row)
        if not workbook_path:
            continue
        workbook_key = str(Path(workbook_path).expanduser())
        if workbook_key not in cache:
            path = Path(workbook_key)
            if not path.exists():
                cache[workbook_key] = pd.DataFrame()
            else:
                xls = pd.ExcelFile(path)
                resp_df = _read_sheet_or_empty(xls, "Responder_Object_Level")
                stats_df = _read_sheet_or_empty(xls, "Object_Stats")
                cache[workbook_key] = _merge_object_level_tables(resp_df, stats_df)
        obj_df = cache[workbook_key]
        if obj_df.empty or "well_id" not in obj_df.columns:
            continue

        well_sub = obj_df.loc[obj_df["well_id"] == row["well_id"]].copy()
        if well_sub.empty:
            continue

        well_sub["analysis_instance_id"] = row["analysis_instance_id"]
        well_sub["test_well"] = row["test_well"]
        well_sub["sample_id"] = row["sample_id"]
        well_sub["date_code"] = row["date_code"]
        well_sub["analysis_date"] = row["analysis_date"]
        well_sub["plate_id"] = row["plate_id"]
        well_sub["cell_type"] = row["cell_type"]
        well_sub["stim1_condition"] = row["stim1_condition"]
        well_sub["stim2_condition"] = row["stim2_condition"]
        well_sub["usable"] = row["usable"]
        well_sub["stim1_positive"] = row["stim1_positive"]
        well_sub["analysis_path"] = row["analysis_path"]
        well_sub["with_obj_analysis_path"] = row["with_obj_analysis_path"]
        well_sub["included_analysis_path"] = row["included_analysis_path"]
        rows.append(well_sub)

    if not rows:
        return pd.DataFrame(
            columns=[
                "object_instance_id",
                "analysis_instance_id",
                "sample_id",
                "date_code",
                "analysis_date",
                "plate_id",
                "well_id",
                "field",
                "object_no",
                "cell_type",
                "stim1_condition",
                "stim2_condition",
            ]
        )

    objects = pd.concat(rows, ignore_index=True)
    objects = objects.drop_duplicates(subset=["analysis_instance_id", "well_id", "field", "object_no"]).reset_index(drop=True)

    for col in ("is_general_responder", "is_stim2_responder", "is_stim1_responder", "iqr_flagged", "obj_iqr_flagged"):
        if col in objects.columns:
            objects[col] = _to_bool(objects[col])

    objects["field"] = pd.to_numeric(objects["field"], errors="coerce").astype("Int64")
    objects["object_no"] = pd.to_numeric(objects["object_no"], errors="coerce").astype("Int64")
    objects["object_instance_id"] = (
        objects["analysis_instance_id"].astype(str)
        + "|F"
        + objects["field"].astype("string").fillna("")
        + "|O"
        + objects["object_no"].astype("string").fillna("")
    )

    if not object_assets.empty:
        asset_work = object_assets.copy()
        join_cols = ["date_code", "well_id", "field", "object_no"]
        grouped = asset_work.groupby(join_cols + ["asset_type"], dropna=False)["asset_path"].agg(list).reset_index()
        csv_assets = grouped[grouped["asset_type"] == "object_trace_csv"][join_cols + ["asset_path"]].rename(
            columns={"asset_path": "object_trace_csv_paths"}
        )
        png_assets = grouped[grouped["asset_type"] == "object_trace_png"][join_cols + ["asset_path"]].rename(
            columns={"asset_path": "object_trace_png_paths"}
        )
        objects = objects.merge(csv_assets, on=join_cols, how="left")
        objects = objects.merge(png_assets, on=join_cols, how="left")
        objects["object_trace_csv_path"] = objects["object_trace_csv_paths"].apply(
            lambda x: _stringify_list(x) if isinstance(x, list) else ""
        )
        objects["object_trace_png_path"] = objects["object_trace_png_paths"].apply(
            lambda x: _stringify_list(x) if isinstance(x, list) else ""
        )
        objects = objects.drop(columns=["object_trace_csv_paths", "object_trace_png_paths"], errors="ignore")
    else:
        objects["object_trace_csv_path"] = ""
        objects["object_trace_png_path"] = ""

    lead_cols = [
        "object_instance_id",
        "analysis_instance_id",
        "test_well",
        "sample_id",
        "date_code",
        "analysis_date",
        "plate_id",
        "well_id",
        "field",
        "object_no",
        "cell_type",
        "stim1_condition",
        "stim2_condition",
        "usable",
        "stim1_positive",
        "object_trace_csv_path",
        "object_trace_png_path",
        "analysis_path",
        "with_obj_analysis_path",
        "included_analysis_path",
    ]
    other_cols = [c for c in objects.columns if c not in lead_cols]
    return objects[lead_cols + other_cols].sort_values(
        ["analysis_date", "date_code", "sample_id", "well_id", "field", "object_no"]
    ).reset_index(drop=True)


def build_object_flag_summary(object_df: pd.DataFrame) -> pd.DataFrame:
    if object_df.empty:
        return pd.DataFrame(
            columns=[
                "cell_type",
                "stim1_condition",
                "stim2_condition",
                "objects",
                "general_responders",
                "stim1_responders",
                "stim2_responders",
                "iqr_flagged_objects",
            ]
        )
    work = object_df.copy()
    if "obj_iqr_flagged" in work.columns and "iqr_flagged" not in work.columns:
        work = work.rename(columns={"obj_iqr_flagged": "iqr_flagged"})
    for col in ("is_general_responder", "is_stim1_responder", "is_stim2_responder", "iqr_flagged"):
        if col not in work.columns:
            work[col] = False
    return (
        work.groupby(["cell_type", "stim1_condition", "stim2_condition"], dropna=False)
        .agg(
            objects=("object_instance_id", "count"),
            general_responders=("is_general_responder", "sum"),
            stim1_responders=("is_stim1_responder", "sum"),
            stim2_responders=("is_stim2_responder", "sum"),
            iqr_flagged_objects=("iqr_flagged", "sum"),
        )
        .reset_index()
        .sort_values(["cell_type", "stim1_condition", "stim2_condition"])
        .reset_index(drop=True)
    )


def build_dax_measures() -> str:
    return """-- Power BI DAX measures for PT exports
Total Samples =
DISTINCTCOUNT('well_analysis'[sample_id])

Total Analyses =
COUNTROWS('well_analysis')

Total Objects =
COUNTROWS('object_analysis')

Selected Sample Analysis Count =
COUNTROWS('well_analysis')

Selected Sample Object Count =
COUNTROWS('object_analysis')

Selected Sample Cell Types =
CONCATENATEX(
    VALUES('well_analysis'[cell_type]),
    'well_analysis'[cell_type],
    ", "
)

Stim1 Positive Wells =
CALCULATE(
    COUNTROWS('well_analysis'),
    'well_analysis'[stim1_positive] = TRUE()
)

General Responder Objects =
CALCULATE(
    COUNTROWS('object_analysis'),
    'object_analysis'[is_general_responder] = TRUE()
)

Stim1 Responder Objects =
CALCULATE(
    COUNTROWS('object_analysis'),
    'object_analysis'[is_stim1_responder] = TRUE()
)

Stim2 Responder Objects =
CALCULATE(
    COUNTROWS('object_analysis'),
    'object_analysis'[is_stim2_responder] = TRUE()
)

Average Object Peak FF0 =
AVERAGE('object_analysis'[obj_peak_ff0_win])
"""


def build_dax_link_columns() -> str:
    return """-- Calculated columns for clickable file links
-- After creating these columns, set Data Category to Web URL in Power BI.

well_analysis[analysis_file_url] =
"file:///" & SUBSTITUTE('well_analysis'[analysis_path], "\\", "/")

well_analysis[included_analysis_file_url] =
"file:///" & SUBSTITUTE('well_analysis'[included_analysis_path], "\\", "/")

well_analysis[mean_plot_file_url] =
IF(
    'well_analysis'[candidate_mean_plot_paths] <> BLANK(),
    "file:///" & SUBSTITUTE('well_analysis'[candidate_mean_plot_paths], "\\", "/"),
    BLANK()
)

well_analysis[object_plot_file_url] =
IF(
    'well_analysis'[candidate_object_plot_paths] <> BLANK(),
    "file:///" & SUBSTITUTE('well_analysis'[candidate_object_plot_paths], "\\", "/"),
    BLANK()
)

object_analysis[trace_csv_file_url] =
IF(
    'object_analysis'[object_trace_csv_path] <> BLANK(),
    "file:///" & SUBSTITUTE('object_analysis'[object_trace_csv_path], "\\", "/"),
    BLANK()
)

object_analysis[trace_png_file_url] =
IF(
    'object_analysis'[object_trace_png_path] <> BLANK(),
    "file:///" & SUBSTITUTE('object_analysis'[object_trace_png_path], "\\", "/"),
    BLANK()
)
"""


def build_dax_readme() -> pd.DataFrame:
    rows = [
        {
            "topic": "Import",
            "notes": "Load the Excel workbook sheets into Power BI. Use well_analysis as the main fact table and object_analysis for object-level filtering.",
        },
        {
            "topic": "Relationships",
            "notes": "Relate sample_summary[sample_id] to well_analysis[sample_id] and object_analysis[sample_id] as one-to-many.",
        },
        {
            "topic": "Links",
            "notes": "Create the calculated columns from dax_link_columns.dax and set their Data Category to Web URL so file paths become clickable.",
        },
        {
            "topic": "Interactive filtering",
            "notes": "Use slicers on cell_type, stim1_condition, stim2_condition, is_general_responder, is_stim1_responder, is_stim2_responder, and iqr_flagged.",
        },
    ]
    return pd.DataFrame(rows)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def write_excel_workbook(tables: dict[str, pd.DataFrame], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name, df in tables.items():
            df.to_excel(writer, sheet_name=sheet_name[:31], index=False)


def write_dax_files(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dax_measures.dax").write_text(build_dax_measures(), encoding="utf-8")
    (output_dir / "dax_link_columns.dax").write_text(build_dax_link_columns(), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Power BI-friendly Excel/CSV tables from PT analyzed folders and sample manifest metadata."
    )
    parser.add_argument(
        "--manifest",
        help="Existing sample manifest workbook. If omitted, the manifest is rebuilt from the roots below.",
    )
    parser.add_argument(
        "--layout-root",
        help="Plate layout workbook directory. Required when --manifest is not supplied.",
    )
    parser.add_argument(
        "--supplemental-layout-root",
        help="Optional directory of supplemental plate-layout workbooks (used when --manifest is not supplied).",
    )
    parser.add_argument(
        "--analysis-root",
        required=True,
        help="Root containing analyzed date folders and plot outputs.",
    )
    parser.add_argument(
        "--output-dir",
        default="powerbi_export",
        help="Directory for generated outputs.",
    )
    parser.add_argument(
        "--excel-name",
        default="powerbi_export.xlsx",
        help="Workbook filename written inside --output-dir.",
    )
    parser.add_argument(
        "--write-csv",
        action="store_true",
        help="Also write each table as CSV in addition to the workbook.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    analysis_root = Path(args.analysis_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    workbook_path = output_dir / args.excel_name
    manifest_path = Path(args.manifest).expanduser().resolve() if args.manifest else None

    if manifest_path:
        manifest_raw = load_manifest_table(manifest_path)
        existing_manifest = manifest_path
    else:
        if not args.layout_root:
            raise SystemExit("--layout-root is required when --manifest is not supplied.")
        layout_root = Path(args.layout_root).expanduser().resolve()
        supplemental_layout_root = (
            Path(args.supplemental_layout_root).expanduser().resolve() if args.supplemental_layout_root else None
        )
        existing_manifest = output_dir / "sample_manifest.xlsx"
        manifest_raw = build_manifest_table_from_roots(
            layout_root=layout_root,
            supplemental_layout_root=supplemental_layout_root,
            analysis_root=analysis_root,
            existing_manifest=existing_manifest if existing_manifest.exists() else None,
        )

    manifest = normalize_manifest(manifest_raw)
    plot_index = scan_plot_index(analysis_root)
    object_asset_index = scan_object_asset_index(analysis_root)
    well_analysis = build_well_analysis_table(manifest, plot_index)
    object_analysis = build_object_analysis_table(manifest, object_asset_index)
    sample_summary = build_sample_summary(well_analysis)
    sample_cell_types = build_sample_cell_type_bridge(well_analysis)
    stim_summary = build_stim_condition_summary(well_analysis)
    object_flag_summary = build_object_flag_summary(object_analysis)
    dax_guide = build_dax_readme()

    tables = {
        "well_analysis": well_analysis,
        "object_analysis": object_analysis,
        "sample_summary": sample_summary,
        "sample_cell_types": sample_cell_types,
        "stim_condition_summary": stim_summary,
        "object_flag_summary": object_flag_summary,
        "plot_index": plot_index,
        "object_asset_index": object_asset_index,
        "dax_readme": dax_guide,
    }

    write_excel_workbook(tables, workbook_path)
    write_dax_files(output_dir)

    if args.write_csv:
        for name, df in tables.items():
            write_csv(df, output_dir / f"{name}.csv")

    print(f"Wrote Power BI export workbook to {workbook_path}")
    print(f"  well_analysis: {len(well_analysis)} rows")
    print(f"  object_analysis: {len(object_analysis)} rows")
    print(f"  sample_summary: {len(sample_summary)} rows")
    print(f"  sample_cell_types: {len(sample_cell_types)} rows")
    print(f"  stim_condition_summary: {len(stim_summary)} rows")
    print(f"  object_flag_summary: {len(object_flag_summary)} rows")
    print(f"  plot_index: {len(plot_index)} rows")
    print(f"  object_asset_index: {len(object_asset_index)} rows")
    print(f"DAX files: {output_dir / 'dax_measures.dax'}")
    print(f"           {output_dir / 'dax_link_columns.dax'}")


if __name__ == "__main__":
    main()
