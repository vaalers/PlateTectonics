#!/usr/bin/env python3
"""
background_method_report.py

Generate QA plots and summary statistics for candidate background estimators
using the same Sequence 2 / first-N-timepoints image selection used by
compute_fov_background.py.
"""

import argparse
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    from pt.compute_fov_background import estimate_mode, load_image, process_experiment
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pt.compute_fov_background import estimate_mode, load_image, process_experiment


def discover_experiment_dirs(root: Path, dates: list[str] | None, all_dates: bool, experiment: str | None) -> list[Path]:
    if experiment:
        exp_path = Path(experiment).expanduser().resolve()
        if exp_path.exists() and (exp_path / "indexfile.txt").exists():
            return [exp_path]
        raise SystemExit(f"Experiment directory not found or missing indexfile.txt: {exp_path}")

    if not all_dates and not dates:
        raise SystemExit("Specify --dates ..., --all-dates, or --experiment")

    if all_dates:
        date_dirs = sorted([p for p in root.iterdir() if p.is_dir() and re.fullmatch(r"\d{6}", p.name)])
    else:
        want = set(dates or [])
        date_dirs = sorted([p for p in root.iterdir() if p.is_dir() and p.name in want])

    if not date_dirs:
        raise SystemExit("No matching date folders found.")

    exp_dirs = []
    for date_dir in date_dirs:
        analysis_dir = date_dir / "Analysis"
        if not analysis_dir.exists():
            continue
        for exp in analysis_dir.glob("Experiment_*"):
            if exp.is_dir() and (exp / "indexfile.txt").exists():
                exp_dirs.append(exp)

    if not exp_dirs:
        raise SystemExit("No experiment directories found with indexfile.txt")
    return exp_dirs


def sample_values(arr: np.ndarray, max_points: int, rng: np.random.Generator) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float64).ravel()
    if max_points <= 0 or arr.size <= max_points:
        return arr
    idx = rng.choice(arr.size, size=max_points, replace=False)
    return arr[idx]


def summarize_values(arr: np.ndarray, comparison_percentiles: list[float]) -> dict:
    arr = np.asarray(arr, dtype=np.float64).ravel()
    if arr.size == 0:
        return {
            "n_pixels": 0,
            "mode": np.nan,
            "mean": np.nan,
            "median": np.nan,
            "std": np.nan,
            "mad": np.nan,
            "min": np.nan,
            "max": np.nan,
            "pearson_skew": np.nan,
            "bright_tail_fraction_3mad": np.nan,
            "bright_tail_fraction_p99": np.nan,
            "heuristic_best_method": np.nan,
            "supports_mode_over_mean": np.nan,
        }

    percentiles = {p: float(np.percentile(arr, p)) for p in comparison_percentiles}
    mode_val = float(estimate_mode(arr))
    mean_val = float(np.mean(arr))
    median_val = float(np.median(arr))
    std_val = float(np.std(arr))
    mad_val = float(np.median(np.abs(arr - median_val)))
    p99 = percentiles[max(comparison_percentiles)]
    threshold_3mad = median_val + (3.0 * mad_val)
    bright_tail_fraction_3mad = float(np.mean(arr > threshold_3mad)) if mad_val > 0 else 0.0
    bright_tail_fraction_p99 = float(np.mean(arr >= p99))
    pearson_skew = float(3.0 * (mean_val - median_val) / std_val) if std_val > 0 else 0.0
    mean_median_gap = mean_val - median_val

    if pearson_skew >= 0.25 or bright_tail_fraction_3mad >= 0.03 or abs(mode_val - mean_val) > max(1.0, mad_val):
        best_method = "mode"
    else:
        best_method = "mean"

    summary = {
        "n_pixels": int(arr.size),
        "mode": mode_val,
        "mean": mean_val,
        "median": median_val,
        "std": std_val,
        "mad": mad_val,
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "mean_minus_median": mean_median_gap,
        "pearson_skew": pearson_skew,
        "bright_tail_fraction_3mad": bright_tail_fraction_3mad,
        "bright_tail_fraction_p99": bright_tail_fraction_p99,
        "heuristic_best_method": best_method,
        "supports_mode_over_mean": bool(best_method == "mode"),
    }
    for p, val in percentiles.items():
        summary[f"p{int(p):02d}"] = val
    return summary


def plot_histogram(
    values: np.ndarray,
    summary: dict,
    out_path: Path,
    title: str,
    bins: int,
    log_y: bool,
    comparison_percentiles: list[float],
) -> None:
    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=150)
    ax.hist(values, bins=bins, color="#b8d8d8", edgecolor="#476a6f", alpha=0.9)
    ax.axvline(summary["mode"], color="#6a3fb5", linewidth=2, linestyle="-.", label=f"mode={summary['mode']:.2f}")
    ax.axvline(summary["mean"], color="#b22222", linewidth=2, linestyle="-", label=f"mean={summary['mean']:.2f}")
    ax.axvline(summary["median"], color="#1f5aa6", linewidth=2, linestyle="--", label=f"median={summary['median']:.2f}")
    for p in comparison_percentiles:
        key = f"p{int(p):02d}"
        if key not in summary:
            continue
        ax.axvline(summary[key], color="#3b7d3a", linewidth=1.5, linestyle=":", label=f"{int(p)}th={summary[key]:.2f}")

    ax.set_title(title)
    ax.set_xlabel("Pixel intensity")
    ax.set_ylabel("Count")
    if log_y:
        ax.set_yscale("log")
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def plot_method_scatter(summary_df: pd.DataFrame, out_path: Path) -> None:
    if summary_df.empty:
        return
    fig, ax = plt.subplots(figsize=(7.5, 5.5), dpi=150)
    colors = summary_df["supports_mode_over_mean"].map({True: "#6a3fb5", False: "#b22222"}).fillna("#666666")
    ax.scatter(
        summary_df["pearson_skew"],
        summary_df["mode"] - summary_df["mean"],
        c=colors,
        alpha=0.8,
        edgecolors="none",
    )
    ax.axvline(0.25, color="#888888", linestyle="--", linewidth=1)
    ax.axhline(0.0, color="#888888", linestyle="--", linewidth=1)
    ax.set_xlabel("Pearson skew (3 * (mean - median) / std)")
    ax.set_ylabel("Mode - mean")
    ax.set_title("FOV-level evidence for mode-based background estimation")
    ax.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def choose_fovs_for_plotting(summary_df: pd.DataFrame, plot_all: bool, max_fov_plots: int) -> pd.DataFrame:
    if plot_all or len(summary_df) <= max_fov_plots:
        return summary_df.copy()
    ranked = summary_df.sort_values(
        by=["supports_mode_over_mean", "pearson_skew", "mean_minus_median"],
        ascending=[False, False, False],
    )
    return ranked.head(max_fov_plots).copy()


def analyze_experiment(
    exp_dir: Path,
    output_dir: Path,
    max_timepoints: int,
    channel_filter: str | None,
    comparison_percentiles: list[float],
    bins: int,
    sample_pixels_per_fov: int,
    sample_pixels_overall: int,
    max_fov_plots: int,
    plot_all_fovs: bool,
    log_y: bool,
    rng: np.random.Generator,
) -> tuple[pd.DataFrame, dict]:
    base_dir = exp_dir.parent.parent
    fov_df = process_experiment(
        exp_dir,
        base_dir,
        max_timepoints=max_timepoints,
        method="mode",
        channel_filter=channel_filter,
        percentile=None,
    )
    if fov_df.empty:
        return pd.DataFrame(), {}

    fov_output_dir = output_dir / exp_dir.name / "fov_histograms"
    fov_output_dir.mkdir(parents=True, exist_ok=True)
    overall_samples = []
    rows = []

    for _, row in fov_df.iterrows():
        image_paths = [Path(p) for p in str(row["image_paths"]).split(";") if p]
        arrays = []
        for img_path in image_paths:
            try:
                arrays.append(np.asarray(load_image(img_path), dtype=np.float64).ravel())
            except Exception as exc:
                print(f"[WARN] Failed to load {img_path}: {exc}", file=sys.stderr)
        if not arrays:
            continue

        values = np.concatenate(arrays)
        sample = sample_values(values, sample_pixels_per_fov, rng)
        overall_samples.append(sample_values(values, sample_pixels_overall, rng))
        summary = summarize_values(values, comparison_percentiles)
        summary_row = {
            "experiment": row["experiment"],
            "well": row["well"],
            "field": int(row["field"]),
            "n_images": int(row["n_images"]),
            "n_timepoints": int(row["n_timepoints"]),
            "timepoints_used": row["timepoints_used"],
            "image_paths": row["image_paths"],
        }
        summary_row.update(summary)
        rows.append(summary_row)

        png_name = f"{row['well']}_field_{int(row['field']):02d}_hist.png"
        summary_row["histogram_png"] = str((fov_output_dir / png_name).resolve())
        summary_row["_sample_values"] = sample

    summary_df = pd.DataFrame(rows)
    if summary_df.empty:
        return summary_df, {}

    plot_df = choose_fovs_for_plotting(summary_df, plot_all_fovs, max_fov_plots)
    for _, row in plot_df.iterrows():
        plot_histogram(
            row["_sample_values"],
            row.to_dict(),
            Path(row["histogram_png"]),
            title=f"{row['experiment']} | {row['well']} field {int(row['field'])}",
            bins=bins,
            log_y=log_y,
            comparison_percentiles=comparison_percentiles,
        )

    overall_values = np.concatenate(overall_samples) if overall_samples else np.array([], dtype=np.float64)
    overall_summary = summarize_values(overall_values, comparison_percentiles)
    overall_summary.update(
        {
            "experiment": exp_dir.name,
            "n_fovs": int(len(summary_df)),
            "fraction_supporting_mode": float(summary_df["supports_mode_over_mean"].mean()),
        }
    )
    overall_plot = output_dir / exp_dir.name / "overall_histogram.png"
    overall_plot.parent.mkdir(parents=True, exist_ok=True)
    plot_histogram(
        overall_values,
        overall_summary,
        overall_plot,
        title=f"{exp_dir.name} | pooled pixel distribution",
        bins=bins,
        log_y=log_y,
        comparison_percentiles=comparison_percentiles,
    )
    overall_summary["overall_histogram_png"] = str(overall_plot.resolve())

    summary_df = summary_df.drop(columns=["_sample_values"])
    return summary_df, overall_summary


def main() -> None:
    ap = argparse.ArgumentParser(
        description=(
            "Compare candidate background estimators using pixel-intensity distributions "
            "from the same baseline images used for FOV background correction."
        )
    )
    ap.add_argument("--root", default=".", help="Path to the data root (default: .)")
    ap.add_argument("--dates", nargs="*", help="Specific date folders to process (e.g., 081825 082125)")
    ap.add_argument("--all-dates", action="store_true", help="Process all 6-digit date folders under root")
    ap.add_argument("--experiment", help="Process only a specific experiment directory")
    ap.add_argument("--output-dir", type=Path, help="Directory for plots and CSV outputs")
    ap.add_argument("--max-timepoints", type=int, default=5, help="Number of Sequence 2 timepoints per FOV")
    ap.add_argument("--channel", help="Optional channel substring filter")
    ap.add_argument("--bins", type=int, default=120, help="Histogram bin count")
    ap.add_argument("--percentiles", nargs="*", type=float, default=[5.0, 10.0, 25.0], help="Candidate percentiles to compare")
    ap.add_argument("--sample-pixels-per-fov", type=int, default=200000, help="Max pixels per FOV histogram")
    ap.add_argument("--sample-pixels-overall", type=int, default=50000, help="Max sampled pixels contributed by each FOV to the pooled histogram")
    ap.add_argument("--max-fov-plots", type=int, default=24, help="How many per-FOV histogram PNGs to render when not plotting all FOVs")
    ap.add_argument("--plot-all-fovs", action="store_true", help="Render histogram PNGs for every FOV")
    ap.add_argument("--log-y", action="store_true", help="Use a log-scaled y-axis for histograms")
    ap.add_argument("--seed", type=int, default=42, help="Random seed for histogram downsampling")
    args = ap.parse_args()

    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        raise SystemExit(f"Root not found: {root}")

    exp_dirs = discover_experiment_dirs(root, args.dates, args.all_dates, args.experiment)
    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else (root / "background_method_report")
    output_dir.mkdir(parents=True, exist_ok=True)

    comparison_percentiles = sorted({float(p) for p in args.percentiles})
    rng = np.random.default_rng(args.seed)

    all_fov_summaries = []
    experiment_summaries = []
    for exp_dir in exp_dirs:
        print(f"\n[background-report] Processing {exp_dir.name}")
        fov_summary_df, exp_summary = analyze_experiment(
            exp_dir=exp_dir,
            output_dir=output_dir,
            max_timepoints=args.max_timepoints,
            channel_filter=args.channel,
            comparison_percentiles=comparison_percentiles,
            bins=args.bins,
            sample_pixels_per_fov=args.sample_pixels_per_fov,
            sample_pixels_overall=args.sample_pixels_overall,
            max_fov_plots=args.max_fov_plots,
            plot_all_fovs=args.plot_all_fovs,
            log_y=args.log_y,
            rng=rng,
        )
        if fov_summary_df.empty:
            print(f"[background-report] No usable FOVs found for {exp_dir.name}")
            continue

        fov_csv = output_dir / exp_dir.name / "background_method_comparison_by_fov.csv"
        fov_summary_df.to_csv(fov_csv, index=False)
        all_fov_summaries.append(fov_summary_df.assign(source_csv=str(fov_csv.resolve())))
        experiment_summaries.append(exp_summary | {"comparison_csv": str(fov_csv.resolve())})
        print(f"[background-report] Wrote {fov_csv}")

    if not all_fov_summaries:
        raise SystemExit("No report outputs were generated.")

    all_fov_df = pd.concat(all_fov_summaries, ignore_index=True)
    all_fov_out = output_dir / "background_method_comparison_all_fovs.csv"
    all_fov_df.to_csv(all_fov_out, index=False)

    exp_summary_df = pd.DataFrame(experiment_summaries)
    exp_summary_out = output_dir / "background_method_summary_by_experiment.csv"
    exp_summary_df.to_csv(exp_summary_out, index=False)
    plot_method_scatter(all_fov_df, output_dir / "mode_vs_mean_evidence.png")

    print(f"\n[OK] Wrote per-FOV summary: {all_fov_out}")
    print(f"[OK] Wrote experiment summary: {exp_summary_out}")
    if not exp_summary_df.empty:
        supported = exp_summary_df["fraction_supporting_mode"].dropna()
        if not supported.empty:
            print(
                "[background-report] Fraction of FOVs supporting mode over mean "
                f"(heuristic): mean={supported.mean():.3f}, min={supported.min():.3f}, max={supported.max():.3f}"
            )


if __name__ == "__main__":
    main()
