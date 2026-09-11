#!/usr/bin/env python3
"""
compute_fov_background_avg.py

Compute per-FOV background for each sequence (Baseline, Stim1, Stim2) using
a pixel-wise averaged image:

  For each (well, field, sequence):
    1. Load images for the sequence.
       - Sequence 2 (Baseline): use first N timepoints (default 5), no skipping.
       - Sequences 3 (Stim1) and 4 (Stim2): skip first 2 timepoints, then
         use up to N timepoints from the remainder.
    2. Compute pixel-wise average across selected images → averaged_image.
    3. Find the mode of averaged_image → background value for all timepoints
       in that sequence.

Expected directory layout (Opera Phenix):
  <root>/
    <date6>/
      <MeasurementName>/
        Images/
          r02c02f01p01-ch1sk1fk1fl1.tiff   ← raw fluorescence images
          ...
      Analysis/
        <ExperimentName>/
          indexfile.txt                     ← tab-separated index
          Evaluation*/

Output CSV has columns:
  well, field, sequence, sequence_name, background, n_images, timepoints_used

Usage example:
  python src/pt/compute_fov_background_avg.py \\
      --root "D:/PhenixData/Raw" \\
      --dates 092625 \\
      --channel "Alexa 488"
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import tifffile
    HAS_TIFFFILE = True
except ImportError:
    HAS_TIFFFILE = False

if not HAS_PIL and not HAS_TIFFFILE:
    sys.exit("ERROR: Need PIL/Pillow or tifffile. Install with: pip install pillow tifffile")


# Image filename pattern: r02c02f01p01-ch1sk1fk1fl1.tiff
# Groups: row, col, field, plane, channel, sk(=timepoint)
SK_FILE_RX = re.compile(
    r"r(\d+)c(\d+)f(\d+)p(\d+)-ch(\d+)sk(\d+)fk\d+fl\d+\.tiff?$",
    re.IGNORECASE,
)

SEQUENCE_NAMES = {2: "Baseline", 3: "Stim1", 4: "Stim2"}


# ─── helpers ─────────────────────────────────────────────────────────────────

def sniff_tab(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="\t", engine="python", dtype=str, keep_default_na=False)


def row_to_letter(row) -> str:
    try:
        r = int(row)
    except Exception:
        return str(row)
    return chr(ord("A") + max(0, r - 1))


def load_image(img_path: Path) -> np.ndarray:
    if HAS_PIL:
        return np.array(Image.open(img_path))
    return tifffile.imread(str(img_path))


def estimate_mode(arr: np.ndarray) -> float:
    """Mode of a 2-D (or flattened) float/int array via histogram."""
    arr = np.asarray(arr, dtype=np.float64).ravel()
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.nan
    # Integer-valued: exact bincount
    if np.all(finite == np.round(finite)) and (finite.max() - finite.min()) <= 1_000_000:
        ints = finite.astype(np.int64)
        lo = int(ints.min())
        counts = np.bincount(ints - lo)
        return float(lo + int(np.argmax(counts)))
    # Float: histogram
    bins = int(min(4096, max(64, np.sqrt(finite.size))))
    counts, edges = np.histogram(finite, bins=bins)
    idx = int(np.argmax(counts))
    return float((edges[idx] + edges[idx + 1]) / 2.0)


def find_images_dir(date_dir: Path, exp_dir: Path) -> "Path | None":
    """
    Find the Images/ folder for a given experiment.

    The measurement folder sits alongside Analysis/ with the same name as the
    experiment directory:
        <date_dir>/<exp_dir.name>/Images/

    Falls back to searching the whole date_dir if the direct path is missing.
    """
    # Primary: <date_dir>/<same_name_as_exp>/Images/
    candidate = date_dir / exp_dir.name / "Images"
    if candidate.is_dir() and next(candidate.rglob("r*c*f*p*-ch*sk*.tiff*"), None):
        return candidate

    # Fallback: search anywhere under date_dir
    for images_dir in date_dir.rglob("Images"):
        if images_dir.is_dir():
            sample = next(images_dir.rglob("r*c*f*p*-ch*sk*.tiff*"), None)
            if sample:
                return images_dir
    return None


def build_image_index(images_dir: Path, channel_id: "int | None" = None) -> dict:
    """
    Scan the Images/ directory (recursively, to handle well subfolders) and
    build a dict: (row, col, field, timepoint) -> Path
    Optionally filter by channel_id (integer from filename, e.g. ch1 → 1).
    """
    index = {}
    for p in images_dir.rglob("*.tiff*"):
        m = SK_FILE_RX.match(p.name)
        if not m:
            continue
        r, c, f, _plane, ch, sk = (int(m.group(i)) for i in range(1, 7))
        if channel_id is not None and ch != channel_id:
            continue
        key = (r, c, f, sk)
        # If multiple planes / channels, keep first found (p01 preferred)
        if key not in index:
            index[key] = p
    return index


# ─── core per-sequence background ────────────────────────────────────────────

def averaged_mode(image_paths: list) -> "tuple[float, int]":
    """
    Load images, compute pixel-wise average, return (mode_of_averaged_image, n_images_loaded).
    """
    arrays = []
    for p in image_paths:
        try:
            arrays.append(load_image(p).astype(np.float64))
        except Exception as e:
            print(f"  [WARN] Cannot load {p.name}: {e}", file=sys.stderr)

    if not arrays:
        return np.nan, 0

    stack = np.stack(arrays, axis=0)   # (n_frames, H, W)
    avg_image = stack.mean(axis=0)     # (H, W)
    return estimate_mode(avg_image), len(arrays)


# ─── main processing ──────────────────────────────────────────────────────────

def resolve_channel_id(df: pd.DataFrame, channel_filter: str) -> "int | None":
    """
    Given a channel name substring, find the corresponding Channel ID integer
    from the indexfile so we can filter images by ch<N> in filenames.
    Returns None if we can't determine it (accept all channels).
    """
    ch_name_col = next(
        (c for c in df.columns if c.lower() in ["channel name", "channelname"]), None
    )
    ch_id_col = next(
        (c for c in df.columns if c.lower() in ["channel id", "channelid", "channel"]), None
    )
    if ch_name_col and ch_id_col and channel_filter:
        mask = df[ch_name_col].str.contains(channel_filter, case=False, na=False)
        ids = pd.to_numeric(df.loc[mask, ch_id_col], errors="coerce").dropna().unique()
        if len(ids) == 1:
            return int(ids[0])
        if len(ids) > 1:
            print(f"  [WARN] Channel filter '{channel_filter}' matched multiple IDs {ids}; "
                  f"using {int(ids[0])}")
            return int(ids[0])
    return None


def process_experiment(exp_dir: Path, date_dir: Path,
                       max_timepoints: int = 5,
                       skip_start_stim: int = 2,
                       channel_filter: str = None,
                       sequences: list = None) -> pd.DataFrame:
    """
    Process one experiment directory (containing indexfile.txt).

    Parameters
    ----------
    exp_dir          : directory containing indexfile.txt
    date_dir         : date-level directory (parent of Analysis/ and Measurement/)
    max_timepoints   : frames to use per sequence (after any skipping)
    skip_start_stim  : timepoints to drop at start of Stim sequences (3, 4)
    channel_filter   : optional channel name substring (e.g. 'Alexa 488')
    sequences        : sequence numbers to process (default [2, 3, 4])
    """
    if sequences is None:
        sequences = [2, 3, 4]

    indexfile = exp_dir / "indexfile.txt"
    if not indexfile.exists():
        print(f"  [SKIP] No indexfile.txt in {exp_dir}")
        return pd.DataFrame()

    df = sniff_tab(indexfile)

    # Normalise required column names
    col_map = {c.lower(): c for c in df.columns}
    required = ["row", "column", "sequence", "field", "timepoint"]
    missing = [r for r in required if r not in col_map]
    if missing:
        print(f"  [SKIP] Missing columns in {exp_dir}: {missing}")
        return pd.DataFrame()
    df = df.rename(columns={v: k for k, v in col_map.items() if k in required})

    # Numeric conversions
    for c in required:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=required)

    # Well label
    df["well"] = df.apply(lambda r: f"{row_to_letter(r['row'])}{int(r['column'])}", axis=1)

    # Resolve channel ID for filename filtering
    channel_id = resolve_channel_id(df, channel_filter) if channel_filter else None
    if channel_filter:
        if channel_id is not None:
            print(f"  Channel '{channel_filter}' → ch{channel_id} in image filenames")
        else:
            print(f"  [WARN] Could not resolve channel ID for '{channel_filter}'; "
                  f"will accept all channels")

    # Optional: filter indexfile rows to the requested channel (for timepoint selection only)
    if channel_filter:
        ch_name_col = next(
            (c for c in df.columns if c.lower() in ["channel name", "channelname"]), None
        )
        if ch_name_col:
            df = df[df[ch_name_col].str.contains(channel_filter, case=False, na=False)]

    # Find the Images directory: <date_dir>/<exp_name>/Images/
    images_dir = find_images_dir(date_dir, exp_dir)
    if images_dir is None:
        print(f"  [SKIP] No Images/ folder found under {date_dir}")
        return pd.DataFrame()
    print(f"  Images directory: {images_dir}")

    # Build image index once (fast)
    img_index = build_image_index(images_dir, channel_id=channel_id)
    print(f"  Indexed {len(img_index)} image files (ch{channel_id})")

    results = []

    for seq_num in sequences:
        seq_df = df[df["sequence"] == seq_num].copy()
        if seq_df.empty:
            print(f"  [INFO] Sequence {seq_num} not found")
            continue

        seq_name = SEQUENCE_NAMES.get(seq_num, f"Seq{seq_num}")
        skip_n = skip_start_stim if seq_num in (3, 4) else 0

        for (well, field), grp in seq_df.groupby(["well", "field"]):
            grp_sorted = grp.sort_values("timepoint")
            all_tps = np.sort(grp_sorted["timepoint"].unique())

            # Select timepoints
            if skip_n and len(all_tps) > skip_n:
                tps_to_use = all_tps[skip_n: skip_n + max_timepoints]
            else:
                tps_to_use = all_tps[:max_timepoints]

            row_num = int(grp_sorted.iloc[0]["row"])
            col_num = int(grp_sorted.iloc[0]["column"])
            field_num = int(field)

            # Look up images in the pre-built index
            image_paths = []
            for tp in tps_to_use:
                key = (row_num, col_num, field_num, int(tp))
                if key in img_index:
                    image_paths.append(img_index[key])
                else:
                    print(f"    [WARN] No image for r{row_num}c{col_num}f{field_num} "
                          f"sk{int(tp)} (seq{seq_num})")

            if not image_paths:
                print(f"  [WARN] No images for well={well}, field={field_num}, seq={seq_num}")
                continue

            bg, n_loaded = averaged_mode(image_paths)

            results.append({
                "experiment": exp_dir.name,
                "well": well,
                "field": field_num,
                "sequence": seq_num,
                "sequence_name": seq_name,
                "background": round(bg, 3) if np.isfinite(bg) else np.nan,
                "n_images": n_loaded,
                "timepoints_used": sorted(int(tp) for tp in tps_to_use),
                "skip_start": skip_n,
            })
            print(f"    {well} f{field_num:02d} seq{seq_num} ({seq_name:8s}): "
                  f"bg={bg:8.1f}  ({n_loaded} frames, skip_start={skip_n})")

    return pd.DataFrame(results)


def find_experiment_dirs(date_dir: Path) -> list:
    """
    Find all directories under date_dir/Analysis/ that contain indexfile.txt.
    Works regardless of experiment folder naming convention.
    """
    analysis = date_dir / "Analysis"
    if not analysis.exists():
        return []
    return [p for p in analysis.iterdir()
            if p.is_dir() and (p / "indexfile.txt").exists()]


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description=(
            "Compute per-FOV background per sequence (Baseline / Stim1 / Stim2) "
            "using averaged + Gaussian-smoothed image mode.\n\n"
            "Root should be the directory that contains the 6-digit date folders, "
            "e.g. 'D:/PhenixData/Raw'."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--root", default=".",
                    help="Directory containing 6-digit date folders (default: .)")
    ap.add_argument("--dates", nargs="*",
                    help="Date folder(s) to process (e.g. 092625 100225)")
    ap.add_argument("--all-dates", action="store_true",
                    help="Process all 6-digit date folders under --root")
    ap.add_argument("--experiment",
                    help="Path to a single experiment directory (containing indexfile.txt)")
    ap.add_argument("--output", "-o",
                    help="Output CSV path (default: <date>_fov_backgrounds_avg.csv)")
    ap.add_argument("--max-timepoints", type=int, default=20,
                    help="Max frames per sequence after optional skipping (default: 20)")
    ap.add_argument("--skip-start-stim", type=int, default=2,
                    help="Frames to skip at start of Stim1/Stim2 sequences (default: 2)")

    ap.add_argument("--channel",
                    help="Channel name substring to select (e.g. 'Alexa 488')")
    ap.add_argument("--sequences", nargs="*", type=int, default=[2, 3, 4],
                    help="Sequence numbers to process (default: 2 3 4)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.exists():
        sys.exit(f"Root not found: {root}")

    if not args.all_dates and not args.dates and not args.experiment:
        sys.exit("Specify --dates ..., --all-dates, or --experiment")

    # Discover (date_dir, exp_dir) pairs
    pairs = []   # list of (date_dir, exp_dir)
    date_dirs = []

    if args.experiment:
        ep = Path(args.experiment).resolve()
        if not ep.exists() or not (ep / "indexfile.txt").exists():
            sys.exit(f"Experiment directory not found or missing indexfile.txt: {ep}")
        # date_dir = ep.parent.parent  (Analysis/<exp> → Analysis → date)
        pairs.append((ep.parent.parent, ep))
    else:
        if args.all_dates:
            date_dirs = sorted(p for p in root.iterdir()
                               if p.is_dir() and re.fullmatch(r"\d{6}", p.name))
        else:
            want = set(args.dates or [])
            date_dirs = sorted(p for p in root.iterdir()
                               if p.is_dir() and p.name in want)
        if not date_dirs:
            sys.exit(f"No matching date folders found under {root}. "
                     f"Check that --root points to the directory containing "
                     f"the 6-digit date folders.")
        for dd in date_dirs:
            exp_dirs = find_experiment_dirs(dd)
            if not exp_dirs:
                print(f"[WARN] No experiment directories (with indexfile.txt) found "
                      f"under {dd / 'Analysis'}")
            for ep in exp_dirs:
                pairs.append((dd, ep))

    if not pairs:
        sys.exit("No experiment directories found.")

    print(f"Processing {len(pairs)} experiment(s), sequences: {args.sequences}")
    print(f"  Baseline (seq 2): first {args.max_timepoints} timepoints")
    print(f"  Stim1/2 (seq 3/4): skip {args.skip_start_stim}, "
          f"then up to {args.max_timepoints} timepoints")


    all_dfs = []
    for date_dir, exp_dir in pairs:
        print(f"\n[Experiment] {exp_dir.name}")
        result = process_experiment(
            exp_dir, date_dir,
            max_timepoints=args.max_timepoints,
            skip_start_stim=args.skip_start_stim,
            channel_filter=args.channel,
            sequences=args.sequences,
        )
        if not result.empty:
            all_dfs.append(result)

    if not all_dfs:
        print("\nNo results generated.")
        sys.exit(1)

    combined = pd.concat(all_dfs, ignore_index=True)

    # Output path
    if args.output:
        out_path = Path(args.output)
    elif args.experiment:
        out_path = Path.cwd() / f"{pairs[0][1].name}_fov_backgrounds_avg.csv"
    else:
        out_path = Path.cwd() / f"{date_dirs[0].name}_fov_backgrounds_avg.csv"

    combined.to_csv(out_path, index=False)
    print(f"\n[OK] Saved {len(combined)} rows -> {out_path}")

    for seq_num, grp in combined.groupby("sequence"):
        sname = SEQUENCE_NAMES.get(seq_num, f"Seq{seq_num}")
        print(f"  Seq {seq_num} ({sname}): n={len(grp):4d}  "
              f"mean_bg={grp['background'].mean():.1f}  "
              f"median={grp['background'].median():.1f}  "
              f"std={grp['background'].std():.1f}")


if __name__ == "__main__":
    main()
