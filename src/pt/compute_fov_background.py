#!/usr/bin/env python3
"""
compute_fov_background.py

Compute background signal for each FOV (Field of View) using the first 5 images
from Sequence 2 (baseline) for each well. This is useful for downstream analysis
that requires FOV-specific background correction.

The script:
1. Reads indexfile.txt to identify Sequence 2 images
2. Finds the first 5 timepoints in Sequence 2 for each FOV
3. Computes background statistics (mode, median, mean, std) per FOV
4. Saves results to CSV for downstream analysis
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse
import pandas as pd
import numpy as np

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    try:
        import tifffile
        HAS_TIFFFILE = True
        HAS_PIL = False
    except ImportError:
        sys.exit("ERROR: Need either PIL/Pillow or tifffile for image reading. Install with: pip install pillow tifffile")

# Reuse patterns from phenix_reorg.py
GUIDRX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
URL_GUIDRX = re.compile(r"/C/([0-9a-f\-]{36})/", re.I)


def pct_removed(before: int, after: int) -> float:
    """Percent removed going from `before` to `after`."""
    if before <= 0:
        return 0.0
    return ((before - after) / before) * 100.0


def sniff_tab(path: Path) -> pd.DataFrame:
    """Read tab-separated file."""
    return pd.read_csv(path, sep="\t", engine="python", dtype=str, keep_default_na=False)


def row_to_letter(row):
    """Convert row number (1-based) to letter (A-H)."""
    try:
        r = int(row)
    except Exception:
        return str(row)
    if r < 1:
        r = 1
    return chr(ord("A") + (r - 1))


def load_image(img_path: Path) -> np.ndarray:
    """Load TIFF image and return as numpy array."""
    if HAS_PIL:
        try:
            img = Image.open(img_path)
            return np.array(img)
        except Exception as e:
            raise RuntimeError(f"Failed to load {img_path} with PIL: {e}")
    else:
        try:
            return tifffile.imread(str(img_path))
        except Exception as e:
            raise RuntimeError(f"Failed to load {img_path} with tifffile: {e}")


def estimate_mode(arr: np.ndarray) -> float:
    """
    Estimate the mode of image intensities.

    For integer-valued image data, compute the exact most frequent value.
    For non-integer data, fall back to the center of the densest histogram bin.
    """
    arr = np.asarray(arr).ravel()
    if arr.size == 0:
        return np.nan

    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return np.nan

    if np.issubdtype(finite.dtype, np.integer):
        arr_int = finite.astype(np.int64, copy=False)
        lo = int(arr_int.min())
        hi = int(arr_int.max())
        span = hi - lo
        if 0 <= span <= 1_000_000:
            counts = np.bincount(arr_int - lo)
            return float(lo + int(np.argmax(counts)))
        vals, counts = np.unique(arr_int, return_counts=True)
        return float(vals[int(np.argmax(counts))])

    bins = int(min(4096, max(64, np.sqrt(finite.size))))
    counts, edges = np.histogram(finite.astype(np.float64, copy=False), bins=bins)
    idx = int(np.argmax(counts))
    return float((edges[idx] + edges[idx + 1]) / 2.0)


def compute_background_stats(image_paths: list[Path], method: str = "median", percentile: float = None) -> dict:
    """
    Compute background statistics from a list of images.
    
    Uses histogram-based approach: collects all pixel values and computes background
    using mode, median, mean, or a low percentile (to exclude bright cell pixels).
    
    Args:
        image_paths: List of paths to image files
        method: 'mode', 'median', 'mean', or 'percentile' for background computation
        percentile: If method='percentile', use this percentile (e.g., 10.0 for 10th percentile)
                    Lower percentiles exclude bright cell pixels better
        
    Returns:
        Dictionary with 'background', 'mean', 'std', 'min', 'max', 'n_images'
    """
    if not image_paths:
        return {
            "background": np.nan,
            "mode": np.nan,
            "mean": np.nan,
            "median": np.nan,
            "std": np.nan,
            "min": np.nan,
            "max": np.nan,
            "n_images": 0
        }
    
    all_values = []
    for img_path in image_paths:
        if not img_path.exists():
            continue
        try:
            img = load_image(img_path)
            # Flatten image and collect all pixel values
            # This includes both background and cell pixels
            all_values.extend(img.flatten())
        except Exception as e:
            print(f"  [WARN] Failed to load {img_path}: {e}", file=sys.stderr)
            continue
    
    if not all_values:
        return {
            "background": np.nan,
            "mode": np.nan,
            "mean": np.nan,
            "median": np.nan,
            "std": np.nan,
            "min": np.nan,
            "max": np.nan,
            "n_images": 0
        }
    
    arr = np.array(all_values, dtype=np.float64)
    
    mode_val = estimate_mode(arr)
    median_val = float(np.median(arr))
    mean_val = float(np.mean(arr))

    # Compute background using specified method
    if method == "percentile":
        if percentile is None:
            percentile = 10.0  # Default to 10th percentile
        bg = np.percentile(arr, percentile)
    elif method == "mode":
        bg = mode_val
    elif method == "median":
        bg = median_val
    else:  # mean
        bg = mean_val

    return {
        "background": float(bg),
        "mode": mode_val,
        "mean": mean_val,
        "median": median_val,
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "n_images": len(image_paths)
    }


def find_image_file(base_dir: Path, url_path: str, guid: str = None) -> Path | None:
    """
    Find image file given URL path from indexfile.
    Tries multiple strategies to locate the file.
    """
    url_parsed = urlparse(url_path)
    filename = Path(url_parsed.path).name
    
    # Strategy 1: Search in GUID folder if provided
    if guid:
        guid_dirs = list(base_dir.rglob(guid))
        for gdir in guid_dirs:
            candidate = gdir / filename
            if candidate.exists():
                return candidate
            # Try recursive search within GUID folder
            candidates = list(gdir.rglob(filename))
            if candidates:
                return candidates[0]
    
    # Strategy 2: Search in well-organized folders
    # Look for files matching the name pattern
    candidates = list(base_dir.rglob(filename))
    if candidates:
        return candidates[0]
    
    # Strategy 3: Try suffix matching
    candidates = [p for p in base_dir.rglob("*.tif*") if p.name.endswith(filename)]
    if candidates:
        return candidates[0]
    
    return None


def find_images_by_sk_pattern(base_dir: Path, row: int, col: int, field: int, 
                               sk_numbers: list[int]) -> list[Path]:
    """
    Find images by filename pattern: r<row>c<col>f<field>p<plane>-ch<channel>sk<timepoint>...
    where sk1-sk5 correspond to Sequence 2 (first 5 timepoints).
    
    Example: r08c01f06p01-ch2sk29fk1fl1.tiff
    - r08 = row 8, c01 = column 1, f06 = field 6, p01 = plane 1
    - ch2 = channel 2, sk29 = timepoint 29
    
    Args:
        base_dir: Root directory to search
        row: Row number (1-based)
        col: Column number (1-based)
        field: Field number (1-based)
        sk_numbers: List of sk numbers to find (e.g., [1, 2, 3, 4, 5] for Sequence 2)
    
    Returns:
        List of image paths matching the pattern
    """
    image_paths = []
    
    # Build patterns: r<row>c<col>f<field>p*-ch*sk<sk_num>*.tiff
    # Try both zero-padded and non-zero-padded versions
    row_strs = [f"r{row:02d}", f"r{row}"]
    col_strs = [f"c{col:02d}", f"c{col}"]
    field_strs = [f"f{field:02d}", f"f{field}"]
    
    for row_pat in row_strs:
        for col_pat in col_strs:
            for field_pat in field_strs:
                for sk_num in sk_numbers:
                    # Pattern: r<row>c<col>f<field>p*-ch*sk<sk_num>*.tiff
                    # Try both sk1 and sk01 formats
                    patterns = [
                        f"{row_pat}{col_pat}{field_pat}p*-ch*sk{sk_num}*.tiff",
                        f"{row_pat}{col_pat}{field_pat}p*-ch*sk{sk_num:02d}*.tiff",
                    ]
                    
                    for pattern in patterns:
                        # Use glob to find files
                        try:
                            candidates = list(base_dir.rglob(pattern))
                            for cand in candidates:
                                if cand.is_file() and cand not in image_paths:
                                    # Verify the sk number is correct (not sk10, sk11, etc. when looking for sk1)
                                    # Extract sk number from filename
                                    import re
                                    sk_match = re.search(r'sk(\d+)', cand.name)
                                    if sk_match:
                                        found_sk = int(sk_match.group(1))
                                        if found_sk == sk_num:
                                            image_paths.append(cand)
                        except Exception:
                            continue
    
    return sorted(image_paths)


def process_experiment(exp_dir: Path, base_dir: Path, max_timepoints: int = 5, 
                       method: str = "median", channel_filter: str = None, percentile: float = None) -> pd.DataFrame:
    """
    Process a single experiment directory to compute FOV backgrounds.
    
    Args:
        exp_dir: Experiment directory containing indexfile.txt
        base_dir: Root directory to search for image files
        max_timepoints: Number of timepoints to use from Sequence 2 (default: 5)
        method: 'mode', 'median', 'mean', or 'percentile' for background computation
        channel_filter: Optional channel name filter (e.g., "Alexa 488")
        
    Returns:
        DataFrame with columns including well, field, background, mode, mean, median, std, min, max, n_images, image_paths
    """
    indexfile = exp_dir / "indexfile.txt"
    if not indexfile.exists():
        print(f"  [SKIP] No indexfile.txt in {exp_dir}")
        return pd.DataFrame()
    
    df = sniff_tab(indexfile)
    total_rows = len(df)
    
    # Check required columns (case-insensitive)
    col_map = {c.lower(): c for c in df.columns}
    required_lower = ["row", "column", "sequence", "field", "timepoint", "url"]
    missing = []
    for req in required_lower:
        if req not in col_map:
            missing.append(req)
    if missing:
        print(f"  [SKIP] Missing columns in {exp_dir}: {missing}")
        return pd.DataFrame()
    
    # Normalize column names to lowercase for consistent access
    df = df.rename(columns={v: k for k, v in col_map.items() if k in required_lower})
    
    # Filter to Sequence 2
    try:
        df["sequence"] = pd.to_numeric(df["sequence"], errors="coerce")
        df_seq2 = df[df["sequence"] == 2].copy()
    except Exception:
        print(f"  [SKIP] Could not parse Sequence column in {exp_dir}")
        return pd.DataFrame()
    seq2_rows = len(df_seq2)
    
    if df_seq2.empty:
        print(f"  [SKIP] No Sequence 2 data in {exp_dir}")
        return pd.DataFrame()
    
    # Filter by channel if specified
    channel_col = None
    for col in df_seq2.columns:
        if col.lower() in ["channel name", "channel"]:
            channel_col = col
            break
    if channel_filter and channel_col:
        df_seq2 = df_seq2[df_seq2[channel_col].str.contains(channel_filter, case=False, na=False)]
    elif channel_filter and not channel_col:
        print(f"  [WARN] Channel filter '{channel_filter}' requested but no channel column found in {exp_dir}")
    channel_rows = len(df_seq2)
    
    # Extract GUID from URLs for better file finding
    guids = set()
    for url in df_seq2["url"]:
        m = URL_GUIDRX.search(url)
        if m:
            g = m.group(1)
            if GUIDRX.fullmatch(g):
                guids.add(g)
    
    # Group by well, field, and timepoint
    df_seq2["row"] = pd.to_numeric(df_seq2["row"], errors="coerce")
    df_seq2["column"] = pd.to_numeric(df_seq2["column"], errors="coerce")
    df_seq2["field"] = pd.to_numeric(df_seq2["field"], errors="coerce")
    df_seq2["timepoint"] = pd.to_numeric(df_seq2["timepoint"], errors="coerce")
    
    df_seq2 = df_seq2.dropna(subset=["row", "column", "field", "timepoint"])
    valid_rows = len(df_seq2)
    
    # Create well code
    df_seq2["well"] = df_seq2.apply(
        lambda row: f"{row_to_letter(row['row'])}{int(row['column'])}", axis=1
    )
    
    # Group by well and field, get first N timepoints
    results = []
    total_group_rows_before_tp = 0
    total_group_rows_after_tp = 0
    total_expected_images = 0
    total_found_images = 0
    
    for (well, field), group in df_seq2.groupby(["well", "field"]):
        # Sort by timepoint and take first max_timepoints
        group_sorted = group.sort_values("timepoint")
        timepoints_to_use = group_sorted["timepoint"].unique()[:max_timepoints]
        group_filtered = group_sorted[group_sorted["timepoint"].isin(timepoints_to_use)]
        total_group_rows_before_tp += len(group_sorted)
        total_group_rows_after_tp += len(group_filtered)
        
        # Get row and column for this well
        first_row = group_sorted.iloc[0]
        row_num = int(first_row["row"])
        col_num = int(first_row["column"])
        field_num = int(field)
        
        # Find image files - try multiple strategies
        image_paths_set = set()
        
        # Strategy 1: Try to find by URL from indexfile (original method)
        for _, row in group_filtered.iterrows():
            url = row["url"]
            # Try with each GUID
            img_path = None
            for guid in guids:
                img_path = find_image_file(base_dir, url, guid)
                if img_path:
                    break
            if not img_path:
                img_path = find_image_file(base_dir, url)
            
            if img_path and img_path.exists():
                image_paths_set.add(img_path)
        
        # Strategy 2: If URL method failed, try finding by sk pattern (sk1-sk5 for Sequence 2)
        if not image_paths_set:
            # Sequence 2 corresponds to sk1-sk5 (first 5 timepoints)
            sk_numbers = list(range(1, max_timepoints + 1))  # [1, 2, 3, 4, 5]
            image_paths_set = set(find_images_by_sk_pattern(base_dir, row_num, col_num, field_num, sk_numbers))
        
        image_paths = sorted(image_paths_set)
        expected_images = len(group_filtered)
        found_images = len(image_paths)
        missing_images = max(expected_images - found_images, 0)
        pct_images_missing = (missing_images / expected_images * 100.0) if expected_images > 0 else 0.0
        total_expected_images += expected_images
        total_found_images += found_images
        
        if not image_paths:
            print(f"  [WARN] No images found for well {well}, field {int(field)} (row={row_num}, col={col_num}, field={field_num})")
            continue
        
        # Compute background
        stats = compute_background_stats(image_paths, method=method, percentile=percentile)
        
        results.append({
            "experiment": exp_dir.name,
            "well": well,
            "field": int(field),
            "timepoints_used": sorted(timepoints_to_use.tolist()),
            "n_timepoints": len(timepoints_to_use),
            "background": stats["background"],
            "mode": stats["mode"],
            "mean": stats["mean"],
            "median": stats["median"],
            "std": stats["std"],
            "min": stats["min"],
            "max": stats["max"],
            "n_images": stats["n_images"],
            "image_paths": ";".join(str(p) for p in image_paths),
            "rows_in_group_before_timepoint_filter": len(group_sorted),
            "rows_in_group_after_timepoint_filter": len(group_filtered),
            "rows_removed_by_timepoint_filter": len(group_sorted) - len(group_filtered),
            "pct_removed_by_timepoint_filter": pct_removed(len(group_sorted), len(group_filtered)),
            "expected_images_for_group": expected_images,
            "images_found_for_group": found_images,
            "images_missing_for_group": missing_images,
            "pct_images_missing_for_group": pct_images_missing,
            "exp_rows_total": total_rows,
            "exp_rows_sequence2": seq2_rows,
            "exp_rows_after_channel_filter": channel_rows,
            "exp_rows_after_numeric_cleanup": valid_rows
        })

    print("  Filter summary:")
    print(f"    Total rows: {total_rows}")
    print(f"    Sequence 2 rows: {seq2_rows} (removed {total_rows - seq2_rows}, {pct_removed(total_rows, seq2_rows):.2f}%)")
    print(f"    After channel filter: {channel_rows} (removed {seq2_rows - channel_rows}, {pct_removed(seq2_rows, channel_rows):.2f}%)")
    print(f"    After numeric cleanup: {valid_rows} (removed {channel_rows - valid_rows}, {pct_removed(channel_rows, valid_rows):.2f}%)")
    print(
        f"    After timepoint cap (first {max_timepoints}): {total_group_rows_after_tp} "
        f"(removed {total_group_rows_before_tp - total_group_rows_after_tp}, "
        f"{pct_removed(total_group_rows_before_tp, total_group_rows_after_tp):.2f}%)"
    )
    if total_expected_images > 0:
        missing_total = max(total_expected_images - total_found_images, 0)
        pct_missing_total = (missing_total / total_expected_images) * 100.0
        print(
            f"    Image match loss: expected {total_expected_images}, found {total_found_images}, "
            f"missing {missing_total} ({pct_missing_total:.2f}%)"
        )

    return pd.DataFrame(results)


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Compute background signal for each FOV using first N images from Sequence 2 (baseline). "
            "Results are saved to CSV for downstream analysis."
        )
    )
    ap.add_argument("--root", default=".", help="Path to the data root (default: .)")
    ap.add_argument("--dates", nargs="*", help="Specific date folders to process (e.g., 081825 082125)")
    ap.add_argument("--all-dates", action="store_true", help="Process all 6-digit date folders under root")
    ap.add_argument("--output", "-o", help="Output CSV file path (default: <date>_fov_backgrounds.csv)")
    ap.add_argument("--max-timepoints", type=int, default=5,
                    help="Number of timepoints from Sequence 2 to use (default: 5)")
    ap.add_argument("--method", choices=["mode", "median", "mean", "percentile"], default="mode",
                    help="Background computation method: 'mode' (most frequent pixel value), 'median', 'mean', or 'percentile'")
    ap.add_argument("--percentile", type=float, default=10.0,
                    help="Percentile to use when method='percentile' (default: 10.0, i.e., 10th percentile). Lower values exclude more bright cell pixels.")
    ap.add_argument("--channel", help="Filter by channel name (e.g., 'Alexa 488')")
    ap.add_argument("--experiment", help="Process only specific experiment directory")
    args = ap.parse_args()
    
    root = Path(args.root).resolve()
    if not root.exists():
        sys.exit(f"Root not found: {root}")
    
    if not args.all_dates and not args.dates and not args.experiment:
        sys.exit("Specify --dates ..., --all-dates, or --experiment")
    
    # Find experiments to process
    exp_dirs = []
    
    if args.experiment:
        exp_path = Path(args.experiment).resolve()
        if exp_path.exists() and (exp_path / "indexfile.txt").exists():
            exp_dirs.append(exp_path)
            # Infer date from path
            date_dirs = [exp_path.parent.parent] if "Analysis" in str(exp_path) else [exp_path.parent]
        else:
            sys.exit(f"Experiment directory not found or missing indexfile.txt: {exp_path}")
    else:
        # Find date directories
        if args.all_dates:
            date_dirs = sorted([p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{6}", p.name)])
        else:
            want = set(args.dates or [])
            date_dirs = sorted([p for p in root.iterdir() if p.is_dir() and p.name in want])
        
        if not date_dirs:
            sys.exit("No matching date folders found.")
        
        # Find experiment directories
        for date_dir in date_dirs:
            analysis_dir = date_dir / "Analysis"
            if analysis_dir.exists():
                for exp in analysis_dir.glob("Experiment_*"):
                    if exp.is_dir() and (exp / "indexfile.txt").exists():
                        exp_dirs.append(exp)
    
    if not exp_dirs:
        sys.exit("No experiment directories found with indexfile.txt")
    
    print(f"Processing {len(exp_dirs)} experiment(s)...")
    
    all_results = []
    for exp_dir in exp_dirs:
        print(f"\nProcessing: {exp_dir.name}")
        # Determine base directory for image search
        base_dir = exp_dir.parent.parent  # Go up from Analysis/Experiment_* to date directory
        
        df = process_experiment(
            exp_dir, 
            base_dir, 
            max_timepoints=args.max_timepoints,
            method=args.method,
            channel_filter=args.channel,
            percentile=args.percentile if args.method == "percentile" else None
        )
        
        if not df.empty:
            all_results.append(df)
            print(f"  Found backgrounds for {len(df)} well/field combinations")
    
    if not all_results:
        print("\nNo results generated.")
        sys.exit(1)
    
    # Combine results
    result_df = pd.concat(all_results, ignore_index=True)
    
    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        # Use first date or experiment name
        if args.experiment:
            output_name = f"{exp_dirs[0].name}_fov_backgrounds.csv"
        else:
            date6 = date_dirs[0].name
            output_name = f"{date6}_fov_backgrounds.csv"
        output_path = root / output_name
    
    result_df.to_csv(output_path, index=False)
    print(f"\n[OK] Saved results to: {output_path}")
    print(f"  Total: {len(result_df)} well/field combinations")
    print(f"  Wells: {result_df['well'].nunique()}")
    print(f"  Fields: {sorted(result_df['field'].unique())}")
    
    # Print summary statistics
    print(f"\nBackground statistics (method: {args.method}):")
    if "mode" in result_df.columns:
        print(f"  Mean of per-FOV modes: {result_df['mode'].mean():.2f}")
    print(f"  Mean: {result_df['background'].mean():.2f}")
    print(f"  Median: {result_df['background'].median():.2f}")
    print(f"  Std: {result_df['background'].std():.2f}")
    print(f"  Range: [{result_df['background'].min():.2f}, {result_df['background'].max():.2f}]")


if __name__ == "__main__":
    main()

