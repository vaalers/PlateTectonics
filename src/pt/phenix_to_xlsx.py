# phenix_to_xlsx.py
# -*- coding: utf-8 -*-
import argparse, fnmatch, os, re, shutil, string, sys, tempfile
from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd
from pt.pt_utils import sniff_delim
PLATE_FILE   = "PlateResults.txt"
OBJECTS_GLOB = "Objects_Population*.txt"   # Harmony: "Objects_Population - <population>.txt"

CAN_TIME      = "Time [s]"
CAN_NUMOBJS   = "Number of Objects"
CAN_MEANWELL  = "Intensity Mean per Well"

def pick_engine():
    if importlib.util.find_spec("openpyxl"):   return "openpyxl"
    if importlib.util.find_spec("xlsxwriter"): return "xlsxwriter"
    return None

# ---------- robust table reader ----------
ENCODINGS = ["utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be"]
DELIMS    = ["\t", ",", ";"]


def find_header_after_data_tag(lines, delim):
    # header is the line immediately following a line equal to "[Data]"
    for i, ln in enumerate(lines[:9]):
        if ln.strip().strip('"') == "[Data]":
            return i
    # fallback: first line containing both Row and Column tokens
    for i, ln in enumerate(lines[:9]):
        cols = [c.strip() for c in ln.split(delim)]
        if "Row" in cols and "Column" in cols:
            return i
    return 0

def normalize_headers(cols):
    out = []
    for c in cols:
        c = c.replace("¬µ", "µ")           # Opera’s odd micro symbol variant
        c = re.sub(r"\s+", " ", c).strip() # collapse spaces
        out.append(c)
    return out

def read_table_robust(path: Path) -> pd.DataFrame:
    last_err = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc, errors="replace", newline="") as f:
                lines = f.read().splitlines()
            delim  = sniff_delim(lines)
            header = find_header_after_data_tag(lines, delim)
            df = pd.read_csv(
                path,
                sep=delim,
                engine="python",
                encoding=enc,
                header=header,
                dtype=str,
                keep_default_na=False,
                on_bad_lines="skip",
            )
            df.columns = normalize_headers([str(c) for c in df.columns])
            return df
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Could not parse {path.name}: {last_err}")

# ---------- helpers ----------
def find_col(df: pd.DataFrame, pattern: str) -> str:
    cands = [c for c in df.columns if re.search(pattern, c, flags=re.I)]
    if not cands:
        raise KeyError(f"Missing column matching /{pattern}/. Available headers: {list(df.columns)[:25]} ...")
    return cands[0]

def row_to_letter(val) -> str:
    try:
        i = int(val)
        return string.ascii_uppercase[i-1]
    except Exception:
        return str(val)

def first_header_like(cols, *patterns):
    for pat in patterns:
        for c in cols:
            if re.search(pat, c, flags=re.I): return c
    return None

# ---------- main work ----------
def process_one_plate(plate_dir: Path, out_path: Path, agg: str = "median") -> str:
    plate_path   = plate_dir / PLATE_FILE
    objects_path = next(iter(sorted(plate_dir.glob(OBJECTS_GLOB))), None)
    if not plate_path.is_file() or objects_path is None:
        raise FileNotFoundError(f"Required files not found in {plate_dir}")

    pr = read_table_robust(plate_path)
    ob = read_table_robust(objects_path)

    # Coerce numeric keys
    for df in (pr, ob):
        for k in ["Row","Column","Plane","Timepoint","Field","Object No","X","Y"]:
            if k in df.columns:
                df[k] = pd.to_numeric(df[k], errors="coerce")

    # Flexible PlateResults columns
    col_time     = find_col(pr, r'^(Time\b|Time\s*\[.*s.*\])')
    col_numobjs  = find_col(pr, r'Number\s*of\s*Objects')
    col_meanwell = find_col(pr, r'Intensity.*Mean\s*-\s*Mean\s*per\s*Well')

    pr_keep = pr[["Row","Column","Plane","Timepoint", col_time, col_numobjs, col_meanwell]].copy()
    pr_keep = pr_keep.rename(columns={col_time: CAN_TIME, col_numobjs: CAN_NUMOBJS, col_meanwell: CAN_MEANWELL})

    # Merge object-level with per-well metrics
    merged = ob.merge(pr_keep, on=["Row","Column","Plane","Timepoint"], how="left", validate="m:1")

    for c in ["Timepoint","Field","Object No","X","Y"]:
        if c in merged.columns:
            merged[c] = pd.to_numeric(merged[c], errors="ignore")
    merged = merged.sort_values(["Row","Column","Timepoint","Field","Object No"], kind="stable")

    merged["_Well"] = merged.apply(
        lambda r: f"{row_to_letter(r['Row'])}{int(r['Column']) if pd.notna(r['Column']) else r['Column']}", axis=1
    )

    # Column order (tolerant to µ variants)
    posx = first_header_like(merged.columns, r'^Position\s*X', r'X\s*\[\s*µ?m\s*\]')
    posy = first_header_like(merged.columns, r'^Position\s*Y', r'Y\s*\[\s*µ?m\s*\]')
    desired = ["Row","Column","Plane","Timepoint","Field","Object No","X","Y","Bounding Box"]
    if posx: desired.append(posx)
    if posy: desired.append(posy)
    desired += [CAN_TIME, CAN_NUMOBJS, CAN_MEANWELL]
    final_cols = [c for c in desired if c in merged.columns] + [c for c in merged.columns if c not in desired and c != "_Well"]

    # Plate_Overview aggregation
    agg_map = {"median": np.nanmedian, "mean": np.nanmean, "sum": np.nansum, "max": np.nanmax, "min": np.nanmin}
    if agg not in agg_map: agg = "median"
    n_rows = int(pd.to_numeric(pr.get("Row"), errors="coerce").max() or 8)
    n_cols = int(pd.to_numeric(pr.get("Column"), errors="coerce").max() or 12)
    n_rows, n_cols = max(n_rows,8), max(n_cols,12)

    pr_num = pr_keep.copy()
    pr_num["Row"]    = pd.to_numeric(pr_num["Row"], errors="coerce")
    pr_num["Column"] = pd.to_numeric(pr_num["Column"], errors="coerce")
    pr_num["val"]    = pd.to_numeric(pr_num[CAN_NUMOBJS], errors="coerce")

    grouped = pr_num.dropna(subset=["Row","Column"]).groupby(["Row","Column"])["val"]
    if   agg == "median": well_summary = grouped.median().reset_index()
    elif agg == "mean":   well_summary = grouped.mean().reset_index()
    elif agg == "sum":    well_summary = grouped.sum().reset_index()
    elif agg == "max":    well_summary = grouped.max().reset_index()
    else:                 well_summary = grouped.min().reset_index()

    index_letters = [string.ascii_uppercase[i] for i in range(n_rows)]
    overview = pd.DataFrame(index=index_letters, columns=[str(i) for i in range(1, n_cols+1)], dtype="float")
    for _, r in well_summary.iterrows():
        rowL, colS = row_to_letter(r["Row"]), str(int(r["Column"]))
        if rowL in overview.index and colS in overview.columns:
            overview.loc[rowL, colS] = r["val"]
    overview = overview.map(lambda x: int(x) if pd.notna(x) else np.nan)

    # Write atomically
    out_path.parent.mkdir(parents=True, exist_ok=True)
    engine = pick_engine()
    if engine is None:
        raise RuntimeError("No Excel writer found. Install one:  python3 -m pip install openpyxl xlsxwriter")
    tmp = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name)
    try:
        with pd.ExcelWriter(tmp, engine=engine) as xlw:
            overview.to_excel(xlw, sheet_name="Plate_Overview")
            for well, dfw in merged.groupby("_Well", sort=True):
                sheet = (well or "Unknown")[:31]
                dfw[final_cols].to_excel(xlw, sheet_name=sheet, index=False)
        shutil.move(str(tmp), str(out_path))
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return f"OK: {out_path}"

def find_plate_dirs(root: Path):
    for dirpath, _, filenames in os.walk(root):
        if PLATE_FILE in filenames and any(fnmatch.fnmatch(f, OBJECTS_GLOB) for f in filenames):
            yield Path(dirpath)

def main():
    ap = argparse.ArgumentParser(description="Opera Phenix → per-well Excel workbook")
    ap.add_argument("paths", nargs="*", help="Plate folder(s) that contain the two TSVs")
    ap.add_argument("--batch", metavar="ROOT", help="Process ALL subfolders under ROOT that contain the two TSVs")
    ap.add_argument("--agg", choices=["median","mean","sum","max","min"], default="median",
                    help="Aggregation for Plate_Overview across timepoints (default: median)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    ap.add_argument("--out", help="Output xlsx path (only valid with exactly one input folder)")
    args = ap.parse_args()

    targets = []
    if args.batch:
        targets.extend(sorted(find_plate_dirs(Path(args.batch))))
    if args.paths:
        targets.extend([Path(p) for p in args.paths])

    if not targets:
        ap.error("Provide at least one plate folder or --batch ROOT")

    single_out = Path(args.out) if args.out else None
    if single_out and len(targets) != 1:
        ap.error("--out is only allowed with exactly one input folder")

    errors = 0
    for t in targets:
        plate_dir = Path(t).resolve()
        base_name = plate_dir.name.replace(" ", "_") + "_OperaPhenix_per-well.xlsx"
        out_path  = single_out if single_out else (plate_dir / base_name)
        if out_path.exists() and not args.overwrite:
            print(f"SKIP (exists): {out_path}")
            continue
        try:
            print(process_one_plate(plate_dir, out_path, agg=args.agg))
        except Exception as e:
            errors += 1
            print(f"ERROR in {plate_dir}: {e}", file=sys.stderr)

    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
