# Filtering, Math, and Code References By Script

> Code references (`file.py:line`) are approximate and may drift as the code evolves; search for the named function or column instead of relying on the line number.

This document summarizes filtering criteria, key calculations, and implementation references for scripts under `src/pt`.

## `build_plate_manifest.py`
### Filtering
- Skips temporary layout files (`~$*`).
- Skips wells with missing row/column labels or empty raw cell content.
- Normalizes cell type to upper case; restricts to the `--cell-types` vocabulary when given; empty values become `Null`.
- Drops prior manifest rows with missing/non-numeric `TEST WELL` when reusing IDs.
- Sets `Calculate: Meets criteria to use for data?` by `general_responders > 20`.
- Plate-level QC forces criteria false if plate mean `% respond to STIM 1 >= 80`.
### Math
- `% respond to STIM 1 = (stim1_responders / n_objects) * 100`, else `0` when denominator `<= 0`.
- Positive response criterion is groupwise `all()` over same `DATE + PLATE ID + SAMPLE ID`.
### Code References
- `src/pt/build_plate_manifest.py:56`
- `src/pt/build_plate_manifest.py:338`
- `src/pt/build_plate_manifest.py:347`
- `src/pt/build_plate_manifest.py:467`

## `combine_date_summaries.py`
### Filtering
- Ignores temp files (`~$*`).
- Splits groups when source wells overlap.
- Drops blank/NA wells during extraction and pivot prep.
- `Filter_Fail_Pivot` excludes blank wells/reasons.
### Math
- `Filter_Fail_Pivot` uses `nunique(Well)` per `Reason`.
- Stim1/general bar uses `stim1_responders / general_responders * 100`.
### Code References
- `src/pt/combine_date_summaries.py:134`
- `src/pt/combine_date_summaries.py:156`
- `src/pt/combine_date_summaries.py:620`
- `src/pt/combine_date_summaries.py:638`

## `compute_fov_background.py`
### Filtering
- Uses only `Sequence == 2` rows.
- Optional case-insensitive channel substring filter.
- Drops rows missing numeric row/column/field/timepoint.
- Uses first `N` unique timepoints per `(well, field)`.
### Math
- Background method is one of `median`, `mean`, or `percentile(p)` (default `p=10`).
- Also computes `mean`, `std`, `min`, `max` over selected pixels.
### Code References
- `src/pt/compute_fov_background.py:275`
- `src/pt/compute_fov_background.py:291`
- `src/pt/compute_fov_background.py:308`
- `src/pt/compute_fov_background.py:321`
- `src/pt/compute_fov_background.py:390`

## `create_example_plots.py`
### Filtering
- No data filtering; synthetic data generation only.
### Math
- Uses Dirichlet-sampled proportions that sum to `1.0` per stacked bar.
### Code References
- `src/pt/create_example_plots.py`

## `detect_stim_times.py`
### Filtering
- Requires conda env `pt-py310`.
- Skips first sheet by default and sheets in `EXCLUDE_SHEETS`.
### Math
- Stim points: first timepoint for each sequence `>= 3`.
- Baseline range from sequence `== 2` min/max timepoint.
### Code References
- `src/pt/detect_stim_times.py:11`
- `src/pt/detect_stim_times.py:177`
- `src/pt/detect_stim_times.py:179`
- `src/pt/detect_stim_times.py:236`
- `src/pt/detect_stim_times.py:242`

## `filter_post_stats.py`
### Filtering
- Optional `--responders-only` drops non-general responders.
- Requires all rows in object to have `filter_reason == kept` if present.
- Enforces max counts for nonfinite/negative `F_over_F0` and negative `F_corr`.
- Enforces expected timepoint count (`--expected-n`, default `27`).
- Enforces peak threshold (`peak_global_ff0 > --peak-threshold`).
- Drops object when baseline outlier fraction exceeds cutoff.
### Math
- Outlier threshold: `well_base_mean + baseline_sigma * well_base_std`.
- Outlier point: `(F_over_F0 > threshold) OR (F_over_F0 > baseline_abs)`.
- Exclude if `outlier_fraction > baseline_outlier_frac`.
### Code References
- `src/pt/filter_post_stats.py:127`
- `src/pt/filter_post_stats.py:128`
- `src/pt/filter_post_stats.py:132`
- `src/pt/filter_post_stats.py:145`
- `src/pt/filter_post_stats.py:283`
- `src/pt/filter_post_stats.py:285`
- `src/pt/filter_post_stats.py:294`
- `src/pt/filter_post_stats.py:343`

## `per_object_ff0.py`
### Filtering
- Drops rows missing time/intensity/field/object id.
- Applies IQR filtering to area and intensity.
- Drops objects with too few points (`min_points`) or invalid `F0`.
### Math
- IQR bounds: `Q1 - k*IQR` to `Q3 + k*IQR`.
- `F_corr = raw - background`.
- `F_over_F0 = F_corr / F0`.
- General responder threshold: `well_base_mean + 2*well_base_std`.
- General responder: `peak_global_ff0 >= baseline_threshold`.
- Stim1 responder requires general responder + `stim1_peak >= 2.0` + baseline-within-well check.
- Stim2 responder requires peak above both `baseline_threshold` and `pre_mean + 2*pre_std`; marks ambiguous when `pre_high_frac >= 0.5`.
- AUC uses trapezoidal integral of `(F_over_F0 - 1.0)` in stats window.
### Code References
- `src/pt/per_object_ff0.py:249`
- `src/pt/per_object_ff0.py:348`
- `src/pt/per_object_ff0.py:349`
- `src/pt/per_object_ff0.py:480`
- `src/pt/per_object_ff0.py:527`
- `src/pt/per_object_ff0.py:553`
- `src/pt/per_object_ff0.py:567`
- `src/pt/per_object_ff0.py:581`

## `per_object_ff0_plots.py`
### Filtering
- Requires time/field/object/F_over_F0 columns and drops NA rows before plotting.
### Math
- No new analysis math; visualization only.
### Code References
- `src/pt/per_object_ff0_plots.py:141`
- `src/pt/per_object_ff0_plots.py:149`

## `phenix_plots_from_xlsx.py`
### Filtering
- Drops rows missing numeric time/intensity.
- Applies area IQR (two-sided) and intensity IQR (upper-only).
### Math
- Baseline pre-stim normalization to `F/F0`.
- Error band uses `SEM = std/sqrt(n)` or SD.
- AUC by trapezoidal integral over stim window.
### Code References
- `src/pt/phenix_plots_from_xlsx.py:139`
- `src/pt/phenix_plots_from_xlsx.py:143`
- `src/pt/phenix_plots_from_xlsx.py:144`
- `src/pt/phenix_plots_from_xlsx.py:264`
- `src/pt/phenix_plots_from_xlsx.py:265`

## `phenix_to_xlsx.py`
### Filtering
- No biological filtering; parser/merger workflow.
- Skips existing outputs unless `--overwrite`.
### Math
- `Plate_Overview` aggregation uses one of: `median`, `mean`, `sum`, `max`, `min`.
### Code References
- `src/pt/phenix_to_xlsx.py:115`
- `src/pt/phenix_to_xlsx.py:213`

## `phenix_to_xlsx_batch.py`
### Filtering
- Directory inclusion controlled by experiment/evaluation regexes.
- Requires raw pair files to build.
- Skips when output workbook already exists.
### Math
- Uses grouped summary/count style plate overview fallback logic.
### Code References
- `src/pt/phenix_to_xlsx_batch.py:32`
- `src/pt/phenix_to_xlsx_batch.py:33`
- `src/pt/phenix_to_xlsx_batch.py:154`
- `src/pt/phenix_to_xlsx_batch.py:166`
- `src/pt/phenix_to_xlsx_batch.py:168`

## `responder_bar_charts.py`
### Filtering
- Prefers filtered workbook when available.
- Drops blank wells and rows with missing/non-numeric field where needed.
### Math
- Stim2 %: `stim2_responders / (stim2_responders + stim2_ambiguous) * 100`.
- Stim1 %: `stim1_responders / (stim1_responders + stim1_nonresponders) * 100`.
- Quotient %: `stim1_responders / general_responders * 100`.
- Category/FOV bars use `count / total` fractions.
### Code References
- `src/pt/responder_bar_charts.py:66`
- `src/pt/responder_bar_charts.py:225`
- `src/pt/responder_bar_charts.py:233`
- `src/pt/responder_bar_charts.py:503`

## `pt_tokenize.py`
### Filtering
- No PT-specific filtering logic.
### Math
- No PT analysis math.
### Code References
- `src/pt/pt_tokenize.py`

## `run_pt_pipeline.py`
### Filtering
- Includes dirs matching experiment/evaluation regex.
- Ignores Excel lockfiles (`~$*`).
- Prefers filtered workbook when present.
### Math
- Orchestration only; passes numeric thresholds to downstream scripts.
### Code References
- `src/pt/run_pt_pipeline.py:11`
- `src/pt/run_pt_pipeline.py:12`
- `src/pt/run_pt_pipeline.py:55`
- `src/pt/run_pt_pipeline.py:452`
- `src/pt/run_pt_pipeline.py:680`
- `src/pt/run_pt_pipeline.py:681`
- `src/pt/run_pt_pipeline.py:683`

## `phenix_reorg.py`
### Filtering
- Date folders limited to six-digit names (or explicit list).
- GUID and experiment matching via regex.
- Image split only for parseable `.tif/.tiff` filenames.
- Recursive image scan excludes Analysis and already-binned directories.
### Math
- No responder/statistical math.
### Code References
- `src/pt/phenix_reorg.py:12`
- `src/pt/phenix_reorg.py:21`
- `src/pt/phenix_reorg.py:25`
- `src/pt/phenix_reorg.py:47`
- `src/pt/phenix_reorg.py:105`
- `src/pt/phenix_reorg.py:358`

## `spaghetti_plot_filtered.py`
### Filtering
- Starts from `spaghetti_plot_per_well` preprocessing filters.
- Requires pre-stim data when stim1 exists.
- Requires full timepoint coverage unless `--allow-gaps`.
- Drops traces with excessive stim2-window negatives.
### Math
- Baseline bounds: `mean_baseline ± baseline_sd_k * sd_baseline`.
- Negative fraction: `mean(F_over_F0 < stim2_below)`; drop if `> stim2_neg_frac`.
### Code References
- `src/pt/spaghetti_plot_filtered.py:61`
- `src/pt/spaghetti_plot_filtered.py:85`
- `src/pt/spaghetti_plot_filtered.py:91`
- `src/pt/spaghetti_plot_filtered.py:107`
- `src/pt/spaghetti_plot_filtered.py:108`
- `src/pt/spaghetti_plot_filtered.py:272`

## `spaghetti_plot_per_well.py`
### Filtering
- Drops rows missing numeric time/intensity.
- Applies area IQR (two-sided) and intensity IQR (upper-only).
- Skips well if all rows are filtered out.
### Math
- `F_corr = raw - background`; `F_over_F0 = F_corr / F0`.
- Mean-mode errors use `SEM` or `SD`.
### Code References
- `src/pt/spaghetti_plot_per_well.py:326`
- `src/pt/spaghetti_plot_per_well.py:347`
- `src/pt/spaghetti_plot_per_well.py:352`
- `src/pt/spaghetti_plot_per_well.py:353`
- `src/pt/spaghetti_plot_per_well.py:714`
- `src/pt/spaghetti_plot_per_well.py:715`

## `pt_utils.py`
### Filtering
- Utility-only helpers; no primary pipeline filtering stage.
### Math
- Delimiter scoring uses median delimiter counts.
- Stim extraction and sequence mode helpers support downstream calculations.
### Code References
- `src/pt/pt_utils.py:97`
- `src/pt/pt_utils.py:109`
- `src/pt/pt_utils.py:145`
- `src/pt/pt_utils.py:197`

## `__init__.py`
### Filtering
- No filtering logic.
### Math
- No math.
### Code References
- `src/pt/__init__.py:1`
