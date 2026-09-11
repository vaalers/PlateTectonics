# PTHTS CLI Reference

Complete argument reference for all command-line tools in the PTHTS pipeline.

---

## Table of Contents

0. [Example data — make_example_data.py](#example-data--toolsmake_example_datapy)
0. [pt-gui — Graphical Interface](#pt-gui--graphical-interface)
1. [pt-run — Main Pipeline](#pt-run--main-pipeline)
2. [run_all_dates.bat — Batch Runner](#run_all_datesbat--batch-runner)
3. [filter_post_stats — QC Filtering](#filter_post_stats--qc-filtering)
4. [per_object_ff0 — F/F0 Analysis](#per_object_ff0--ff0-analysis)
5. [spaghetti_plot_per_well — Spaghetti Plots](#spaghetti_plot_per_well--spaghetti-plots)
6. [combine_date_summaries — Summary Merger](#combine_date_summaries--summary-merger)
7. [compute_fov_background — Background Computation](#compute_fov_background--background-computation)
8. [pt-reorg — Folder Reorganization](#pt-reorg--folder-reorganization)
9. [build-sample-manifest — Sample Manifest](#build-sample-manifest--sample-manifest)
10. [build-plate-overview — Plate Overview](#build-plate-overview--plate-overview)


---

## Example data — `tools/make_example_data.py`

Writes a small synthetic Harmony export (six wells, 27 timepoints, baseline → Stim 1 → Stim 2) that
exercises every pipeline step. Nothing in it comes from real measurements.

```bat
python tools/make_example_data.py                 REM examples/example_data (text exports only)
python tools/make_example_data.py --with-images   REM also writes tiny TIFF frames for --compute-backgrounds
python tools/make_example_data.py --out D:\demo --date 010126 --seed 3 --clean
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--out` | `examples/example_data` | Output root; the date folder is written under `<out>/<20YY>/<MMDDYY>`. |
| `--date` | `090826` | Date code (MMDDYY) for the generated date folder. |
| `--seed` | `0` | Random seed; the same seed always yields the same files. |
| `--with-images` | off | Also write synthetic 16-bit TIFF frames named like Phenix exports. |
| `--image-size` | `64` | Pixel size of the square synthetic frames. |
| `--clean` | off | Delete the date folder first (removes previous pipeline outputs too). |

Then: `pt-run examples/example_data/2026/090826 --expected-n 27`. See [examples/README.md](../examples/README.md).

---

## pt-gui — Graphical Interface

Starts a local Streamlit web app that wraps `pt-run`, `pt-reorg`, `build-sample-manifest` and
`build-plate-overview` with forms, a live log, and a results browser.

```bat
pt-gui [streamlit options]
pt-gui --server.port 8502
```

Any arguments are passed to `streamlit run`. `run_gui.bat` / `run_gui.sh` launch it inside the
`pt-py310` environment. The GUI always displays the equivalent command line before running it.

Unlike the bare `pt-run` command, the GUI treats background correction as required: a run must either
compute backgrounds from raw images (`--compute-backgrounds --raw-data-root …`), name an existing CSV
(`--background-csv`), or use a `<date>_fov_backgrounds_avg.csv` already present in each selected date folder.

---

## pt-run — Main Pipeline

Orchestrates the full pipeline: Phenix → Excel → F/F0 → QC filter → spaghetti plots → date summary.

```bat
pt-run <path> [<path> ...] [options]
```

`<path>` can be a 6-digit date folder, an `Analysis/` directory, or an `Experiment_*` directory.

### Input / Discovery

| Argument | Default | Description |
|----------|---------|-------------|
| `--objects-population` | *(first found)* | Substring of the Harmony population name to use when an Evaluation folder holds several `Objects_Population - <name>.txt` exports. Also settable via `PT_OBJECTS_POPULATION`. |
| `paths` *(positional)* | *(required)* | One or more paths to process. Can be a date folder (`081825`), an `Analysis/` directory, or an `Experiment_*` directory. |
| `--root` | `.` | Root folder used by `--reorg-first` and `--compute-backgrounds` when `--dates` is also provided. |
| `--dates` | *(none)* | Limit processing to specific date folders, e.g. `--dates 081825 082125`. Used together with `--root`. |

### Analysis Parameters

| Argument | Default | Description |
|----------|---------|-------------|
| `--stim` | `10` | Stimulus timepoint number, passed to `per_object_ff0`. |
| `--baseline-n` | `5` | Number of pre-stimulus timepoints used to compute F0. |
| `--ylim YMIN YMAX` | `-0.5 7.0` | Y-axis limits for all F/F0 plots. |
| `--expected-n` | `20` | Exact expected timepoints per object. Objects with a different count are flagged by `filter_post_stats`. Ignored when `--min-n` is set. |
| `--min-n` | *(none)* | Minimum acceptable timepoints per object (relaxed mode). When set, objects pass as long as they have **at least** this many timepoints. Use this when dates have different timepoint counts (e.g. 20, 25, or 27). |
| `--per-page` | `20` | Number of objects per faceted PNG page. |

### Plot Controls

| Argument | Default | Description |
|----------|---------|-------------|
| `--pptx` / `--no-pptx` | on | Generate a PPTX presentation alongside the faceted PNGs. |
| `--ff0-no-plots` | off | Disable **all** plotting in `per_object_ff0` (no PNGs, no PPTX). |
| `--ff0-no-single-pngs` | off | Disable individual object PNGs while keeping faceted pages and PPTX. |
| `--ff0-no-iqr-filter` | off | Disable size and intensity IQR filtering before plotting. |
| `--no-spaghetti` | off | Skip spaghetti plot generation entirely. |
| `--spaghetti-both` | on | Write both raw and filtered spaghetti plots into separate subfolders. |

### Step Skip Flags

| Argument | Default | Description |
|----------|---------|-------------|
| `--no-phenix` | off | Skip the Phenix → Excel conversion step (use when `.xlsx` files already exist). |
| `--no-ff0` | off | Skip F/F0 analysis and QC filtering. |
| `--no-spaghetti` | off | Skip spaghetti plots. |
| `--no-combine-date-summaries` | off | Skip the final step that merges all experiment summaries per date. |

### Background Correction

| Argument | Default | Description |
|----------|---------|-------------|
| `--compute-backgrounds` | off | Compute per-FOV backgrounds from Sequence 2 images before analysis. |
| `--background-method` | `mode` | Method used when computing backgrounds: `mode`, `median`, `mean`, or `percentile`. |
| `--background-timepoints` | `20` | Max timepoints per phase to use for background. |
| `--background-channel` | *(none)* | Filter background images by channel name (e.g., 'Alexa 488'). |
| `--background-csv` | *(none)* | Path to existing background CSV file (skips computation if provided). |
| `--raw-data-root` | *(none)* | Root of raw image data; required with `--compute-backgrounds`. Date folders live under `<root>\20YY\MMDDYY`. |

### Data Reorganization (Optional Pre-step)

| Argument | Default | Description |
|----------|---------|-------------|
| `--reorg-first` | off | Run `phenix_reorg` on the data before the pipeline starts. |
| `--reorg-apply` | off | Apply the reorganization changes. Without this flag, `phenix_reorg` runs as a dry-run. |
| `--reorg-all-dates` | off | When `--dates` is not provided, reorganize all date folders under `--root`. |

### Execution

| Argument | Default | Description |
|----------|---------|-------------|
| `--threads` | `4` | Number of parallel worker threads for per-evaluation tasks. |
| `--timeout` | `7200` | Subprocess timeout in seconds (default 2 hours). Increase for very slow cloud-sync folders. |
| `--dry-run` | off | Print all commands that would run without executing them. |
| `--strict-phenix` | off | Fail immediately if the Phenix → Excel step cannot produce any output for an experiment. |
| `--verbose` / `-v` | off | Print detailed progress messages (helpful when debugging hangs on cloud-sync folders). |
| `--pipeline-log` | auto | Path to write the full pipeline log. Default: `run_pt_pipeline_<YYYYMMDD_HHMMSS>.log` in the current directory. |
| `--python-bin` | `python3` | Python interpreter used to launch sub-processes. |
| `--scripts-dir` | *(none)* | Directory containing the pipeline scripts if they are not installed as package entry points. |
| `--background-timepoints` | `20` | Max timepoints per phase to use for background. |

### Examples

```bat
REM Basic run for one date
pt-run "D:\PhenixData\Analyzed\081825"

REM Custom stimulus timepoint, relaxed timepoint filter
pt-run "D:\PhenixData\Analyzed\081825" --stim 15 --min-n 18

REM Skip Excel generation (already done), use existing backgrounds
pt-run "D:\PhenixData\Analyzed\081825" --no-phenix --background-csv 081825_fov_backgrounds.csv

REM Process with FOV background computation
pt-run "D:\PhenixData\Analyzed\081825" --compute-backgrounds --background-method median

REM Dry run to preview all commands
pt-run "D:\PhenixData\Analyzed\081825" --dry-run --verbose

REM Reorganize raw data then run pipeline
pt-run "D:\PhenixData\Analyzed\081825" --reorg-first --reorg-apply
```

---

## run_all_dates.bat — Batch Runner

Finds every 6-digit date folder directly inside a root directory and runs `pt-run` on each one sequentially. Reports which dates succeeded and which failed.

```bat
run_all_dates.bat <root_folder> [extra pt-run args...]
```

| Argument | Description |
|----------|-------------|
| `<root_folder>` | *(required)* Path containing 6-digit date subfolders. |
| `[extra args]` | Any additional arguments are forwarded to `pt-run` for every date. |

### Examples

```bat
REM Process all dates with defaults
run_all_dates.bat "D:\PhenixData\Analyzed"

REM Process all dates, skip Excel generation, relaxed timepoints
run_all_dates.bat "D:\PhenixData\Analyzed" --no-phenix --min-n 18

REM Dry run across all dates
run_all_dates.bat "D:\PhenixData\Analyzed" --dry-run
```

---

## filter_post_stats — QC Filtering

Reads an augmented `*_with_obj.xlsx` workbook produced by `per_object_ff0` and writes a filtered copy containing only objects that pass all QC rules.

```bat
conda run -n pt-py310 python -m pt.filter_post_stats <xlsx> [options]
```

*This tool is called automatically by `pt-run`. Run it manually only to re-filter with different thresholds.*

| Argument | Default | Description |
|----------|---------|-------------|
| `xlsx` *(positional)* | *(required)* | Path to `*_with_obj.xlsx` augmented workbook. |
| `--out` | auto | Output path. Default: `<stem>_filtered_objects.xlsx` in the same directory. |
| `--expected-n` | `27` | Exact expected timepoints per object. Objects with a different count are excluded. |
| `--min-n` | *(none)* | Minimum acceptable timepoints (relaxed). Overrides `--expected-n`. Objects with fewer than `min-n` timepoints are excluded; those with more pass. |
| `--peak-threshold` | `2.0` | Minimum `peak_global_ff0` value. Objects below this are excluded. |
| `--baseline-sigma` | `2.0` | Number of standard deviations above which a baseline point is an outlier. |
| `--baseline-abs` | `1.9` | Absolute F_corr threshold for baseline outlier detection. |
| `--baseline-outlier-frac` | `0.5` | Exclude object if more than this fraction of baseline points are outliers. |
| `--max-ff0-nonfinite` | `2` | Maximum non-finite F_over_F0 values allowed per object. |
| `--max-ff0-negative` | `2` | Maximum negative F_over_F0 values allowed per object. |
| `--max-fcorr-negative` | `2` | Maximum negative F_corr values allowed per object. |
| `--baseline-slope-threshold` | `0.02` | Flag baseline as unstable if relative change between consecutive points exceeds this fraction (2% default). |
| `--baseline-n-default` | `5` | Fallback number of baseline timepoints when not embedded in the workbook. |
| `--responders-only` | off | Keep only objects where `obj_responder_general` is true, then apply QC. |
| `--lax-qc` | off | Use more permissive thresholds (lower peak minimum, higher outlier tolerances). |

---

## per_object_ff0 — F/F0 Analysis

Computes per-object F/F0 with optional background correction, IQR-based outlier removal, and generates faceted trace plots and PPTX presentations.

```bat
conda run -n pt-py310 python -m pt.per_object_ff0 <xlsx> [options]
```

*Called automatically by `pt-run`. Run manually to re-process a single workbook.*

| Argument | Default | Description |
|----------|---------|-------------|
| `xlsx` *(positional)* | *(required)* | Per-well Excel workbook from `phenix-to-xlsx-batch`. |
| `--outdir` | `<xlsx_dir>/plots/individual traces` | Output directory for all generated files. |
| `--background` | `0.0` | Global background value subtracted from all intensities. |
| `--background-csv` | *(none)* | CSV with FOV-specific backgrounds (`well, field, background`). Takes precedence over `--background`. |
| `--stim` | *(none)* | Stimulus timepoint. Used to define the statistics window and responder classification. |
| `--baseline-n` | `5` | Number of pre-stimulus timepoints for F0 computation. |
| `--stats-window` | `30.0` | Duration in seconds of the post-stimulus window for peak/AUC statistics. |
| `--ylim YMIN YMAX` | `-0.5 5.0` | Y-axis limits for all trace plots. |
| `--per-page` | `20` | Objects per faceted PNG page. |
| `--ncols` | `5` | Columns in the faceted grid. |
| `--no-iqr-filter` | off | Disable size and intensity IQR filtering. |
| `--size-iqr-k` | `1.5` | IQR multiplier for size-based outlier removal. |
| `--intensity-iqr-k` | `1.5` | IQR multiplier for intensity-based outlier removal. |
| `--scope` | `timepoint` | IQR filtering scope: `timepoint` (per frame) or `well` (across entire well). |
| `--no-plots` | off | Skip all plotting. |
| `--no-single-pngs` | off | Skip individual object PNGs (keep faceted pages). |
| `--pptx` | off | Build a PPTX presentation with one slide per facet page. |
| `--min-points` | `2` | Minimum number of valid timepoints required to include an object. |
| `--xlsx-out` | auto | Write augmented workbook with F_corr, F_over_F0, and Object_Stats sheet. |

---

## spaghetti_plot_per_well — Spaghetti Plots

Generates per-well F/F0 overlay plots showing individual object traces or mean ± error.

```bat
conda run -n pt-py310 python -m pt.spaghetti_plot_per_well <xlsx> [options]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `xlsx` *(positional)* | *(required)* | Per-well Excel workbook. |
| `--outdir` | `<xlsx_dir>/plots` | Output directory for PNGs. |
| `--mode` | `per-object` | Plot style: `per-object` (individual traces) or `mean` (mean ± error band). |
| `--both` | off | Also generate plots for the filtered workbook if it exists alongside the raw one. |
| `--ylim` | `-0.5,7.0` | Y-axis limits as a comma-separated string, e.g. `-0.5,10.0`. |
| `--baseline-n` | `3` | Number of baseline timepoints for normalization. |
| `--error` | `sem` | Error type for mean mode: `sem` (standard error) or `sd` (standard deviation). |
| `--alpha-lines` | `0.35` | Transparency of individual trace lines. |
| `--linewidth` | `0.9` | Width of individual trace lines. |
| `--overlay-mean` | off | Overlay mean trace on per-object plots. |
| `--background-csv` | *(none)* | CSV with FOV-specific backgrounds (`well, field, background`). |
| `--background` | `0.0` | Global background value. |
| `--no-iqr-filter` | off | Disable IQR-based outlier filtering before plotting. |
| `--facet` | off | Create a single faceted PNG with all wells as subplots. |
| `--no-per-fov` | off | Skip per-FOV plots (per-object mode only). |
| `--same-ylim` | off | Use a consistent y-axis range across all wells. |

---

## combine_date_summaries — Summary Merger

Merges summary sheets across all experiments within a date into consolidated workbooks.

```bat
conda run -n pt-py310 python -m pt.combine_date_summaries <date> [<date> ...] [--root <path>]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `dates` *(positional)* | *(required)* | One or more 6-digit date folders to merge. |
| `--root` | `.` | Root folder containing the date subdirectories. |

**Output:** Combined `*_with_obj.xlsx` and `*_filtered_objects.xlsx` workbooks at the date level.

---

## compute_fov_background — Background Computation

Estimates per-FOV background signal from baseline images (Sequence 2) for use in downstream background correction.

```bat
conda run -n pt-py310 python -m pt.compute_fov_background [options]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--root` | `.` | Root path containing date folders. |
| `--dates` | *(none)* | Specific date folders to process. |
| `--all-dates` | off | Process all 6-digit date folders found under `--root`. |
| `--output` / `-o` | `<date>_fov_backgrounds.csv` | Output CSV file path. |
| `--max-timepoints` | `5` | Number of early Sequence 2 frames to average. |
| `--method` | `mode` | Estimation method: `mode`, `median`, `mean`, or `percentile`. |
| `--percentile` | `10.0` | Percentile value when `--method percentile` is used. Lower values exclude brighter pixels. |
| `--channel` | *(none)* | Restrict to a specific channel name, e.g. `Alexa 488`. |
| `--experiment` | *(none)* | Process only a specific experiment directory. |

**Output:** CSV with columns `well`, `field`, `background`.

---

## pt-reorg — Folder Reorganization

Renames and reorganizes raw Opera Phenix output folders into the structure expected by `pt-run`.

```bat
pt-reorg [options]
REM  (equivalent: python -m pt.phenix_reorg [options])
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--root` | `.` | Root containing date folders. |
| `--dates` | *(none)* | Specific date folders to reorganize. |
| `--all-dates` | off | Reorganize all 6-digit date folders under `--root`. |
| `--apply` | off | **Required to make changes.** Without this flag the script runs as a dry-run only. |
| `--mirror-root` | off | Also normalize experiment folder names at the date root level (not only inside `Analysis/`). |
| `--roi-mode` | `sibling` | How to create per-well image folders: `sibling` creates `<DIR>_E11` folders, `subdir` creates `<DIR>/E11` subfolders. |
| `--well-source` | `filename` | How to determine well assignments: `filename` (parse image filenames) or `indexfile` (read `indexfile.txt`). |
| `--fov-subdirs` | off | Organize images by FOV within each well folder (`Well/F1/`, `Well/F2/`, etc.). |
| `--overwrite` | off | Allow overwriting existing target folders. |
| `--consolidate-roi-siblings` | off | Move sibling well folders into their parent and remove the now-empty siblings. |

---

## build-sample-manifest — Sample Manifest

Builds a consolidated sample manifest from plate layout files and analysis outputs.

```bat
build-sample-manifest [options]
REM  or via the wrapper:
run_build_sample_manifest.bat <layout_root> <analysis_root> [output_path]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `--layout-root` | *(required)* | Directory containing plate layout `.xlsx` files. |
| `--supplemental-layout-root` | *(none)* | Optional directory of supplemental plate-layout workbooks (cell plate #, compound plate #, culture preparation date). |
| `--analysis-root` | *(required)* | One or more roots containing `<date>/Analysis/*filtered_summary_plate*.xlsx` files, searched in order (accepts multiple paths, e.g. `--analysis-root A B`). |
| `--output` | `sample_manifest.xlsx` | Output manifest workbook path. |
| `--cell-types` | *(any)* | Optional comma-separated controlled vocabulary for cell-type labels; labels outside it become `Null`. |
| `--protocol-version` | `1` | Protocol version label written to every manifest row. |

---

## build-plate-overview — Plate Overview

Generates a spatial, color-coded 96-well plate map from a plate layout workbook. The result is an
Excel (.xlsx) workbook containing a visual grid of the plate plus a flattened data table, making it
easy to identify sample locations, stimuli, and cell types.

**Note:** This script produces an **Excel workbook**, not an image file (PNG/JPG).

```bat
build-plate-overview <xlsx> [-o <output>]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `xlsx` *(positional)* | *(required)* | Path to the source plate layout workbook. |
| `-o` / `--output` | `<input>_with_overview.xlsx` | Output workbook path. The source workbook is copied and two sheets are added. |

**Output Details:**
- **Overview Sheet**: A color-coded 96-well grid (A1-H12) where each cell contains the Sample ID, Cell Type and any Stim 1 / Stim 2 labels (multi-line). Cells are colored by cell type (one pastel fill per distinct label); control wells are highlighted in bold amber text.
- **Overview_Data Sheet**: A flattened table version of the layout data for easier programmatic access or filtering.

---
