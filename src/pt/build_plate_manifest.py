#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter


GRID_MIN_COL = 3   # C
GRID_MAX_COL = 14  # N
GRID_MIN_ROW = 14
GRID_MAX_ROW = 21

WELL_ROW_COL = 2  # B
WELL_COL_ROW = 13  # header row with 1..12

MIN_GENERAL_RESPONDERS_FOR_SCORABLE_WELL = 10
MIN_STIM1_RESPONDERS_FOR_POSITIVE_WELL = 3

# Controlled vocabulary for cell-type labels. Empty = accept any non-empty label verbatim.
# Populated from --cell-types (comma-separated, case-insensitive).
VALID_CELL_TYPES: set = set()
# Protocol version label written to every manifest row (set with --protocol-version).
PROTOCOL_VERSION = "1"

ROW_LETTERS = tuple("ABCDEFGH")
COL_NUMBERS = tuple(str(i) for i in range(1, 13))
_SKIP_SHEET_PATTERN = re.compile(
    r"summary|stats|overview|instructions|log|filtered|paradigm|sample_manifest",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LayoutRecord:
    layout_file: str
    sheet_name: str
    date: str
    plate_num: Optional[int]
    sample_id: int
    well_id: str
    protocol_version: str
    plate_comments: str
    well_comments: str
    well_cell_type: str
    well_condition_1: str
    well_condition_2: str
    review_comments: str


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "na", "n/a"} else text


def strip_labeled_value(value: object, labels: Optional[List[str]] = None) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if not labels:
        return text

    for label in labels:
        if not label:
            continue
        pattern = rf"^\s*{re.escape(label)}\s*[:#=\-]*\s*"
        stripped = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
        if stripped != text:
            return stripped
    return text


def _valid_mmddyy(code: str) -> bool:
    try:
        datetime.strptime(code, "%m%d%y")
        return True
    except ValueError:
        return False


def extract_date_from_name(name: str) -> str:
    """Return DATE as MMDDYY from a filename or folder name.

    Accepts YYYY_MM_DD / YYYY-MM-DD / YYYYMMDD, then six-digit MMDDYY,
    then six-digit YYMMDD (converted to MMDDYY).
    """
    for code in _date_candidates_from_name(name):
        if _valid_mmddyy(code):
            return code
    for code in _date_candidates_from_name(name):
        try:
            dt = datetime.strptime(code, "%y%m%d")
            return dt.strftime("%m%d%y")
        except ValueError:
            continue
    return "0"


def extract_date_from_path(path: Path) -> str:
    """Prefer a date in the filename, then walk parent folder names."""
    from_name = extract_date_from_name(path.name)
    if from_name != "0":
        return from_name
    for part in reversed(path.parts[:-1]):
        from_part = extract_date_from_name(part)
        if from_part != "0":
            return from_part
    return "0"


def normalize_cell_type(value: str) -> str:
    val = normalize_text(value).upper()
    if not val:
        return "Null"
    if VALID_CELL_TYPES and val not in VALID_CELL_TYPES:
        return "Null"
    return val


def normalize_condition(value: str) -> str:
    """Light-touch normalization of a stim condition label.

    Trims whitespace, collapses runs of spaces and standardizes ' + ' as the separator
    between components (e.g. 'CompoundA+ CompoundB' -> 'CompoundA + CompoundB').
    """
    text = normalize_text(value)
    if not text:
        return ""
    text = re.sub(r"\s*\+\s*", " + ", text)
    text = re.sub(r"\s+", " ", text).strip(" +-,;:")
    return text


def infer_protocol_version(date_code: str) -> str:
    """Return the protocol version label for a plate (constant; set with --protocol-version)."""
    return PROTOCOL_VERSION


def extract_plate_num(sheet_name: str, file_name: str) -> Optional[int]:
    for source in (sheet_name, file_name):
        m = re.search(r"plate[ _-]?(\d+)", source, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
        m = re.search(r"sheet[ _-]?(\d+)", source, flags=re.IGNORECASE)
        if m:
            return int(m.group(1))
    m = re.match(r"^layout\s*#?\s*(\d+)$", sheet_name.strip(), re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _layout_sheet_plate_num(sheet_title: str, file_name: str) -> Optional[int]:
    if sheet_title.strip().lower() == "layout":
        return extract_plate_num(sheet_title, file_name) or 1
    m = re.match(r"^layout\s*#?\s*(\d+)$", sheet_title.strip(), re.IGNORECASE)
    if m:
        return int(m.group(1))
    if re.match(r"Plate\s*#?\d+", sheet_title, re.IGNORECASE):
        return extract_plate_num(sheet_title, file_name)
    return extract_plate_num(sheet_title, file_name)


def _as_grid_label(value: object) -> str:
    text = normalize_text(value)
    if re.fullmatch(r"\d+(?:\.0+)?", text):
        return str(int(float(text)))
    return text.upper()


def locate_well_grid(ws) -> Optional[Tuple[int, int, int, int]]:
    """Find the 96-well header row and A–H labels.

    Returns (header_row, row_label_col, col_start, row_start) or None.
    Prefers the historical C13 / B14 grid, then scans the sheet.
    """
    def matches_at(header_row: int, col_start: int, row_label_col: int) -> bool:
        headers = [_as_grid_label(ws.cell(header_row, col_start + i).value) for i in range(12)]
        if headers != list(COL_NUMBERS):
            return False
        rows = [_as_grid_label(ws.cell(header_row + 1 + i, row_label_col).value) for i in range(8)]
        return rows == list(ROW_LETTERS)

    if matches_at(WELL_COL_ROW, GRID_MIN_COL, WELL_ROW_COL):
        return WELL_COL_ROW, WELL_ROW_COL, GRID_MIN_COL, GRID_MIN_ROW

    max_row = min(getattr(ws, "max_row", 80) or 80, 80)
    max_col = min(getattr(ws, "max_column", 40) or 40, 40)
    for r in range(1, max_row + 1):
        for c in range(2, max(3, max_col - 10)):
            if matches_at(r, c, c - 1):
                return r, c - 1, c, r + 1
    return None


def _select_plate_worksheets(wb):
    plate_ws = [
        ws for ws in wb.worksheets
        if re.match(r"Plate\s*#?\d+", ws.title, re.IGNORECASE)
    ]
    if plate_ws:
        return plate_ws

    layout_ws = []
    for ws in wb.worksheets:
        if _SKIP_SHEET_PATTERN.search(ws.title):
            continue
        title = ws.title.strip()
        if title.lower() == "layout" or re.match(r"^layout\s*#?\s*\d+$", title, re.IGNORECASE):
            layout_ws.append(ws)
    if layout_ws:
        return layout_ws

    return [ws for ws in wb.worksheets if not _SKIP_SHEET_PATTERN.search(ws.title)]


def parse_layout_cell(value: str) -> Tuple[int, str, str, str, str]:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return 0, "", "", "", ""

    first_line = lines[0].strip()
    sample_id = 0
    line_start = 1
    inferred_cell_type = ""
    comments: List[str] = []
    if re.fullmatch(r"[+-]?\d+(\.\d+)?", first_line):
        sample_id = int(float(first_line))
    else:
        # Non-numeric sample ID becomes 0.
        # If the next line is a known cell type, prefer that as cell type and
        # keep the first line as comment/context (e.g., "Ringer's").
        if len(lines) > 1 and VALID_CELL_TYPES and normalize_cell_type(lines[1]) in VALID_CELL_TYPES:
            inferred_cell_type = lines[1].strip()
            comments.append(first_line)
            line_start = 2
        else:
            # Fallback behavior when no explicit next-line cell type is available.
            inferred_cell_type = first_line

    cell_type = inferred_cell_type
    condition_1 = ""
    condition_2 = ""

    for line in lines[line_start:]:
        if ":" in line:
            key, raw_val = line.split(":", 1)
            key = key.strip().lower()
            val = raw_val.strip().replace("'", "")
            if key == "stim1":
                condition_1 = val
            elif key == "stim2":
                condition_2 = val
            else:
                comments.append(f"{key}: {val}")
        elif not cell_type:
            cell_type = line
        else:
            comments.append(line)

    return (
        sample_id,
        normalize_cell_type(cell_type),
        normalize_condition(condition_1),
        normalize_condition(condition_2),
        "; ".join(comments),
    )


def well_sort_key(well: str) -> Tuple[int, int]:
    m = re.match(r"^([A-Za-z]+)(\d+)$", well)
    if not m:
        return (999, 999)
    row = m.group(1).upper()
    col = int(m.group(2))
    row_num = 0
    for ch in row:
        row_num = row_num * 26 + (ord(ch) - ord("A") + 1)
    return (row_num, col)


_FORMULA_TOKEN_RE = re.compile(
    r"^(?:(?:'([^']+)'|([A-Za-z_][A-Za-z0-9_. ]*))!)?\$?([A-Za-z]{1,3})\$?(\d+)$"
)


def _split_formula_concat(expr: str) -> List[str]:
    tokens: List[str] = []
    current = ""
    in_quotes = False
    for ch in expr:
        if ch == '"':
            in_quotes = not in_quotes
            current += ch
        elif ch == "&" and not in_quotes:
            tokens.append(current)
            current = ""
        else:
            current += ch
    tokens.append(current)
    return tokens


def resolve_concat_formula(formula: object, wb, default_sheet: str) -> Optional[str]:
    """Best-effort evaluator for '=A1&CHAR(10)&"lit"&Sheet!B2'-style formulas.

    Layout workbooks from mid-Feb 2026 onward build each well's text by
    concatenating cross-sheet cell references instead of typing the value
    directly. These files are saved without Excel recalculating them, so
    openpyxl's data_only read returns None for every well cell. This walks
    the formula text and re-derives the same string from the referenced
    cells so those workbooks aren't silently dropped.
    """
    if not isinstance(formula, str) or not formula.startswith("="):
        return None

    parts: List[str] = []
    for raw_token in _split_formula_concat(formula[1:]):
        token = raw_token.strip()
        if not token:
            continue
        if token.upper() == "CHAR(10)":
            parts.append("\n")
            continue
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            parts.append(token[1:-1])
            continue
        m = _FORMULA_TOKEN_RE.match(token)
        if not m:
            return None
        sheet_name = m.group(1) or m.group(2) or default_sheet
        cell_ref = f"{m.group(3)}{m.group(4)}"
        if sheet_name not in wb.sheetnames:
            return None
        parts.append(normalize_text(wb[sheet_name][cell_ref].value))
    return "".join(parts)


def build_merged_anchor_map(ws) -> Dict[str, str]:
    anchor_map: Dict[str, str] = {}
    for merged in ws.merged_cells.ranges:
        for row in range(merged.min_row, merged.max_row + 1):
            for col in range(merged.min_col, merged.max_col + 1):
                coord = f"{get_column_letter(col)}{row}"
                anchor = f"{get_column_letter(merged.min_col)}{merged.min_row}"
                anchor_map[coord] = anchor
    return anchor_map


def get_layout_metadata(wb) -> Dict[str, str]:
    ws = wb["Layout"] if "Layout" in wb.sheetnames else wb.worksheets[0]

    cell_plate = strip_labeled_value(
        ws["H2"].value,
        labels=["Cell Plate #", "Cell Plate Number", "Cell Plate"],
    )
    compound_plate = strip_labeled_value(
        ws["H3"].value,
        labels=["Compound Plate #", "Compound Plate Number", "Compound Plate"],
    )
    plate_comments = strip_labeled_value(
        ws["D4"].value,
        labels=["Plate Comments", "Comments"],
    )

    culture_n2 = strip_labeled_value(
        ws["N2"].value,
        labels=["Culture Preparation Date", "Culter Preperation Date", "Preparation Date", "Culture Date"],
    )
    culture_s7 = strip_labeled_value(
        ws["S7"].value,
        labels=["Culture Preparation Date", "Culter Preperation Date", "Preparation Date", "Culture Date"],
    )
    culture_values = [v for v in [culture_n2, culture_s7] if v]
    culture_preparation_date = " | ".join(dict.fromkeys(culture_values))

    return {
        "cell_plate_num": cell_plate,
        "compound_plate_num": compound_plate,
        "plate_comments": plate_comments,
        "culture_preparation_date": culture_preparation_date,
    }


def _date_candidates_from_name(name: str) -> List[str]:
    candidates: List[str] = []

    # Match YYYY_MM_DD, YYYY-MM-DD, or compact YYYYMMDD and emit MMDDYY then YYMMDD.
    m_long = re.search(r"(?<!\d)((?:19|20)\d{2})[_-]?(\d{2})[_-]?(\d{2})(?!\d)", name)
    if m_long:
        yyyy, mm, dd = m_long.groups()
        yy = yyyy[2:]
        candidates.append(f"{mm}{dd}{yy}")  # MMDDYY
        candidates.append(f"{yy}{mm}{dd}")  # YYMMDD

    # Compact MMDDYYYY (e.g. 06302026).
    m_8 = re.search(r"(?<!\d)(\d{2})(\d{2})((?:19|20)\d{2})(?!\d)", name)
    if m_8:
        mm, dd, yyyy = m_8.groups()
        yy = yyyy[2:]
        candidates.append(f"{mm}{dd}{yy}")
        candidates.append(f"{yy}{mm}{dd}")

    # Match direct six-digit codes (MMDDYY or YYMMDD).
    for m_short in re.finditer(r"(?<!\d)(\d{6})(?!\d)", name):
        candidates.append(m_short.group(1))

    # Preserve order but de-dup.
    return list(dict.fromkeys(candidates))


def _supplemental_plate_num_from_name(name: str) -> Optional[int]:
    stem = Path(name).stem

    # Primary pattern for names like:
    # PlateLayout_2025_10_06_Plate1(...)
    # PlateLayout_2025_09_24_Plate1_Exp2 and Exp3
    # PlateLayout_2025_11_07_Plate1_CP18
    # Use non-letter boundary (not \b) so "_Plate1_Exp2" is matched.
    m = re.search(r"(?<![A-Za-z])plate\s*[_-]?(\d+)", stem, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))

    # Fallback for compact variants like *_1exp2*
    m = re.search(r"[_-](\d+)\s*exp\b", stem, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))

    # Fallback for compact variants like *_1cp18*
    m = re.search(r"[_-](\d+)\s*cp\d+\b", stem, flags=re.IGNORECASE)
    if m:
        return int(m.group(1))

    return None


def build_supplemental_layout_index(supplemental_root: Optional[Path]) -> Dict[Tuple[str, int], Dict[str, str]]:
    index: Dict[Tuple[str, int], Dict[str, str]] = {}
    if supplemental_root is None:
        return index
    print(f"[INFO] Looking for metadata in: {supplemental_root}")
    if not supplemental_root.exists():
        print(f"[WARNING] Metadata directory does not exist: {supplemental_root}")
        return index

    for path in sorted(supplemental_root.rglob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        # Match expected supplemental naming styles such as:
        # PlateLayout_2025_10_06_Plate1(10-'6').xlsx
        # PlateLayout_2025_09_24_Plate1_Exp2 and Exp3.xlsx
        # PlateLayout_2025_11_07_Plate1_CP18.xlsx
        date_candidates = _date_candidates_from_name(path.name)
        canonical = extract_date_from_path(path)
        if canonical != "0":
            date_candidates = list(dict.fromkeys([canonical, *date_candidates]))
        if not date_candidates:
            continue
        plate_num = _supplemental_plate_num_from_name(path.name)
        if plate_num is None:
            continue
        try:
            wb = load_workbook(path, data_only=True)
        except Exception:
            continue
        metadata = get_layout_metadata(wb)
        metadata["supplemental_layout_file"] = str(path)
        for date_code in date_candidates:
            index.setdefault((date_code, plate_num), metadata)
    return index


def parse_layout_workbook(path: Path) -> List[LayoutRecord]:
    wb = load_workbook(path, data_only=True)
    wb_formulas = load_workbook(path, data_only=False)
    out: List[LayoutRecord] = []
    file_date = extract_date_from_path(path)
    protocol_version = infer_protocol_version(file_date)

    for ws in _select_plate_worksheets(wb):
        grid = locate_well_grid(ws)
        if grid is None:
            continue
        header_row, row_label_col, col_start, row_start = grid
        plate_num = _layout_sheet_plate_num(ws.title, path.name)

        anchor_map = build_merged_anchor_map(ws)
        ws_formulas = wb_formulas[ws.title] if ws.title in wb_formulas.sheetnames else None
        plate_comments = normalize_text(ws["F4"].value) or normalize_text(ws["D4"].value)

        for r in range(row_start, row_start + 8):
            row_label = _as_grid_label(ws.cell(r, row_label_col).value)
            if not row_label:
                continue

            for c in range(col_start, col_start + 12):
                col_label = _as_grid_label(ws.cell(header_row, c).value)
                if not col_label:
                    continue
                well_id = f"{row_label}{col_label}"

                coord = f"{get_column_letter(c)}{r}"
                anchor = anchor_map.get(coord, coord)
                raw_value = normalize_text(ws[anchor].value)
                if not raw_value and ws_formulas is not None:
                    resolved = resolve_concat_formula(ws_formulas[anchor].value, wb, ws.title)
                    raw_value = normalize_text(resolved)
                if not raw_value:
                    continue

                sample_id, cell_type, condition_1, condition_2, comments = parse_layout_cell(raw_value)

                out.append(
                    LayoutRecord(
                        layout_file=str(path),
                        sheet_name=ws.title,
                        date=file_date,
                        plate_num=plate_num,
                        sample_id=sample_id,
                        well_id=well_id,
                        protocol_version=protocol_version,
                        plate_comments=plate_comments,
                        well_comments=normalize_text(comments),
                        well_cell_type=normalize_text(cell_type),
                        well_condition_1=normalize_text(condition_1),
                        well_condition_2=normalize_text(condition_2),
                        review_comments="",
                    )
                )
    return out


def load_analysis_rows(path: Path) -> pd.DataFrame:
    try:
        xls = pd.ExcelFile(path)
    except Exception:
        return pd.DataFrame()
    sheet_name = next((s for s in xls.sheet_names if str(s).strip().lower() == "responder_well_level"), None)
    if not sheet_name:
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name=sheet_name)
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df

    need = ["Well", "n_objects", "general_responders", "stim1_responders"]
    for col in need:
        if col not in df.columns:
            df[col] = pd.NA
    out = df[need].copy()
    out["Well"] = out["Well"].astype(str).str.strip().str.upper()
    return out


# Per-object/ROI export folders can contain thousands of unrelated .xlsx files;
# summary files always sit directly under a date's Analysis folder, never inside
# these, so pruning them keeps the analysis-root walk fast on large trees.
_SKIP_DIR_PREFIX_RE = re.compile(r"^Experiment_", re.IGNORECASE)


def _iter_xlsx_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _SKIP_DIR_PREFIX_RE.match(d)]
        for name in filenames:
            if name.lower().endswith(".xlsx") and not name.startswith("~$"):
                yield Path(dirpath) / name


def build_analysis_index(
    analysis_roots: List[Path], kind: str
) -> Tuple[Dict[Tuple[str, int], List[Path]], Dict[str, List[Path]]]:
    by_plate: Dict[Tuple[str, int], List[Path]] = {}
    by_date: Dict[str, List[Path]] = {}

    # Define alternative tokens for 'kind'.
    # If kind is 'filtered', also accept 'included'.
    aliases = {"filtered": "included", "included": "filtered"}
    kinds = [kind]
    if kind in aliases:
        kinds.append(aliases[kind])

    patterns = []
    for k in kinds:
        # Match variations: <MMDDYY>_<kind>_summary_plate<n> or <MMDDYY>_<kind>_summary
        # Allow optional extra text after 'summary' or 'plate<n>', but ensure it ends with .xlsx
        # We use [_-]? to be flexible with separators.
        plate_pattern = re.compile(rf"(\d{{6}})[_-]{re.escape(k)}[_-]summary[_-]plate(\d+).*\.xlsx$", re.IGNORECASE)
        date_pattern = re.compile(rf"(\d{{6}})[_-]{re.escape(k)}[_-]summary.*\.xlsx$", re.IGNORECASE)
        patterns.append((plate_pattern, date_pattern))

    # Every root's match is kept as a candidate, in root order; build_manifest
    # picks the first candidate that actually has usable data, so a stale/empty
    # file in an earlier root doesn't shadow a good one in a later root.
    for analysis_root in analysis_roots:
        print(f"[INFO] Looking for {kind} analysis data in: {analysis_root}")
        for path in _iter_xlsx_files(analysis_root):
            name = path.name
            for plate_pattern, date_pattern in patterns:
                m_plate = plate_pattern.search(name)
                if m_plate:
                    key = (m_plate.group(1), int(m_plate.group(2)))
                    by_plate.setdefault(key, []).append(path)
                    break
                m_date = date_pattern.search(name)
                if m_date:
                    by_date.setdefault(m_date.group(1), []).append(path)
                    break

    return by_plate, by_date


def as_number(value: object) -> int:
    if pd.isna(value):
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def _make_test_well_key(date: object, plate_id: object, sample_id: object, well_id: object) -> str:
    return f"{date}|{plate_id}|{sample_id}|{well_id}"


def parse_mmddyy_date(value: object):
    text = normalize_text(value)
    if not re.fullmatch(r"\d{6}", text):
        return pd.NaT
    try:
        return datetime.strptime(text, "%m%d%y")
    except ValueError:
        return pd.NaT


def numeric_sample_id_for_excel(value: object) -> int:
    text = normalize_text(value).lstrip("'")
    if not text:
        return 0
    try:
        return int(float(text))
    except Exception:
        return 0


def load_existing_test_well_ids(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        prev = pd.read_excel(
            path,
            sheet_name="Manifest",
            usecols=["DATE", "PLATE ID", "SAMPLE ID", "WELL ID", "TEST WELL"],
        )
    except Exception:
        return {}
    if prev.empty:
        return {}

    prev["TEST WELL"] = pd.to_numeric(prev["TEST WELL"], errors="coerce")
    prev = prev.dropna(subset=["TEST WELL"])
    if prev.empty:
        return {}

    mapping: Dict[str, int] = {}
    for _, r in prev.iterrows():
        key = _make_test_well_key(r["DATE"], r["PLATE ID"], r["SAMPLE ID"], r["WELL ID"])
        mapping.setdefault(key, int(r["TEST WELL"]))
    return mapping


def assign_test_well_ids(manifest: pd.DataFrame, existing_map: Optional[Dict[str, int]] = None) -> pd.DataFrame:
    if manifest.empty:
        return manifest

    existing_map = dict(existing_map or {})
    manifest = manifest.copy()
    manifest["_TEST_WELL_KEY"] = manifest.apply(
        lambda r: _make_test_well_key(r["DATE"], r["PLATE ID"], r["SAMPLE ID"], r["WELL ID"]),
        axis=1,
    )

    missing = (
        manifest.loc[
            ~manifest["_TEST_WELL_KEY"].isin(existing_map.keys()),
            ["DATE", "PLATE ID", "SAMPLE ID", "WELL ID", "_TEST_WELL_KEY"],
        ]
        .drop_duplicates()
        .sort_values(["DATE", "PLATE ID", "SAMPLE ID", "WELL ID"])
    )
    next_id = (max(existing_map.values()) + 1) if existing_map else 1
    for key in missing["_TEST_WELL_KEY"]:
        existing_map[key] = next_id
        next_id += 1

    manifest["TEST WELL"] = manifest["_TEST_WELL_KEY"].map(existing_map).astype(int)
    return manifest.drop(columns=["_TEST_WELL_KEY"])


def apply_high_stim1_plate_qc(manifest: pd.DataFrame, threshold: float = 80.0) -> pd.DataFrame:
    if manifest.empty:
        return manifest

    plate_avg = (
        manifest.groupby(["DATE", "PLATE ID"], dropna=False)["Calculate: % respond to STIM 1"]
        .mean()
        .reset_index(name="plate_avg_pct_stim1_response")
    )
    flagged = plate_avg.loc[plate_avg["plate_avg_pct_stim1_response"] >= threshold, ["DATE", "PLATE ID"]]

    if flagged.empty:
        manifest["_plate_filtered_high_stim1"] = False
        return manifest

    manifest = manifest.merge(
        flagged.assign(_plate_filtered_high_stim1=True),
        on=["DATE", "PLATE ID"],
        how="left",
    )
    manifest["_plate_filtered_high_stim1"] = manifest["_plate_filtered_high_stim1"].eq(True)

    note = "abnormally high stim1 response"
    high_rows = manifest["_plate_filtered_high_stim1"]
    existing_comments = manifest.loc[high_rows, "PLATE COMMENTS"].fillna("").astype(str).str.strip()
    has_note = existing_comments.str.contains(re.escape(note), case=False, regex=True)
    needs_note_idx = existing_comments[~has_note].index
    manifest.loc[needs_note_idx, "PLATE COMMENTS"] = existing_comments[~has_note].map(
        lambda c: note if not c else f"{c}; {note}"
    )

    return manifest


def normalize_cell_type_comment_swap(manifest: pd.DataFrame) -> pd.DataFrame:
    if manifest.empty:
        return manifest
    out = manifest.copy()
    valid_types = VALID_CELL_TYPES
    comment_upper = out["WELL COMMENTS"].fillna("").astype(str).str.strip().str.upper()
    cell_upper = out["WELL CELL TYPE"].fillna("").astype(str).str.strip().str.upper()
    mask = comment_upper.isin(valid_types) & ((cell_upper == "") | (cell_upper == "NULL"))
    out.loc[mask, "WELL CELL TYPE"] = comment_upper[mask]
    out.loc[mask, "WELL COMMENTS"] = ""
    return out


def build_manifest(
    layout_root: Path, analysis_roots: List[Path], supplemental_layout_root: Optional[Path] = None
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_layouts: List[LayoutRecord] = []
    print(f"[INFO] Looking for plate layouts in: {layout_root}")
    layout_paths = sorted(
        p for p in layout_root.rglob("*.xlsx") if not p.name.startswith("~$")
    )
    print(f"[INFO] Found {len(layout_paths)} layout workbook(s)")
    dates_found = sorted(
        {extract_date_from_path(p) for p in layout_paths} - {"0"},
        key=lambda d: datetime.strptime(d, "%m%d%y"),
    )
    print(f"[INFO] Dates parsed from layout files: {', '.join(dates_found) if dates_found else '(none)'}")
    for path in layout_paths:
        all_layouts.extend(parse_layout_workbook(path))

    if not all_layouts:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    with_obj_by_plate, with_obj_by_date = build_analysis_index(analysis_roots, "with_obj")
    filtered_by_plate, filtered_by_date = build_analysis_index(analysis_roots, "filtered")
    supplemental_index = build_supplemental_layout_index(supplemental_layout_root)
    analysis_cache: Dict[Path, pd.DataFrame] = {}

    def get_df(path: Optional[Path]) -> pd.DataFrame:
        if not path:
            return pd.DataFrame()
        if path not in analysis_cache:
            analysis_cache[path] = load_analysis_rows(path)
        return analysis_cache[path]

    def resolve_analysis_path(candidates: Optional[List[Path]]) -> Optional[Path]:
        """Pick the first candidate (in root order) whose relevant sheet has data.

        Falls back to the first candidate if none have data, so status reporting
        still reflects a real file (e.g. sheet_missing_or_empty) instead of
        looking like the file was never found at all.
        """
        if not candidates:
            return None
        for path in candidates:
            if not get_df(path).empty:
                return path
        return candidates[0]

    records: List[Dict[str, object]] = []
    unmatched_rows: List[Dict[str, object]] = []
    path_toc_rows: List[Dict[str, object]] = []
    for item in all_layouts:
        supplemental_meta = (
            supplemental_index.get((item.date, item.plate_num), {})
            if item.plate_num is not None
            else {}
        )
        row: Dict[str, object] = {
            "SAMPLE ID": item.sample_id,
            "TEST WELL": "",
            "DATE": item.date,
            "Cell Plate #": supplemental_meta.get("cell_plate_num", ""),
            "Compound Plate #": supplemental_meta.get("compound_plate_num", ""),
            "Culter Preperation Date": supplemental_meta.get("culture_preparation_date", ""),
            "PROTOCOL VERSION NUMBER": item.protocol_version,
            "PLATE ID": (f"Plate {item.plate_num}" if item.plate_num is not None else item.sheet_name),
            "PLATE COMMENTS": item.plate_comments,
            "WELL ID": item.well_id,
            "WELL COMMENTS": item.well_comments,
            "WELL CELL TYPE": item.well_cell_type,
            "WELL CONDITION": item.well_condition_1,
            "WELL CONDITION 2": item.well_condition_2,
            "Number of total ROIs": 0,
            "Number of scorable ROIs": 0,
            "Number of general responders": 0,
            "Number of ROIs responding to STIM 1": 0,
            "Calculate: Meets criteria to use for data?": False,
            "Calculate: % respond to STIM 1": 0.0,
            "Calculate: Meets criteria for positive response?": False,
            "Review comments": item.review_comments,
            "ANALYSIS PATH": "",
            "WITH_OBJ ANALYSIS PATH": "",
            "INCLUDED ANALYSIS PATH": "",
        }

        with_obj_candidates = with_obj_by_plate.get((item.date, item.plate_num)) or with_obj_by_date.get(item.date)
        with_obj_path = resolve_analysis_path(with_obj_candidates)
        with_obj_status = "ok" if with_obj_path else "missing_file"

        filtered_candidates = filtered_by_plate.get((item.date, item.plate_num)) or filtered_by_date.get(item.date)
        filtered_path = resolve_analysis_path(filtered_candidates)
        filtered_status = "ok" if filtered_path else "missing_file"
        row["WITH_OBJ ANALYSIS PATH"] = str(with_obj_path) if with_obj_path else ""
        row["INCLUDED ANALYSIS PATH"] = str(filtered_path) if filtered_path else ""
        row["ANALYSIS PATH"] = row["INCLUDED ANALYSIS PATH"] or row["WITH_OBJ ANALYSIS PATH"]
        path_toc_rows.append(
            {
                "LAYOUT FILE PATH": item.layout_file,
                "SUPPLEMENTAL LAYOUT FILE PATH": supplemental_meta.get("supplemental_layout_file", ""),
                "WITH_OBJ ANALYSIS PATH": row["WITH_OBJ ANALYSIS PATH"],
                "INCLUDED ANALYSIS PATH": row["INCLUDED ANALYSIS PATH"],
            }
        )

        df_with_obj = get_df(with_obj_path)
        with_obj_well_found = False
        if not df_with_obj.empty:
            match_total = df_with_obj[df_with_obj["Well"] == item.well_id.upper()]
            if not match_total.empty:
                row["Number of total ROIs"] = as_number(match_total.iloc[0].get("n_objects"))
                with_obj_well_found = True
            else:
                with_obj_status = "well_not_found"
        elif with_obj_status == "ok":
            with_obj_status = "sheet_missing_or_empty"

        df_filtered = get_df(filtered_path)
        filtered_well_found = False
        if not df_filtered.empty:
            match_filtered = df_filtered[df_filtered["Well"] == item.well_id.upper()]
            if not match_filtered.empty:
                ar = match_filtered.iloc[0]
                scorable = as_number(ar.get("n_objects"))
                general_resp = as_number(ar.get("general_responders"))
                stim1_resp = as_number(ar.get("stim1_responders"))
                row["Number of scorable ROIs"] = scorable
                row["Number of general responders"] = general_resp
                row["Number of ROIs responding to STIM 1"] = stim1_resp
                row["Calculate: Meets criteria to use for data?"] = (
                    general_resp >= MIN_GENERAL_RESPONDERS_FOR_SCORABLE_WELL
                )
                row["Calculate: % respond to STIM 1"] = (stim1_resp / scorable * 100.0) if scorable > 0 else 0.0
                filtered_well_found = True
            else:
                filtered_status = "well_not_found"
        elif filtered_status == "ok":
            filtered_status = "sheet_missing_or_empty"

        records.append(row)
        if with_obj_status != "ok" or filtered_status != "ok":
            unmatched_rows.append(
                {
                    "LAYOUT_FILE": item.layout_file,
                    "DATE": item.date,
                    "PLATE ID": (f"Plate {item.plate_num}" if item.plate_num is not None else item.sheet_name),
                    "PLATE NUM": item.plate_num if item.plate_num is not None else "",
                    "SAMPLE ID": item.sample_id,
                    "WELL ID": item.well_id,
                    "WITH_OBJ_FILE": str(with_obj_path) if with_obj_path else "",
                    "WITH_OBJ_STATUS": with_obj_status,
                    "INCLUDED_FILE": str(filtered_path) if filtered_path else "",
                    "INCLUDED_STATUS": filtered_status,
                    "HAS_WITH_OBJ_WELL_MATCH": with_obj_well_found,
                    "HAS_INCLUDED_WELL_MATCH": filtered_well_found,
                }
            )

    manifest = pd.DataFrame.from_records(records)
    manifest = normalize_cell_type_comment_swap(manifest)

    # Plate-level QC and comments update for abnormally high STIM 1 response.
    manifest = apply_high_stim1_plate_qc(manifest, threshold=80.0)

    manifest["Calculate: Meets criteria for positive response?"] = (
        manifest["Calculate: Meets criteria to use for data?"].astype(bool)
        & (
            pd.to_numeric(manifest["Number of ROIs responding to STIM 1"], errors="coerce").fillna(0)
            > MIN_STIM1_RESPONDERS_FOR_POSITIVE_WELL
        )
    )
    manifest["Well Locator"] = (
        manifest["DATE"].astype(str)
        + "-"
        + manifest["PLATE ID"].astype(str)
        + "-"
        + manifest["WELL ID"].astype(str)
        + "-"
        + manifest["Compound Plate #"]
    )
    manifest["Usable"] = manifest["Calculate: Meets criteria to use for data?"].astype(bool).astype(int)
    manifest["Stim1 Positive"] = manifest["Calculate: Meets criteria for positive response?"].astype(bool).astype(int)

    col_order = [
        "SAMPLE ID",
        "TEST WELL",
        "DATE",
        "Cell Plate #",
        "Compound Plate #",
        "Culter Preperation Date",
        "PROTOCOL VERSION NUMBER",
        "PLATE ID",
        "WELL ID",
        "Well Locator",
        "PLATE COMMENTS",
        "ANALYSIS PATH",
        "WITH_OBJ ANALYSIS PATH",
        "INCLUDED ANALYSIS PATH",
        "WELL COMMENTS",
        "WELL CELL TYPE",
        "WELL CONDITION",
        "WELL CONDITION 2",
        "Number of total ROIs",
        "Number of scorable ROIs",
        "Number of general responders",
        "Number of ROIs responding to STIM 1",
        "Calculate: Meets criteria to use for data?",
        "Usable",
        "Calculate: % respond to STIM 1",
        "Calculate: Meets criteria for positive response?",
        "Stim1 Positive",
        "Review comments",
    ]
    manifest = manifest[col_order].copy()
    manifest = manifest.sort_values(["DATE", "PLATE ID", "SAMPLE ID", "WELL ID"]).reset_index(drop=True)

    unmatched = pd.DataFrame(unmatched_rows)
    if not unmatched.empty:
        unmatched = unmatched.drop_duplicates().sort_values(
            ["DATE", "PLATE ID", "SAMPLE ID", "WELL ID"]
        ).reset_index(drop=True)
    path_toc = pd.DataFrame(path_toc_rows)
    if not path_toc.empty:
        path_toc = path_toc.drop_duplicates().sort_values(
            ["LAYOUT FILE PATH", "SUPPLEMENTAL LAYOUT FILE PATH", "WITH_OBJ ANALYSIS PATH", "INCLUDED ANALYSIS PATH"]
        ).reset_index(drop=True)
    return manifest, unmatched, path_toc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build consolidated sample manifest from plate layouts + analysis.")
    parser.add_argument(
        "--layout-root",
        required=True,
        help="Directory containing plate layout .xlsx files.",
    )
    parser.add_argument(
        "--supplemental-layout-root",
        default=None,
        help="Optional directory of supplemental plate layout .xlsx files (cell plate #, compound plate #, "
        "culture preparation date). Skipped when omitted.",
    )
    parser.add_argument(
        "--analysis-root",
        nargs="+",
        required=True,
        help="One or more root directories containing pipeline output (.xlsx summaries). "
        "Searched in order; a (date, plate) match found in an earlier root wins over "
        "the same match in a later one.",
    )
    parser.add_argument(
        "--cell-types",
        default="",
        help="Optional comma-separated controlled vocabulary for cell-type labels (e.g. 'iPSC,HEK'). "
        "Labels not in the list become Null. Default: accept any label.",
    )
    parser.add_argument(
        "--protocol-version",
        default="1",
        help="Protocol version label written to every manifest row (default: 1).",
    )
    parser.add_argument(
        "--output",
        default="sample_manifest.xlsx",
        help="Output .xlsx path.",
    )
    return parser.parse_args()


def build_data_dictionary() -> pd.DataFrame:
    rows: List[Dict[str, str]] = []

    manifest_defs = [
        ("SAMPLE ID", "Sample identifier", "If the well is a control well it's Sample ID is assigned as 0"),
        ("TEST WELL", "Persistent numeric key for each manifest row.", "Existing values are reused from prior output when DATE+PLATE ID+SAMPLE ID+WELL ID match; new rows get next available integer."),
        ("DATE", "Date extracted from the layout file name or parent folder.", "Written as Excel date type with format mmddyy. Accepts YYYY_MM_DD, YYYYMMDD, MMDDYY, and YYMMDD; invalid/unparsed values are blank."),
        ("Cell Plate #", "Cell plate identifier from Layout!H2.", "Label text is stripped when embedded in the cell. Matched by DATE + PLATE ID parsed from supplemental layout filename."),
        ("Compound Plate #", "Compound plate identifier from Layout!H3.", "Label text is stripped when embedded in the cell. Matched by DATE + PLATE ID parsed from supplemental layout filename."),
        ("Culter Preperation Date", "Culture preparation date from Layout!N2 and Layout!S7.", "Label text is stripped; non-empty values are combined. Matched by DATE + PLATE ID parsed from supplemental layout filename."),
        ("PROTOCOL VERSION NUMBER", "Protocol version label.", "Set with --protocol-version (default 1)."),
        ("PLATE ID", "Plate label derived from detected plate number or worksheet name.", ""),
        ("WELL ID", "Well coordinate built from row/column labels (e.g., A1, H12).", ""),
        ("Well Locator", "Unique row locator built as DATE-PLATE ID-WELL ID.", ""),
        ("PLATE COMMENTS", "Plate-level notes from layout sheet cell Q4.", "For DATE+PLATE ID with average % respond to STIM 1 >= 80, appends: abnormally high stim1 response."),
        ("ANALYSIS PATH", "Primary analysis file path used for this row.", "Uses included analysis path when available; otherwise with_obj analysis path."),
        ("WITH_OBJ ANALYSIS PATH", "Resolved with_obj analysis file path used for lookup.", ""),
        ("INCLUDED ANALYSIS PATH", "Resolved included analysis file path used for lookup.", ""),
        ("WELL COMMENTS", "Free-text comments parsed from layout cell lines not mapped to stim1/stim2.", ""),
        ("WELL CELL TYPE", "Cell type parsed from the layout cell.", "Restricted to --cell-types when given; otherwise any label is kept. Empty values become Null."),
        ("WELL CONDITION", "Stim 1 condition parsed from the layout cell (key stim1).", "Whitespace and '+' separators are standardized."),
        ("WELL CONDITION 2", "Stim 2 condition parsed from the layout cell (key stim2).", "Whitespace and '+' separators are standardized."),
        ("Number of total ROIs", "n_objects from matching with_obj responder summary row.", "Default 0 when file/sheet/well match is missing."),
        ("Number of scorable ROIs", "n_objects from matching included responder summary row.", "Default 0 when file/sheet/well match is missing."),
        ("Number of general responders", "general_responders from matching included responder summary row.", "Default 0 when file/sheet/well match is missing."),
        ("Number of ROIs responding to STIM 1", "stim1_responders from matching included responder summary row.", "Default 0 when file/sheet/well match is missing."),
        ("Calculate: Meets criteria to use for data?", "True when Number of general responders >= 10.", "Base threshold: general_responders >= 10."),
        ("Usable", "Integer flag for Calculate: Meets criteria to use for data?", "1=True, 0=False."),
        ("Calculate: % respond to STIM 1", "100 * (Number of ROIs responding to STIM 1 / Number of scorable ROIs), else 0.", ""),
        (
            "Calculate: Meets criteria for positive response?",
            "Well-level positive call.",
            "True when Calculate: Meets criteria to use for data? is True and Number of ROIs responding to STIM 1 > 3.",
        ),
        ("Stim1 Positive", "Integer flag for Calculate: Meets criteria for positive response?.", "1=True, 0=False."),
        ("Review comments", "Reserved field for manual reviewer notes.", ""),
    ]
    # Add explicit calc note for percentage field.
    manifest_defs = [
        (c, d, ("0 if Number of scorable ROIs <= 0." if c == "Calculate: % respond to STIM 1" else n))
        for c, d, n in manifest_defs
    ]
    for col, desc, note in manifest_defs:
        rows.append({"Sheet": "Manifest", "Column": col, "Definition": desc, "Calculation Notes": note})

    unmatched_defs = [
        ("LAYOUT_FILE", "Plate layout workbook path that produced the unmatched row.", ""),
        ("DATE", "Date code linked to the affected layout row.", ""),
        ("PLATE ID", "Plate label linked to the affected layout row.", ""),
        ("PLATE NUM", "Parsed numeric plate index, if available.", ""),
        ("SAMPLE ID", "Sample identifier linked to the affected layout row.", ""),
        ("WELL ID", "Well coordinate linked to the affected layout row.", ""),
        ("WITH_OBJ_FILE", "Resolved with_obj summary file path used for lookup.", ""),
        ("WITH_OBJ_STATUS", "with_obj lookup status (ok, missing_file, sheet_missing_or_empty, well_not_found).", ""),
        ("INCLUDED_FILE", "Resolved included summary file path used for lookup.", ""),
        ("INCLUDED_STATUS", "included lookup status (ok, missing_file, sheet_missing_or_empty, well_not_found).", ""),
        ("HAS_WITH_OBJ_WELL_MATCH", "True if a with_obj row for this well was found.", ""),
        ("HAS_INCLUDED_WELL_MATCH", "True if an included row for this well was found.", ""),
    ]
    for col, desc, note in unmatched_defs:
        rows.append({"Sheet": "Unmatched", "Column": col, "Definition": desc, "Calculation Notes": note})

    path_toc_defs = [
        ("LAYOUT FILE PATH", "Path to the primary plate layout workbook used for manifest rows.", ""),
        ("SUPPLEMENTAL LAYOUT FILE PATH", "Path to the matched supplemental plate layout workbook used for supplemental metadata columns.", ""),
        ("WITH_OBJ ANALYSIS PATH", "Resolved with_obj analysis workbook path used for ROI total lookup.", ""),
        ("INCLUDED ANALYSIS PATH", "Resolved included analysis workbook path used for responder/scorable lookup.", ""),
    ]
    for col, desc, note in path_toc_defs:
        rows.append({"Sheet": "Path TOC", "Column": col, "Definition": desc, "Calculation Notes": note})

    return pd.DataFrame(rows, columns=["Sheet", "Column", "Definition", "Calculation Notes"])


def main() -> None:
    global VALID_CELL_TYPES, PROTOCOL_VERSION
    args = parse_args()
    VALID_CELL_TYPES = {t.strip().upper() for t in str(args.cell_types).split(",") if t.strip()}
    PROTOCOL_VERSION = str(args.protocol_version)
    layout_root = Path(args.layout_root)
    supplemental_layout_root = Path(args.supplemental_layout_root) if args.supplemental_layout_root else None
    analysis_roots = [Path(p) for p in args.analysis_root]
    output = Path(args.output)
    print(f"[INFO] Running build_plate_manifest from {Path(__file__).resolve()}")

    manifest, unmatched, path_toc = build_manifest(layout_root, analysis_roots, supplemental_layout_root)
    if manifest.empty:
        print("No manifest rows were produced.")
        return

    existing_test_well_map = load_existing_test_well_ids(output)
    manifest = assign_test_well_ids(manifest, existing_test_well_map)

    dictionary = build_data_dictionary()
    manifest_out = manifest.copy()
    manifest_out["DATE"] = manifest_out["DATE"].map(parse_mmddyy_date)
    manifest_out["SAMPLE ID"] = manifest_out["SAMPLE ID"].map(numeric_sample_id_for_excel)
    unmatched_out = unmatched.copy()
    if not unmatched_out.empty and "DATE" in unmatched_out.columns:
        unmatched_out["DATE"] = unmatched_out["DATE"].map(parse_mmddyy_date)
    if not unmatched_out.empty and "SAMPLE ID" in unmatched_out.columns:
        unmatched_out["SAMPLE ID"] = unmatched_out["SAMPLE ID"].map(numeric_sample_id_for_excel)

    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        manifest_out.to_excel(writer, index=False, sheet_name="Manifest")
        if not unmatched_out.empty:
            unmatched_out.to_excel(writer, index=False, sheet_name="Unmatched")
        if not path_toc.empty:
            path_toc.to_excel(writer, index=False, sheet_name="Path TOC")
        dictionary.to_excel(writer, index=False, sheet_name="Data Dictionary")

        # Force DATE columns to render as mmddyy Excel date type.
        ws_manifest = writer.sheets.get("Manifest")
        if ws_manifest is not None:
            date_col = manifest_out.columns.get_loc("DATE") + 1
            for r in range(2, len(manifest_out) + 2):
                ws_manifest.cell(row=r, column=date_col).number_format = "mmddyy"
            sample_id_col = manifest_out.columns.get_loc("SAMPLE ID") + 1
            for r in range(2, len(manifest_out) + 2):
                ws_manifest.cell(row=r, column=sample_id_col).number_format = "0000"
        if not unmatched_out.empty:
            ws_unmatched = writer.sheets.get("Unmatched")
            if ws_unmatched is not None and "DATE" in unmatched_out.columns:
                date_col = unmatched_out.columns.get_loc("DATE") + 1
                for r in range(2, len(unmatched_out) + 2):
                    ws_unmatched.cell(row=r, column=date_col).number_format = "mmddyy"
            if ws_unmatched is not None and "SAMPLE ID" in unmatched_out.columns:
                sample_id_col = unmatched_out.columns.get_loc("SAMPLE ID") + 1
                for r in range(2, len(unmatched_out) + 2):
                    ws_unmatched.cell(row=r, column=sample_id_col).number_format = "0000"
    print(f"Wrote {len(manifest)} rows to {output}")


if __name__ == "__main__":
    main()
