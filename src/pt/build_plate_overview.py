#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


ROW_LETTERS = tuple("ABCDEFGH")
COL_NUMBERS = tuple(str(i) for i in range(1, 13))

THIN_BORDER = Border(
    left=Side(style="thin", color="999999"),
    right=Side(style="thin", color="999999"),
    top=Side(style="thin", color="999999"),
    bottom=Side(style="thin", color="999999"),
)

# Pastel fills assigned to cell types in sorted order of appearance (any labels work).
CELLTYPE_PALETTE = ["A7D8F0", "F6DDCF", "DDEBD3", "FDE9A9", "E3D5F5", "F9C6D9", "CDEDEA", "E0E0E0"]


def build_celltype_fills(records) -> Dict[str, PatternFill]:
    types = sorted({r.cell_type for r in records if r.cell_type})
    fills = {"": PatternFill(fill_type="solid", fgColor="FFFFFF")}
    for i, t in enumerate(types):
        fills[t] = PatternFill(fill_type="solid", fgColor=CELLTYPE_PALETTE[i % len(CELLTYPE_PALETTE)])
    return fills


@dataclass(frozen=True)
class GridSpec:
    header_row: int
    col_start: int
    row_label_col: int
    row_start: int


@dataclass(frozen=True)
class WellRecord:
    well: str
    row: str
    column: int
    experiment_date: str
    plate_number: Optional[int]
    compound_plate: Optional[int]
    sample_number: str
    display_id: str
    cell_type: str
    stim1: str
    stim2: str
    control_type: str


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"none", "nan", "na", "n/a"} else text


def normalize_sample_key(value: object) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    if re.fullmatch(r"[+-]?\d+(?:\.0+)?", text):
        return str(int(float(text)))
    return text


def infer_experiment_date(path: Path) -> str:
    m = re.search(r"PlateLayout_(\d{4})_(\d{2})_(\d{2})_", path.name, flags=re.IGNORECASE)
    if not m:
        raise ValueError(f"Could not infer experiment date from filename: {path.name}")
    yyyy, mm, dd = m.groups()
    return f"{yyyy}-{mm}-{dd}"


def infer_plate_number(path: Path) -> Optional[int]:
    m = re.search(r"_Plate(\d+)", path.stem, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def infer_compound_plate(path: Path) -> Optional[int]:
    m = re.search(r"_CP(\d+)", path.stem, flags=re.IGNORECASE)
    return int(m.group(1)) if m else None


def find_sheet_name(sheet_names: Iterable[str], candidates: Iterable[str]) -> Optional[str]:
    lowered = {name.lower(): name for name in sheet_names}
    for candidate in candidates:
        match = lowered.get(candidate.lower())
        if match:
            return match
    return None


def find_plate_grid(ws, row_label_offset: int = 1) -> GridSpec:
    max_row = min(ws.max_row, 200)
    max_col = min(ws.max_column, 80)
    for r in range(1, max_row + 1):
        for c in range(1, max_col - 11):
            values = [normalize_text(ws.cell(r, c + i).value) for i in range(12)]
            if values != list(COL_NUMBERS):
                continue
            row_label_col = c - row_label_offset
            if row_label_col < 1:
                continue
            row_values = [
                normalize_text(ws.cell(r + 1 + i, row_label_col).value).upper()
                for i in range(8)
            ]
            if row_values == list(ROW_LETTERS):
                return GridSpec(header_row=r, col_start=c, row_label_col=row_label_col, row_start=r + 1)
    raise ValueError(f"Could not locate 96-well grid in sheet '{ws.title}'")


def fill_down(values: list[str]) -> list[str]:
    out: list[str] = []
    last = ""
    for value in values:
        text = normalize_text(value)
        if text:
            last = text
        out.append(last)
    return out


def parse_layout_assignments(ws) -> Dict[str, str]:
    grid = find_plate_grid(ws, row_label_offset=1)
    out: Dict[str, str] = {}
    for row_idx, row_letter in enumerate(ROW_LETTERS, start=grid.row_start):
        for col_offset, col_label in enumerate(COL_NUMBERS):
            value = normalize_sample_key(ws.cell(row_idx, grid.col_start + col_offset).value)
            out[f"{row_letter}{col_label}"] = value
    return out


def locate_header_row(ws, required_names: Iterable[str]) -> int:
    required = {name.lower() for name in required_names}
    for r in range(1, min(ws.max_row, 30) + 1):
        row_values = {normalize_text(ws.cell(r, c).value).lower() for c in range(1, min(ws.max_column, 20) + 1)}
        if required.issubset(row_values):
            return r
    raise ValueError(f"Could not find header row in sheet '{ws.title}'")


def parse_sample_manifest(ws) -> Dict[str, str]:
    header_row = locate_header_row(ws, {"Number"})
    headers = {
        normalize_text(ws.cell(header_row, c).value).lower(): c
        for c in range(1, min(ws.max_column, 20) + 1)
        if normalize_text(ws.cell(header_row, c).value)
    }

    number_col = headers.get("number")
    barcode_col = headers.get("ih_sample_id") or headers.get("barcode (4 digit)") or headers.get("barcode")
    if not number_col or not barcode_col:
        raise ValueError(f"Could not identify sample number/barcode columns in '{ws.title}'")

    mapping: Dict[str, str] = {}
    for r in range(header_row + 1, ws.max_row + 1):
        number = normalize_sample_key(ws.cell(r, number_col).value)
        barcode_raw = ws.cell(r, barcode_col).value
        barcode = normalize_text(barcode_raw)
        if barcode and re.fullmatch(r"[+-]?\d+(?:\.0+)?", barcode):
            barcode = f"{int(float(barcode_raw)):04d}"
        if number and barcode:
            mapping[number] = barcode
    return mapping


def parse_paradigm_row_cell_types(ws) -> Dict[str, str]:
    grid = find_plate_grid(ws, row_label_offset=1)
    cell_type_col = grid.row_label_col - 1
    raw_values = [normalize_text(ws.cell(r, cell_type_col).value).upper() for r in range(grid.row_start, grid.row_start + 8)]
    filled = fill_down(raw_values)
    return {row_letter: cell_type for row_letter, cell_type in zip(ROW_LETTERS, filled)}


def parse_paradigm_controls(ws) -> Dict[str, str]:
    grid = find_plate_grid(ws, row_label_offset=1)
    controls: Dict[str, str] = {}
    for row_idx, row_letter in enumerate(ROW_LETTERS, start=grid.row_start):
        for col_offset, col_label in enumerate(COL_NUMBERS):
            value = normalize_text(ws.cell(row_idx, grid.col_start + col_offset).value)
            if value:
                controls[f"{row_letter}{col_label}"] = value
    return controls


def parse_treatment_definitions(ws) -> Dict[int, Dict[str, str]]:
    header_row = None
    first_col = None
    second_col = None
    for r in range(1, min(ws.max_row, 120) + 1):
        headers = [normalize_text(ws.cell(r, c).value).lower() for c in range(1, min(ws.max_column, 20) + 1)]
        if "1st" in headers and "2nd" in headers:
            header_row = r
            first_col = headers.index("1st") + 1
            second_col = headers.index("2nd") + 1
            break
    if header_row is None or first_col is None or second_col is None:
        raise ValueError(f"Could not locate treatment definition table in '{ws.title}'")

    definitions: Dict[int, Dict[str, str]] = {}
    for r in range(header_row + 1, min(ws.max_row, header_row + 20) + 1):
        cond_id = None
        for c in range(1, min(ws.max_column, 8) + 1):
            text = normalize_text(ws.cell(r, c).value)
            if re.fullmatch(r"\d+", text):
                cond_id = int(text)
                break
        if cond_id is None:
            if definitions:
                break
            continue
        definitions[cond_id] = {
            "stim1": normalize_text(ws.cell(r, first_col).value),
            "stim2": normalize_text(ws.cell(r, second_col).value),
        }
    if not definitions:
        raise ValueError(f"No treatment definitions found in '{ws.title}'")
    return definitions


def build_row_treatment_map(row_to_cell_type: Dict[str, str], definitions: Dict[int, Dict[str, str]]) -> Dict[str, int]:
    row_order = list(ROW_LETTERS)
    mapping: Dict[str, int] = {}
    i = 0
    while i < len(row_order):
        row_letter = row_order[i]
        cell_type = row_to_cell_type.get(row_letter, "")
        j = i
        while j < len(row_order) and row_to_cell_type.get(row_order[j], "") == cell_type:
            j += 1
        for idx, grouped_row in enumerate(row_order[i:j], start=1):
            if idx in definitions:
                mapping[grouped_row] = idx
        i = j
    return mapping


def build_well_records(path: Path) -> list[WellRecord]:
    wb = load_workbook(path)

    layout_sheet = find_sheet_name(wb.sheetnames, ["Layout"])
    manifest_sheet = find_sheet_name(wb.sheetnames, ["Sample_Manifest", "Sample_Manifest2"])
    paradigm_sheet = find_sheet_name(wb.sheetnames, ["Paradigm"])
    if not layout_sheet or not manifest_sheet or not paradigm_sheet:
        raise ValueError("Workbook must contain Layout, Sample_Manifest/Sample_Manifest2, and Paradigm sheets")

    layout_assignments = parse_layout_assignments(wb[layout_sheet])
    sample_map = parse_sample_manifest(wb[manifest_sheet])
    row_to_cell_type = parse_paradigm_row_cell_types(wb[paradigm_sheet])
    control_overrides = parse_paradigm_controls(wb[paradigm_sheet])
    definitions = parse_treatment_definitions(wb[paradigm_sheet])
    row_to_treatment = build_row_treatment_map(row_to_cell_type, definitions)

    experiment_date = infer_experiment_date(path)
    plate_number = infer_plate_number(path)
    compound_plate = infer_compound_plate(path)

    out: list[WellRecord] = []
    for row_letter in ROW_LETTERS:
        treatment_id = row_to_treatment.get(row_letter)
        stim1 = definitions.get(treatment_id, {}).get("stim1", "")
        stim2 = definitions.get(treatment_id, {}).get("stim2", "")
        cell_type = row_to_cell_type.get(row_letter, "")

        for col_label in COL_NUMBERS:
            well = f"{row_letter}{col_label}"
            sample_number = layout_assignments.get(well, "")
            control_type = control_overrides.get(well, "")

            if sample_number and sample_number in sample_map:
                display_id = sample_map[sample_number]
            elif control_type:
                display_id = control_type
            else:
                display_id = sample_number

            out.append(
                WellRecord(
                    well=well,
                    row=row_letter,
                    column=int(col_label),
                    experiment_date=experiment_date,
                    plate_number=plate_number,
                    compound_plate=compound_plate,
                    sample_number=sample_number,
                    display_id=display_id,
                    cell_type=cell_type,
                    stim1=stim1,
                    stim2=stim2,
                    control_type=control_type,
                )
            )
    return out


def write_overview_data(ws, records: list[WellRecord]) -> None:
    headers = [
        "well",
        "row",
        "column",
        "experiment_date",
        "plate_number",
        "compound_plate",
        "sample_number",
        "display_id",
        "cell_type",
        "stim1",
        "stim2",
        "control_type",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.border = THIN_BORDER

    for record in records:
        ws.append(
            [
                record.well,
                record.row,
                record.column,
                record.experiment_date,
                record.plate_number,
                record.compound_plate,
                record.sample_number,
                record.display_id,
                record.cell_type,
                record.stim1,
                record.stim2,
                record.control_type,
            ]
        )

    for col_idx in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = 18


def write_overview_sheet(ws, records: list[WellRecord], source_path: Path) -> None:
    fills = build_celltype_fills(records)
    record_map = {record.well: record for record in records}

    ws["A1"] = "Plate Overview"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Source file: {source_path.name}"
    ws["A3"] = f"Experiment date: {records[0].experiment_date if records else ''}"
    ws["D2"] = f"Plate: {records[0].plate_number if records else ''}"
    ws["D3"] = f"Compound plate: {records[0].compound_plate if records else ''}"

    start_row = 5
    start_col = 2

    for offset, col_label in enumerate(COL_NUMBERS):
        cell = ws.cell(start_row, start_col + offset, col_label)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER

    for offset, row_letter in enumerate(ROW_LETTERS, start=1):
        cell = ws.cell(start_row + offset, start_col - 1, row_letter)
        cell.font = Font(bold=True, size=18)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER

        for col_offset, col_label in enumerate(COL_NUMBERS):
            well = f"{row_letter}{col_label}"
            record = record_map[well]
            lines = [record.display_id, record.cell_type]
            if record.stim1:
                lines.append(f"Stim1: {record.stim1}")
            if record.stim2:
                lines.append(f"Stim2: {record.stim2}")

            cell = ws.cell(start_row + offset, start_col + col_offset, "\n".join([line for line in lines if line]))
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = THIN_BORDER
            cell.fill = fills.get(record.cell_type, fills[""])
            if record.control_type:
                cell.font = Font(bold=True, color="8A5A00")
            else:
                cell.font = Font(bold=True)

    ws.freeze_panes = "B6"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 4
    for col in range(start_col, start_col + 12):
        ws.column_dimensions[get_column_letter(col)].width = 18
    for row in range(start_row + 1, start_row + 9):
        ws.row_dimensions[row].height = 72


def ensure_fresh_sheet(wb, sheet_name: str):
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    return wb.create_sheet(sheet_name)


def build_output_path(path: Path, output: Optional[Path]) -> Path:
    if output:
        return output
    return path.with_name(f"{path.stem}_with_overview{path.suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a color-coded 96-well overview sheet from a plate layout workbook."
    )
    parser.add_argument("xlsx", type=Path, help="Path to the source plate layout workbook")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output workbook path. Defaults to '<input>_with_overview.xlsx'",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.xlsx.expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)

    records = build_well_records(source)
    output = build_output_path(source, args.output)

    if source != output:
        shutil.copy2(source, output)

    wb = load_workbook(output)
    write_overview_sheet(ensure_fresh_sheet(wb, "Overview"), records, source)
    write_overview_data(ensure_fresh_sheet(wb, "Overview_Data"), records)
    wb.save(output)

    print(f"Wrote overview workbook: {output}")


if __name__ == "__main__":
    main()
