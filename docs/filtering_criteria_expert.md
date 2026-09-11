# PT Pipeline — Filtering Criteria and Quantitative Calculations
## For Fluorescence Imaging Users

This document describes the data flow, filtering logic, and quantitative calculations used in the PT analysis pipeline. It is written for readers with a background in calcium imaging and high-content fluorescence microscopy who have not seen the underlying analysis code. Every decision point is described in terms of the biological or technical reasoning behind it, alongside the mathematical operations performed.

Scripts are presented in the order they are executed by the automated pipeline. Scripts run outside the automated pipeline are listed in Part 2.

---

## Notation Reference

| Symbol | Meaning |
|---|---|
| $y_i$ | Raw fluorescence intensity at timepoint $i$ (arbitrary units, a.u.) |
| $bg$ | Background fluorescence offset for a given well + field of view |
| $F_{\text{corr},i}$ | Background-corrected fluorescence at timepoint $i$ |
| $F_0$ | Per-object resting baseline fluorescence (mean corrected intensity over baseline timepoints) |
| $F/F_0$ | Normalized fluorescence ratio (dimensionless); equals ~1.0 at rest |
| $\mu_W$, $\sigma_W$ | Well-level mean and SD of baseline $F/F_0$ values across all accepted objects |
| $\bar{b}$, $s_b$ | Per-object mean and SD of $F/F_0$ during the pre-stimulation baseline window |
| $\mu_{\text{pre}}$, $\sigma_{\text{pre}}$ | Mean and SD of $F/F_0$ in the local window immediately before Stim 2 onset |
| $Q_1$, $Q_3$ | First and third quartiles of a distribution |
| $\text{IQR}$ | Interquartile range: $\text{IQR} = Q_3 - Q_1$ |
| $t_{\text{stim1}}$, $t_{\text{stim2}}$ | Absolute time (seconds) of Stim 1 and Stim 2 onsets |
| $\mathcal{T}_{\text{base}}$ | Set of baseline timepoints (the last $N$ timepoints before $t_{\text{stim1}}$) |
| $n_{\text{win}}$ | Number of timepoints within the post-stimulation analysis window |
| $\Delta t_{\text{win}}$ | Duration of the post-stimulation analysis window (default: 30 s) |
| $N$ | Number of baseline timepoints used to compute $F_0$ (default: 5) |
| $k$ | IQR multiplier used for outlier filtering (default: 1.5) |

---

## Part 1: Scripts Run by the Automated Pipeline

---

### Pre-Pipeline Step (Manual): `phenix_reorg.py` — File Reorganization

Raw Harmony exports use an inconsistent directory structure that varies between imaging sessions and software versions. This pre-processing step renames and reorganizes files into a standardized layout (date folders in MMDDYY format) so that all downstream scripts can reliably locate input files.

No quantitative filtering is performed here. This step must be run manually by the analyst before the pipeline is launched.

---

### Step 1 (Optional): `compute_fov_background.py` — Field-of-View Background Estimation

#### Purpose

Even after dye washout, residual intracellular dye, non-cellular autofluorescence, and optical scattering produce a non-zero fluorescence offset in areas containing no cells. This offset varies spatially across the plate — different fields of view within the same well, and different wells, can have meaningfully different background levels due to illumination inhomogeneity and local differences in dye retention.

Subtracting a single plate-wide background value would introduce systematic errors into the corrected intensities. This step therefore estimates a separate background value for each (well, field) combination.

#### Data Selection

Only images from Sequence 2 (the pre-stimulation baseline phase) are used. A cell's resting fluorescence is relatively stable during this window, making it the most reliable period for background characterization. The first $N = 5$ unique timepoints per (well, field) combination are selected. Any row missing a well identifier, field number, or timepoint is excluded from background estimation.

#### Background Estimation Methods

The **default method** is the **median** of all pooled pixel intensities from the selected baseline images for that field of view:

$$bg_{\text{field}} = \text{median}\!\left(\{y_{\text{pixel}}\}_{t \in [t_1 \ldots t_N],\, \text{field}}\right)$$

The median is preferred over the mean because it is robust to the presence of cell bodies — bright objects that are themselves part of the field but should not inflate the background estimate.

Alternative methods available to the analyst:

$$bg_{\text{field}} = \text{mean}\!\left(\{y_{\text{pixel}}\}\right) \qquad \text{or} \qquad bg_{\text{field}} = P_p\!\left(\{y_{\text{pixel}}\}\right)$$

where $P_p$ denotes the $p$-th percentile (default $p = 10$ when using percentile mode, targeting the lower tail of the pixel intensity distribution to approximate the true cell-free background).

#### Output

A CSV file with one row per (well, field) combination, containing columns: `well`, `field`, `background`. Optional QC columns (mean, SD, min, max of pixel intensities) are computed alongside the primary background estimate and retained for analyst review. This CSV is passed as input to all subsequent analysis steps.

---

### Step 2: `phenix_to_xlsx` — Data Conversion from Harmony Exports

The Opera Phenix Harmony software exports raw measurement data as tab-delimited plain-text files (`PlateResults.txt` and `Objects_Population - <population>.txt`). This step parses those files and organizes the data into per-well Excel workbooks, with one workbook per imaging experiment (Experiment_MMDDYY_N) and one sheet per well.

A plate overview sheet is generated by aggregating per-well summary statistics (median, mean, sum, max, or min, depending on the column type). No biological filtering or quantitative calculations are applied.

If output workbooks already exist from a previous run, this step is skipped to avoid redundant computation. If no workbook is found but the raw text files are present, a minimal workbook is constructed directly from the objects file.

---

### Step 3: `detect_stim_times.py` — Stimulation Timepoint Detection

#### Purpose

All downstream analysis — baseline window selection, F/F₀ windowing, and responder classification — depends on knowing the exact time at which each stimulus was applied. The recording consists of five sequences; stimulation events occur at the transitions between sequences.

#### Method

Stimulus onset times are derived from the sequence structure of the imaging protocol:

- **Baseline range**: minimum and maximum timepoints within Sequence 2
- **Stim 1 onset** ($t_{\text{stim1}}$): the first timepoint of Sequence 3
- **Stim 2 onset** ($t_{\text{stim2}}$): the first timepoint of Sequence 4

These three values are written back into each per-well workbook. Workbook sheets corresponding to plate overviews and other non-well-level data are excluded from this detection step.

---

### Step 4: `per_object_ff0.py` — Core Per-Object Fluorescence Analysis

This is the primary analysis script. Each segmented object (cell ROI) identified by the Harmony software is processed independently through background correction, baseline normalization, quality filtering, response quantification, and responder classification. All outputs are written as additional columns in the augmented per-well workbook.

---

#### 4.1 Pre-Processing Filters (Applied Before F/F₀ Calculation)

**Missing data exclusion.** Any row missing a time value, intensity value, field number, or object ID is excluded before further processing. These represent incomplete acquisitions that cannot be reliably analyzed.

**IQR-based ROI size filter (bounding box area, two-sided).**

The Harmony software segments objects by bounding box coordinates. The area of each ROI is:

$$\text{Area}_{\text{ROI}} = (x_2 - x_1) \times (y_2 - y_1) \quad [\mu\text{m}^2]$$

The IQR of the area distribution within the well is:

$$\text{IQR}(\text{area}) = Q_3(\text{area}) - Q_1(\text{area})$$

ROIs are retained only if their area falls within the IQR-based range:

$$Q_1(\text{area}) - k \cdot \text{IQR}(\text{area}) \;\leq\; \text{Area}_{\text{ROI}} \;\leq\; Q_3(\text{area}) + k \cdot \text{IQR}(\text{area})$$

where $k = 1.5$ by default (the standard Tukey fence). Additionally, hard absolute limits are applied: the area must satisfy $50 \;\leq\; \text{Area}_{\text{ROI}} \;\leq\; 2{,}000 \;\mu\text{m}^2$. Objects smaller than 50 µm² are typically debris, dye granules, or out-of-focus particles; objects larger than 2,000 µm² are likely cell clusters, overlapping somas, or segmentation artifacts that span multiple cells.

**IQR-based intensity filter (upper-side only).**

$$y_i \;\leq\; Q_3(y) + k \cdot \text{IQR}(y)$$

Only the upper tail is clipped. Objects with anomalously high raw intensity — which can result from fluorescent debris, saturated pixels, or highly dye-loaded off-target objects — are excluded. The lower tail is not filtered because a cell with a low resting intensity can still mount a real calcium response and should not be discarded based on baseline brightness alone.

**Minimum timepoints.** Objects with fewer than 2 timepoints remaining after the above filters are excluded.

---

#### 4.2 Background Correction

For each object, the per-field background value $bg$ from the background CSV (Step 1) is subtracted at every timepoint:

$$F_{\text{corr},i} = y_i - bg_{\text{well,\,field}}$$

This removes the non-cellular fluorescence offset, leaving only the signal attributable to the cell's calcium-dependent dye response.

If no field-specific background is available, a global fallback value is used. If no global value is supplied, $bg = 0$ (equivalent to no correction). Any deviation from the standard background correction procedure must be documented.

---

#### 4.3 Baseline Selection and F₀ Computation

The baseline window consists of the last $N$ timepoints occurring before the first stimulation:

$$\mathcal{T}_{\text{base}} = \{t_i : t_i < t_{\text{stim1}}\}_{[-N:]}$$

Using the last (most recent) pre-stimulation timepoints, rather than the first, provides the best estimate of the cell's resting state at the moment of stimulation, minimizing the effect of any slow drift in dye loading or equilibration earlier in the recording. The default $N = 5$ corresponds to the five baseline timepoints in Sequence 2.

If no stimulation time is available for a given well, the first $N$ timepoints of the entire series are used as a fallback.

The per-object resting baseline fluorescence is:

$$F_0 = \frac{1}{|\mathcal{T}_{\text{base}}|}\sum_{i \in \mathcal{T}_{\text{base}}} F_{\text{corr},i}$$

Objects with $F_0 = 0$ or a non-finite $F_0$ are excluded, as they would produce undefined or infinite $F/F_0$ values.

---

#### 4.4 Fluorescence Normalization

At every timepoint, the normalized calcium signal is:

$$\frac{F}{F_0}\bigg|_i = \frac{F_{\text{corr},i}}{F_0}$$

**Interpretation:** At rest, $F/F_0 \approx 1.0$. A value of 2.0 indicates that the corrected fluorescence has doubled relative to the cell's own resting level — corresponding to a substantial calcium transient. A value of 1.5 would represent a 50% increase. Because each cell is normalized to its own $F_0$, cells with different absolute brightness levels are placed on a common scale, making inter-cell and inter-well comparisons meaningful.

This normalization is equivalent to the $\Delta F/F$ convention widely used in calcium imaging, except that here the ratio is $F_{\text{corr}}/F_0$ (the full corrected signal divided by the baseline) rather than $(F - F_0)/F_0$, so that the resting value is 1 rather than 0.

---

#### 4.5 Baseline Stability Filter

After computing $F/F_0$, each cell's resting period is assessed against the well-wide baseline distribution. This step identifies cells with intrinsically elevated or unstable baseline calcium — conditions that would make it impossible to reliably define the resting state and therefore to interpret any post-stimulation response.

**Well-level baseline statistics** are assembled by pooling all baseline $F/F_0$ values across all accepted objects in the well:

$$\mu_W = \text{mean}\!\left(\bigcup_{\text{objects}} F/F_0\big|_{\mathcal{T}_{\text{base}}}\right), \qquad \sigma_W = \text{SD}\!\left(\bigcup_{\text{objects}} F/F_0\big|_{\mathcal{T}_{\text{base}}}\right)$$

A single baseline timepoint for a given object is flagged as an outlier if it exceeds the well-level mean by more than 2 standard deviations:

$$\frac{F}{F_0}\bigg|_i > \mu_W + 2\,\sigma_W$$

Note that only the upper tail is checked. Cells with transiently suppressed baseline calcium are not excluded by this criterion; only those with anomalously elevated resting signals are flagged.

An object is excluded if the majority of its baseline timepoints are outliers:

$$\frac{\#\left\{i \in \mathcal{T}_{\text{base}} : \frac{F}{F_0}\big|_i > \mu_W + 2\,\sigma_W \right\}}{|\mathcal{T}_{\text{base}}|} > 0.5$$

Using a majority-vote rule (>50%) rather than excluding any object with a single outlier timepoint makes the filter robust to transient noise events while still capturing cells whose baseline is persistently elevated.

---

#### 4.6 Per-Object Response Statistics

For each object that passes the filters above, the following statistics are computed within the post-stimulation analysis window $[t_{\text{stim}},\; t_{\text{stim}} + \Delta t_{\text{win}}]$, where $\Delta t_{\text{win}} = 30\;\text{s}$ by default. Separate statistics are computed for the Stim 1 and Stim 2 windows.

**Mean $F/F_0$ in window:**

$$\overline{F/F_0}_{\text{win}} = \frac{1}{n_{\text{win}}} \sum_{i \in \text{win}} \frac{F}{F_0}\bigg|_i$$

**Standard deviation of $F/F_0$ in window** (sample SD, $ddof = 1$):

$$\sigma_{F/F_0,\,\text{win}} = \sqrt{\frac{1}{n_{\text{win}}-1} \sum_{i \in \text{win}} \left(\frac{F}{F_0}\bigg|_i - \overline{F/F_0}_{\text{win}}\right)^2}$$

**Peak $F/F_0$ in window:**

$$\left(\frac{F}{F_0}\right)_{\text{peak}} = \max_{i \in \text{win}}\frac{F}{F_0}\bigg|_i$$

**Time of peak — absolute and relative to stimulation onset:**

$$t_{\text{peak, abs}} = t\!\left[\arg\max_{i \in \text{win}} \frac{F}{F_0}\right]$$

$$t_{\text{peak, rel}} = t_{\text{peak, abs}} - t_{\text{stim}}$$

**Area under the curve (AUC) above baseline — trapezoidal integration:**

The AUC is computed by subtracting 1 (the resting $F/F_0$ level) before integrating, so that the integral represents the net calcium elevation above the resting state:

$$\text{AUC} = \int_{t_{\text{stim}}}^{t_{\text{stim}}+\Delta t_{\text{win}}} \!\!\left(\frac{F}{F_0}(t) - 1\right)\,dt \;\approx\; \sum_{i \in \text{win}} \left(\frac{\frac{F}{F_0}\big|_i + \frac{F}{F_0}\big|_{i+1}}{2} - 1\right) \cdot (t_{i+1} - t_i)$$

A positive AUC indicates a sustained calcium elevation; an AUC near zero indicates a transient or absent response. Negative AUC can result from baseline suppression within the window.

**Linear slope of $F/F_0$ vs. time — least-squares regression:**

$$m = \frac{\sum_i (t_i - \bar{t})\left(\frac{F}{F_0}\big|_i - \overline{F/F_0}_{\text{win}}\right)}{\sum_i (t_i - \bar{t})^2}$$

A positive slope indicates a signal still rising within the analysis window; a negative slope indicates a declining signal or a peak that has already passed. The slope is useful for distinguishing a slow-rising tonic response from a fast transient that has decayed back to baseline within the window.

---

#### 4.7 Responder Classification

**General responder.** An object is classified as a general responder if its peak $F/F_0$ over the entire recording (not just within the analysis window) exceeds the well-level baseline threshold by at least 2 standard deviations:

$$\left(\frac{F}{F_0}\right)_{\text{peak, global}} \;\geq\; \mu_W + 2\,\sigma_W$$

This threshold defines a response as any calcium elevation that is statistically unlikely to arise from baseline variability alone, using the well's own distribution as the reference.

**Stim 1 responder.** An object must simultaneously satisfy all three of the following conditions:

1. It is a general responder (criterion above).
2. Its peak $F/F_0$ during the Stim 1 window $[t_{\text{stim1}},\, t_{\text{stim2}})$ meets an absolute threshold:
$$\left(\frac{F}{F_0}\right)_{\text{peak, stim1}} \;\geq\; 2.0$$
3. Its own pre-stimulation baseline mean is within the well-level distribution (confirming a stable resting state from which the response can be measured):
$$\left|\bar{b}_{\text{object}} - \mu_W\right| \;\leq\; 2\,\sigma_W$$

Condition 3 guards against objects with an elevated resting calcium that might reach the threshold simply because of their high starting point, rather than because of a genuine stimulus-evoked response.

**Stim 2 (positive-control stimulus) responder.** Because a cell's calcium may not have fully returned to baseline between Stim 1 and Stim 2, using the global well-level baseline alone to detect a Stim 2 response could either miss a response (if the threshold is too high relative to the post-Stim-1 level) or produce false positives (if the cell is still elevated). A local pre-Stim 2 baseline is therefore used as a secondary reference.

Both of the following conditions must be satisfied:

1. The peak during the Stim 2 window exceeds the global baseline threshold:
$$\left(\frac{F}{F_0}\right)_{\text{peak, stim2}} \;\geq\; \mu_W + 2\,\sigma_W$$

2. The peak also exceeds the local pre-Stim 2 threshold, defined by the mean and SD of $F/F_0$ in the window immediately preceding $t_{\text{stim2}}$:
$$\left(\frac{F}{F_0}\right)_{\text{peak, stim2}} \;\geq\; \mu_{\text{pre}} + 2\,\sigma_{\text{pre}}$$

**Ambiguous Stim 2.** If the cell's calcium is still substantially elevated from Stim 1 at the time Stim 2 is applied, neither a positive nor a negative Stim 2 classification is reliable. An object is flagged as ambiguous if at least half of its pre-Stim 2 timepoints already exceed the global baseline threshold:

$$\frac{\#\left\{i \in \mathcal{T}_{\text{pre-stim2}} : \frac{F}{F_0}\big|_i \geq \mu_W + 2\,\sigma_W \right\}}{|\mathcal{T}_{\text{pre-stim2}}|} \;\geq\; 0.5 \implies \text{flagged as ambiguous}$$

Ambiguous objects are excluded from both the Stim 2 responder count and the non-responder count in downstream summary statistics.

**Well viability (Stim 2 QC).** After per-object classification, each well is evaluated for overall analyzability. A well is considered analyzable only if:

$$n_{\text{cells}} \;\geq\; 3 \quad \text{AND} \quad \frac{n_{\text{respond to Stim 1 or Stim 2}}}{n_{\text{cells}}} \;\geq\; 0.30$$

This criterion uses the positive-control stimulus (Stim 2) as a viability positive control — if fewer than 30% of cells in a well respond to a maximal stimulus, the well is considered to contain too few viable, dye-loaded cells to provide reliable data and is excluded from all downstream analyses.

Additionally, wells where more than 50% of objects fail the baseline stability filter are flagged separately at the well level, independent of the viability criterion.

---

### Step 5: `filter_post_stats.py` — Post-Analysis Quality Filtering

A second, independent quality filtering pass is applied to the augmented workbook produced by Step 4. This step catches data quality problems that only become apparent after full analysis, such as persistent negative corrected-fluorescence values or an incorrect total timepoint count. All thresholds are configurable. Criteria are applied sequentially; an object failing any criterion is excluded from the final filtered workbook.

**1. Carry-forward exclusion flag.** Any object already excluded in Step 4 is excluded here as well, regardless of its other values.

**2. Non-finite and negative $F/F_0$ values.** Objects with more than 2 non-finite values or more than 2 negative $F/F_0$ values across the full trace are excluded:

$$\#\{i : F/F_0\big|_i \notin \mathbb{R}\} > 2 \quad \text{or} \quad \#\{i : F/F_0\big|_i < 0\} > 2$$

A small number of transient non-finite or negative values can arise from timepoints near the lower limit of detection. More than 2 such occurrences suggests a systematic problem.

**3. Persistent negative corrected fluorescence.** Objects with more than 2 timepoints where $F_{\text{corr}} < 0$ are excluded:

$$\#\{i : F_{\text{corr},i} < 0\} > 2$$

Persistent negative $F_{\text{corr}}$ indicates that the background estimate exceeds the raw signal at that location — either the background was overestimated or the object was in a region of genuinely very low cellular signal.

**4. Timepoint count check.** Each object must have exactly the expected number of timepoints for this experimental protocol (default: 27, corresponding to Sequences 2–4):

$$n_{\text{total}} \neq 27 \implies \text{exclude}$$

Objects with fewer timepoints have incomplete traces (possibly due to imaging interruption), making their response statistics unreliable or incomparable to other objects.

**5. Peak $F/F_0$ threshold.** Objects whose global peak normalized fluorescence does not exceed 2.0 at any point in the entire recording are excluded:

$$\left(\frac{F}{F_0}\right)_{\text{peak, global}} \leq 2.0 \implies \text{exclude}$$

A cell that never reaches twice its resting fluorescence level — across all three stimulation-period sequences — did not mount a meaningful calcium response to any stimulus, and its inclusion would dilute the signal in responder statistics.

**6. Baseline outlier majority — re-evaluated from the post-Step-4 distribution.** The same majority-vote baseline stability logic as Step 4.5 is applied, but using the well-level statistics recomputed from the subset of objects that survived Step 4. A baseline timepoint is flagged as an outlier if it exceeds either of two thresholds:

$$\frac{F}{F_0}\bigg|_i > (\mu_W + 2\,\sigma_W) \quad \text{OR} \quad \frac{F}{F_0}\bigg|_i > 1.9$$

The absolute threshold of 1.9 provides a floor: even in wells where $\sigma_W$ is very small (e.g., unusually quiet wells), a cell with a pre-stimulation $F/F_0$ consistently approaching 2.0 is not in a genuinely resting state and should not be analyzed as if it were.

If the fraction of flagged baseline timepoints exceeds 50%, the object is excluded:

$$\frac{\#\{\text{outlier baseline timepoints}\}}{|\mathcal{T}_{\text{base}}|} > 0.5 \implies \text{exclude}$$

---

### Step 6: `per_object_ff0_plots.py` — Individual Cell Trace Plots

Reads the augmented workbooks — both the full (unfiltered) output from Step 4 and the filtered output from Step 5 — and generates individual $F/F_0$ vs. time traces for each object. These plots allow visual inspection of individual cell responses and are saved to separate "raw plots" and "filtered plots" subfolders. No new filtering calculations are performed. Objects missing time, field ID, object ID, or $F/F_0$ values are skipped silently.

---

### Step 7: `spaghetti_plot_per_well.py` — Per-Well Trace Overlay Plots

Applies the same pre-processing IQR filters as Step 4.1 (two-sided size filter, upper-only intensity filter) to each well before generating overlaid trace plots. Every surviving trace is plotted individually (the "spaghetti"), and the population mean trace is superimposed with an error band:

$$\text{SEM} = \frac{\sigma_{F/F_0}}{\sqrt{n_{\text{objects}}}} \qquad \text{or} \qquad \text{SD} = \sigma_{F/F_0}$$

The SEM reflects the uncertainty on the mean response, while the SD reflects the heterogeneity of responses across individual cells. Wells where all objects are filtered out are skipped entirely. Both raw (unfiltered) and filtered versions of each plot are produced.

---

### Step 8: `responder_bar_charts.py` — Responder Percentage Bar Charts

Computes and visualizes the following summary percentages for each experimental condition. Blank wells and rows missing required numeric fields are excluded before calculation.

**Stim 1 response rate** (fraction of cells with any general response that specifically responded to Stim 1):

$$\%\,\text{Stim 1 responders} = \frac{n_{\text{stim1 responders}}}{n_{\text{stim1 responders}} + n_{\text{stim1 non-responders}}} \times 100$$

Note that this denominator includes only general responders — cells that had no response at all are not included. This isolates the specificity of the Stim 1 response among cells capable of responding.

**Stim 2 response rate** (ambiguous objects are excluded from the denominator):

$$\%\,\text{Stim 2 responders} = \frac{n_{\text{stim2 responders}}}{n_{\text{stim2 responders}} + n_{\text{stim2 non-responders}}} \times 100$$

**Stim 1 fraction of all responsive cells:**

$$\%\,\text{Stim 1 of General} = \frac{n_{\text{stim1 responders}}}{n_{\text{general responders}}} \times 100$$

This metric answers the question: of all cells that responded to anything (Stim 1 or positive-control stimulus), what proportion specifically responded to the test stimulus?

---

### Step 9: `combine_date_summaries.py` — Cross-Date Summary Aggregation

Merges per-plate summary data from multiple imaging dates into a single combined spreadsheet. The primary metric propagated across dates is:

$$\%\,\text{Stim 1 of General} = \frac{n_{\text{stim1 responders}}}{n_{\text{general responders}}} \times 100$$

A filter-failure pivot table is generated, counting the number of unique wells assigned to each exclusion reason across all dates. This provides a high-level view of data attrition at each step of the pipeline and can flag dates with unusually high failure rates for follow-up investigation.

Blank wells, wells with missing identifiers, and wells whose group assignment would create duplicates are excluded before merging.

---

## Part 2: Scripts Run Outside the Automated Pipeline

---

### `build_plate_manifest.py` — Plate-Level Well Manifest and QC Flagging

#### Well-Level Scorability Criterion

A well is considered to have sufficient statistical power to contribute to summary analyses only if it contains more than 20 general responders:

$$\text{Scorable} \iff n_{\text{general responders}} > 20$$

Wells falling below this threshold are retained in the dataset but flagged as non-scorable and excluded from group-level comparisons.

#### Plate-Level Stim 1 QC Flag

The Stim 1 response rate is computed across all objects on the plate:

$$\%\,\text{Stim 1 (plate)} = \frac{n_{\text{stim1 responders}}}{n_{\text{objects}}} \times 100$$

If this plate-wide rate reaches or exceeds 80%, the entire plate is flagged as technically suspect:

$$\%\,\text{Stim 1 (plate)} \geq 80\% \implies \text{all wells on plate: scorable} = \text{False}$$

A biologically plausible Stim 1 response rate in a heterogeneous cell population is unlikely to approach 80% across all cells on a plate. Such a high rate more likely reflects a non-specific stimulus (e.g., mechanical stimulation from the injection, osmotic shock, or dye-related artifacts) or plate-level contamination.

---

### `phenix_plots_from_xlsx.py` — Publication-Style Average Trace Plots

Applies the IQR-based area (two-sided, $k = 1.5$) and intensity (upper-side only, $k = 1.5$) pre-filters from Step 4.1, then normalizes each surviving trace to its pre-stimulation $F/F_0$. The population mean trace is plotted with error bands:

$$\text{SEM} = \frac{\sigma_{F/F_0}}{\sqrt{n}} \qquad \text{or} \qquad \text{SD} = \sigma_{F/F_0}$$

AUC above baseline is computed by trapezoidal integration as defined in Step 4.6.

---

### `spaghetti_plot_filtered.py` — Filtered Spaghetti Plots with Additional Trace Criteria

Extends the per-well spaghetti plots (Step 7) with additional trace-level quality criteria:

- Pre-stimulation data must be present when $t_{\text{stim1}}$ is defined (traces beginning after Stim 1 onset are excluded).
- Full timepoint coverage is required unless the analyst explicitly permits gaps.
- Traces are excluded if too large a fraction of Stim 2-window timepoints fall below a negative threshold $\theta_{\text{neg}}$:

$$\frac{\#\!\left\{i \in \text{Stim 2 window} : F/F_0\big|_i < \theta_{\text{neg}}\right\}}{n_{\text{stim2 window}}} > f_{\text{neg}} \implies \text{exclude trace}$$

This removes traces from cells that were dying or detaching during the recording. Dying cells exhibit characteristic prolonged negative deflections in $F/F_0$ — consistent with calcium dysregulation or loss of dye containment — rather than the sharp transient rises seen in genuine calcium responses.

Baseline stability bounds used for plot annotation:

$$\text{Lower bound} = \mu_{\text{baseline}} - k \cdot \sigma_{\text{baseline}}, \qquad \text{Upper bound} = \mu_{\text{baseline}} + k \cdot \sigma_{\text{baseline}}$$

---

## Summary of Key Thresholds (Default Values)

| Parameter | Default | Applied In |
|---|---|---|
| Background estimation method | Median pooled pixel intensity | Step 1 |
| Background baseline timepoints ($N$) | 5 per (well, field) | Step 1 |
| Baseline timepoints for $F_0$ ($N$) | 5 (last pre-stimulation) | Steps 4, 5 |
| ROI area hard minimum | 50 µm² | Step 4 |
| ROI area hard maximum | 2,000 µm² | Step 4 |
| IQR multiplier — ROI size filter ($k$) | 1.5 (two-sided) | Steps 4, 7, 8 |
| IQR multiplier — intensity filter ($k$) | 1.5 (upper-side only) | Steps 4, 7, 8 |
| Baseline outlier threshold (relative) | $\mu_W + 2\,\sigma_W$ | Steps 4, 5 |
| Baseline outlier threshold (absolute) | $F/F_0 > 1.9$ | Step 5 only |
| Max outlier fraction before object exclusion | 50% | Steps 4, 5 |
| General responder threshold | $\mu_W + 2\,\sigma_W$ (global peak) | Step 4 |
| Stim 1 responder peak threshold | $F/F_0 \geq 2.0$ during Stim 1 window | Step 4 |
| Stim 2 ambiguous flag | $\geq 50\%$ of pre-Stim 2 timepoints above threshold | Step 4 |
| Analysis window duration ($\Delta t_{\text{win}}$) | 30 s post-stimulus onset | Step 4 |
| Well viability — minimum cells | $\geq 3$ identified cells | Step 4 |
| Well viability — minimum response rate | $\geq 30\%$ respond to Stim 1 or Stim 2 | Step 4 |
| Expected timepoints per object | 27 | Step 5 |
| Peak $F/F_0$ threshold (post-filter) | $> 2.0$ (global) | Step 5 |
| Scorable well threshold | $n_{\text{general responders}} > 20$ | `build_plate_manifest` |
| Plate-level Stim 1 QC flag | $\geq 80\%$ plate-wide Stim 1 response rate | `build_plate_manifest` |

---

*Document version: April 2026*
