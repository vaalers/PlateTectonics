#!/usr/bin/env python3
# phenix_reorg.py
# Rename Opera Phenix experiment folders, map ROI GUIDs via indexfile.txt,
# and split ANY image directory (recursively) into per-well folders by filename.

import argparse, re, shutil, sys
from pathlib import Path
from urllib.parse import urlparse
import pandas as pd

# ---------- patterns ----------
EXPRX = re.compile(
    r"""^
    (?P<prefix>.+?)__                                # anything, then __
    (?P<stamp>\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2})   # ISO-like timestamp
    -Measurement(?:\s*\d+)?$                         # trailing measurement marker
    """,
    re.X,
)

GUIDRX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
URL_GUIDRX = re.compile(r"/C/([0-9a-f\-]{36})/", re.I)

# Filenames like: R5C11_F1T2P1_<Population>_Cell.tiff
FILE_NAME_RX = re.compile(
    r"""^
        R(?P<row>\d+)
        C(?P<col>\d+)
        _F(?P<field>\d+)
        T(?P<timepoint>\d+)
        P(?P<plane>\d+)
        _.*\.(?:tif|tiff)$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# well-sibling folder names, e.g. "..._ROI_Images_B11"
WELL_SIBLING_RX = re.compile(r".*_[A-H][1-9]\d?$", re.I)
# well subdir names like "B11"
WELL_SUBDIR_RX = re.compile(r"^[A-H][1-9]\d?$", re.I)

RC_PAIR_RX   = re.compile(r"(?i)r\s*0*(\d{1,2}).*?c\s*0*(\d{1,2})")
R_ONLY_RX    = re.compile(r"(?i)r\s*0*(\d{1,2})")
C_ONLY_RX    = re.compile(r"(?i)c\s*0*(\d{1,2})")
WELL_CODE_RX = re.compile(r"(?<![A-Za-z])([A-Ha-h])\s*0*([1-9]\d?)")

def parse_row_col_from_name(name: str) -> tuple[int,int] | None:
    """
    Robust row/col parser:
      - r..c.. anywhere (lower/upper, underscores/hyphens ignored)
      - or well code like A01/A1
    """
    m = RC_PAIR_RX.search(name)
    if m:
        return int(m.group(1)), int(m.group(2))
    mr, mc = R_ONLY_RX.search(name), C_ONLY_RX.search(name)
    if mr and mc:
        return int(mr.group(1)), int(mc.group(1))
    mw = WELL_CODE_RX.search(name)
    if mw:
        row_letter = mw.group(1).upper()
        col = int(mw.group(2))
        row_num = ord(row_letter) - ord("A") + 1
        return row_num, col
    return None


# ---------- helpers ----------
def consolidate_roi_siblings(date_dir: Path, apply: bool, overwrite: bool, log: list[str]):
    well_rx = re.compile(r"_[A-H][1-9]\d?$", re.I)
    for base in (date_dir / "Analysis").glob("Experiment_*_ROI_Images"):
        if not base.is_dir(): 
            continue
        parent = base.parent
        for sib in sorted(parent.glob(base.name + "_*")):
            if not sib.is_dir(): 
                continue
            m = well_rx.search(sib.name)
            if not m:
                continue
            well = m.group(0)[1:]  # "B11"
            target_dir = base / well
            for f in sib.rglob("*"):
                if f.is_file():
                    dst = target_dir / f.name
                    ensure_move_file(f, dst, apply, overwrite, log)
            if apply:
                shutil.rmtree(sib)
                log.append(f"RMDIR: {sib}")

def row_to_letter(row):
    try:
        r = int(row)
    except Exception:
        return str(row)
    if r < 1:
        r = 1
    return chr(ord("A") + (r - 1))


def sniff_tab(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", engine="python", dtype=str, keep_default_na=False)


def find_date_dirs(root: Path, dates: list[str], all_dates: bool) -> list[Path]:
    if all_dates:
        return sorted([p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{6}", p.name)])
    want = set(dates)
    out = []
    for p in root.iterdir():
        if p.is_dir() and p.name in want:
            out.append(p)
    return sorted(out)


def find_experiments(date_dir: Path, sub: str) -> list[Path]:
    base = date_dir / sub
    if not base.exists():
        return []
    out = []
    for p in base.iterdir():
        if not p.is_dir():
            continue
        if EXPRX.match(p.name):
            out.append(p)
    return sorted(out)


def sortkey_by_stamp(p: Path):
    m = EXPRX.match(p.name)
    return m.group("stamp") if m else p.name


def mk_exp_name(date6: str, n: int) -> str:
    return f"Experiment_{date6}_{n}"


def ensure_move(src: Path, dst: Path, apply: bool, overwrite: bool, log: list[str]):
    if src.resolve() == dst.resolve():
        return
    if dst.exists():
        if overwrite:
            if apply:
                if dst.is_file():
                    dst.unlink()
                else:
                    shutil.rmtree(dst)
            log.append(f"[OVERWRITE] {dst}")
        else:
            raise RuntimeError(f"Destination exists (use --overwrite): {dst}")
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    log.append(f"MOVE DIR: {src} -> {dst}")


def ensure_move_file(src: Path, dst: Path, apply: bool, overwrite: bool, log: list[str]):
    if src.resolve() == dst.resolve():
        return dst
    if dst.exists():
        if overwrite:
            if apply:
                dst.unlink()
            log.append(f"[OVERWRITE] {dst}")
        else:
            # uniquify
            n = 1
            stem, suf = dst.stem, dst.suffix
            while (dst.parent / f"{stem}__{n}{suf}").exists():
                n += 1
            dst = dst.parent / f"{stem}__{n}{suf}"
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    log.append(f"MOVE FILE: {src} -> {dst}")
    return dst


def extract_guids_from_index(indexfile: Path) -> list[str]:
    df = sniff_tab(indexfile)
    if "URL" not in df.columns:
        return []
    guids = set()
    for url in df["URL"]:
        m = URL_GUIDRX.search(url)
        if m:
            g = m.group(1)
            if GUIDRX.fullmatch(g):
                guids.add(g)
    return sorted(guids)


def find_guid_dirs(date_dir: Path, guid: str) -> list[Path]:
    """
    Return folders that are either:
      - exactly the GUID (…/<guid>)
      - a GUID that has already been split into well siblings (…/<guid>_B11, etc.)
    """
    hits = []
    guid_lower = guid.lower()
    for p in date_dir.rglob("*"):
        if not p.is_dir():
            continue
        name = p.name
        if name.lower() == guid_lower:
            hits.append(p)
            continue
        # already split siblings like "<guid>_B11"
        if name.lower().startswith(guid_lower + "_"):
            tail = name[len(guid):]  # "_B11", "_E7", etc.
            if re.fullmatch(r"_[A-H][1-9]\d?$", tail, flags=re.I):
                hits.append(p)
    return sorted(set(hits))


def build_name(template: str, rec: dict) -> str:
    safe = lambda s: re.sub(r"[^\w\.-]+", "_", str(s).strip())
    data = {
        "row": rec.get("Row"),
        "col": rec.get("Column"),
        "plane": rec.get("Plane"),
        "timepoint": rec.get("Timepoint"),
        "sequence": rec.get("Sequence"),
        "group": rec.get("Group"),
        "field": rec.get("Field"),
        "channel": rec.get("Channel Name") or rec.get("Channel"),
        "channel_id": rec.get("Channel ID") or rec.get("ChannelID"),
        "timestamp": rec.get("Time Stamp") or rec.get("TimeStamp"),
    }
    for k in data:
        data[k] = safe(data[k]) if data[k] is not None else ""
    try:
        return template.format(**data)
    except Exception:
        return f"R{data['row']}C{data['col']}_F{data['field']}T{data['timepoint']}P{data['plane']}_{data['channel']}"


def reorganize_by_indexfile(experiment_dir: Path, img_dir: Path, template: str, apply: bool, overwrite: bool, log: list[str], fov_subdirs: bool = False):
    indexfile = experiment_dir / "indexfile.txt"
    if not indexfile.exists():
        log.append(f"[WARN] No indexfile.txt at {experiment_dir}")
        return
    df = sniff_tab(indexfile)
    needed = ["Row", "Column", "Plane", "Timepoint", "Field", "URL"]
    for col in needed:
        if col not in df.columns:
            log.append(f"[WARN] indexfile missing column {col} at {experiment_dir}")
            return

    moved = 0
    for _, row in df.iterrows():
        # well subfolder
        r = row.get("Row")
        c = row.get("Column")
        try:
            r_i, c_i = int(float(r)), int(float(c))
        except Exception:
            r_i, c_i = r, c
        well = f"{row_to_letter(r_i)}{int(c_i) if isinstance(c_i, (int, float)) or (isinstance(c_i, str) and c_i.isdigit()) else c_i}"

        dst_dir = img_dir.parent / f"{img_dir.name}_{well}"
        
        # Add FOV subdirectory if requested
        if fov_subdirs:
            field = row.get("Field", "")
            try:
                field_num = int(float(field)) if field else 1
                dst_dir = dst_dir / f"F{field_num}"
            except Exception:
                pass

        urlpath = urlparse(url = row["url"]).path
        src_name = Path(urlpath).name

        # try exact; then suffix
        candidates = list(img_dir.rglob(src_name))
        if not candidates:
            candidates = [p for p in img_dir.rglob("*.tif*") if p.name.endswith(src_name)]
        if not candidates:
            log.append(f"[MISS] {src_name} (not found under {img_dir})")
            continue
        src_file = candidates[0]

        new_name = build_name(template, row.to_dict())
        if not new_name.lower().endswith((".tif", ".tiff")):
            new_name += src_file.suffix
        dst_file = dst_dir / new_name
        ensure_move_file(src_file, dst_file, apply, overwrite, log)
        moved += 1

    if apply and not any(img_dir.rglob("*")):
        try:
            img_dir.rmdir()
            log.append(f"RMDIR: {img_dir}")
        except Exception:
            pass


def split_by_filename(img_dir: Path, mode: str, apply: bool, overwrite: bool, log: list[str], fov_subdirs: bool = False):
    """
    Group images in img_dir into 96-well folders using filename-derived row/col.
    mode='sibling' => '<img_dir.name>_E11' siblings
    mode='subdir'  => 'img_dir/E11' subdirectories
    If fov_subdirs=True, also organize by FOV (Field) within each well: 'E11/F1', 'E11/F2', etc.
    """
    tif_exts = {".tif", ".tiff"}

    files = [p for p in img_dir.iterdir() if p.is_file() and p.suffix.lower() in tif_exts]
    if not files:
        files = [p for p in img_dir.rglob("*") if p.is_file() and p.suffix.lower() in tif_exts]
    if not files:
        return

    matched, skipped = 0, 0
    for f in files:
        rc = parse_row_col_from_name(f.name)
        if not rc:
            skipped += 1
            log.append(f"[SKIP] no row/col in name: {f.name}")
            continue
        row, col = rc
        well = f"{row_to_letter(row)}{col}"
        dst_dir = img_dir.parent / f"{img_dir.name}_{well}" if mode == "sibling" else img_dir / well
        
        # Extract FOV from filename if fov_subdirs is enabled
        if fov_subdirs:
            m = FILE_NAME_RX.match(f.name)
            if m:
                field = m.group("field")
                try:
                    field_num = int(field) if field else 1
                    dst_dir = dst_dir / f"F{field_num}"
                except Exception:
                    pass
        
        ensure_move_file(f, dst_dir / f.name, apply, overwrite, log)
        matched += 1

    log.append(f"[split_by_filename] {img_dir} -> matched {matched}, skipped {skipped}")
    # prune empty source in sibling mode
    if mode == "sibling" and apply:
        if not any(img_dir.rglob("*")):
            try:
                img_dir.rmdir()
                log.append(f"RMDIR: {img_dir}")
            except Exception:
                pass


def _is_under(child: Path, base: Path) -> bool:
    try:
        child.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def collect_image_dirs(date_dir: Path, roi_mode: str) -> list[Path]:
    """
    Return directories OUTSIDE date_dir/Analysis that contain at least one .tif/.tiff,
    skipping:
      - well bins already created (B11) and sibling bins (..._B11)
      - raw GUID folders
      - ROI roots themselves (Experiment_*_*_ROI_Images*)
    Prefer canonical Experiment_*_*/Images when present.
    """
    tif_dirs = set()
    analysis_root = (date_dir / "Analysis").resolve()  # <-- FIXED
    roi_root_rx = re.compile(r"Experiment_\d{6}_\d+_ROI_Images.*", re.I)

    # 1) Prefer canonical Images dirs under each Experiment_*_* (outside Analysis)
    for exp in date_dir.rglob("Experiment_*_*"):
        if not exp.is_dir():
            continue
        if _is_under(exp, analysis_root):
            continue
        images_dir = exp / "Images"
        if images_dir.is_dir():
            if any(p.is_file() and p.suffix.lower() in (".tif", ".tiff") for p in images_dir.rglob("*")):
                tif_dirs.add(images_dir.resolve())

    # 2) Pick up stray tif-holding dirs (outside Analysis) not under Images/
    for p in date_dir.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in (".tif", ".tiff"):
            continue
        parent = p.parent
        if _is_under(parent, analysis_root):
            continue
        name = parent.name
        if WELL_SUBDIR_RX.fullmatch(name):   # 'B11'
            continue
        if WELL_SIBLING_RX.fullmatch(name):  # '..._B11'
            continue
        if GUIDRX.fullmatch(name):
            continue
        if roi_root_rx.fullmatch(name):
            continue
        tif_dirs.add(parent.resolve())

    return sorted(tif_dirs)



# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(
        description=(
            "Phenix data organizer: normalize experiment names, attach ROI images via indexfile.txt, "
            "and split ANY image directory (recursively) into per-well folders."
        )
    )
    ap.add_argument("--consolidate-roi-siblings", action="store_true",
                help="Move any sibling well folders (…_ROI_Images_B11) into …_ROI_Images/B11 and remove siblings")
    ap.add_argument("--root", default=".", help="Path to the data root (default: .)")
    ap.add_argument("--dates", nargs="*", help="Specific date folders to process (e.g., 081825 082125)")
    ap.add_argument("--all-dates", action="store_true", help="Process all 6-digit date folders under root")
    ap.add_argument("--apply", action="store_true", help="Actually perform moves (default: dry-run)")
    ap.add_argument("--overwrite", action="store_true", help="Allow overwriting existing targets")
    ap.add_argument("--mirror-root", action="store_true", help="Also normalize experiment names at the date root (not only Analysis)")
    ap.add_argument("--name-template", default="R{row}C{col}_F{field}T{timepoint}P{plane}_{channel}.tiff",
                    help="Filename template when reorganizing via indexfile.txt (not used for filename-based split)")
    ap.add_argument("--well-source", choices=["filename", "indexfile"], default="filename",
                    help="How to bin well folders for ROI dirs: parse file NAMES (default) or use indexfile.txt")
    ap.add_argument("--roi-mode", choices=["sibling", "subdir"], default="sibling",
                    help="Create well folders as siblings '<DIR>_E11' (default) or subdirs 'DIR/E11'")
    ap.add_argument("--fov-subdirs", action="store_true",
                    help="Organize images by FOV within well folders (creates Well/F1, Well/F2, etc. subdirectories)")
    ap.add_argument("--no-scan-all-img-dirs", action="store_true",
                    help="Disable recursive scan of ALL image directories under each date")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        sys.exit(f"Root not found: {root}")

    if not args.all_dates and not args.dates:
        sys.exit("Specify --dates ... or --all-dates")

    date_dirs = find_date_dirs(root, args.dates or [], args.all_dates)
    if not date_dirs:
        sys.exit("No matching date folders found.")

    overall_log: list[str] = []
    for date_dir in date_dirs:
        date6 = date_dir.name
        log = overall_log
        log.append(f"\n=== DATE {date6} ===")

        # A) Normalize experiments under Analysis
        analysis_exps = find_experiments(date_dir, "Analysis")
        analysis_exps = sorted(analysis_exps, key=sortkey_by_stamp)
        for i, src in enumerate(analysis_exps, start=1):
            dest = date_dir / "Analysis" / mk_exp_name(date6, i)
            try:
                ensure_move(src, dest, args.apply, args.overwrite, log)
            except Exception as e:
                log.append(f"[SKIP] {src} -> {dest}: {e}")

        # Optional: mirror at date root
        if args.mirror_root:
            root_exps = find_experiments(date_dir, ".")
            root_exps = sorted(root_exps, key=sortkey_by_stamp)
            for i, src in enumerate(root_exps, start=1):
                dest = date_dir / mk_exp_name(date6, i)
                try:
                    ensure_move(src, dest, args.apply, args.overwrite, log)
                except Exception as e:
                    log.append(f"[SKIP] {src} -> {dest}: {e}")

        # B) Map GUID ROI folders to each experiment via indexfile.txt
        exp_dirs = sorted([p for p in (date_dir / "Analysis").glob(f"Experiment_{date6}_*") if p.is_dir()])
        for exp in exp_dirs:
            indexfile = exp / "indexfile.txt"
            if not indexfile.exists():
                continue

            guids = extract_guids_from_index(indexfile)
            if not guids:
                continue

            matched_dirs = []
            for g in guids:
                matched_dirs.extend(find_guid_dirs(date_dir, g))

            for j, guid_dir in enumerate(sorted(set(matched_dirs)), start=1):
                base = f"{exp.name}_ROI_Images"
                # preserve existing well suffix if present (e.g., "<guid>_B11" -> "..._ROI_Images_B11")
                m = re.search(r"(_[A-H][1-9]\d?$)", guid_dir.name, flags=re.I)
                if m:
                    target_name = base + m.group(1)
                else:
                    target_name = base if len(matched_dirs) == 1 else f"{base}_{j:02d}"
                target_dir = exp.parent / target_name
                try:
                    ensure_move(guid_dir, target_dir, args.apply, args.overwrite, log)
                except Exception as e:
                    log.append(f"[SKIP ROI] {guid_dir} -> {target_dir}: {e}")


        # C) Split ROI dirs into well folders (filename-based by default; indexfile option available)
        for roi in (date_dir / "Analysis").glob(f"Experiment_{date6}_*_ROI_Images*"):
            if not roi.is_dir():
                continue
            if args.well_source == "filename":
                split_by_filename(roi, args.roi_mode, args.apply, args.overwrite, log, args.fov_subdirs)
            else:
                exp_name = re.sub(r"_ROI_Images.*$", "", roi.name)
                exp_dir = roi.parent / exp_name
                if not exp_dir.exists():
                    log.append(f"[WARN] No matching experiment dir for ROI: {roi}")
                    continue
                reorganize_by_indexfile(exp_dir, roi, args.name_template, args.apply, args.overwrite, log, args.fov_subdirs)

        # D) NEW: split ALL image directories recursively under this date
        if not args.no_scan_all_img_dirs:  # default: scan everything
            img_dirs = collect_image_dirs(date_dir, args.roi_mode)
            for d in img_dirs:
                split_by_filename(d, args.roi_mode, args.apply, args.overwrite, log, args.fov_subdirs)
        if args.consolidate_roi_siblings:
            consolidate_roi_siblings(date_dir, args.apply, args.overwrite, log)


    # Print plan / log
    mode = "APPLY" if args.apply else "DRY-RUN"
    header = f"\n[{mode}] PLAN / LOG".ljust(80, "=")
    print(header)
    print("\n".join(overall_log))
    print("=" * len(header))


if __name__ == "__main__":
    main()
