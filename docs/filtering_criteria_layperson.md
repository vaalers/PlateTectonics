# PT Pipeline — What the Analysis Does: A Plain-Language Guide

This document explains the PT data analysis pipeline in everyday language for anyone who wants to understand what the software is doing — no scientific or coding background required.

---

## What Are We Measuring?

Cells are grown in the wells of a 96-well plate and loaded with a fluorescent indicator dye (for example, a calcium-sensitive dye) that glows brighter when the cells become active. An automated microscope (the Opera Phenix) photographs each well repeatedly: first while the cells are resting, then after a first stimulus ("Stim 1") is added, and then after a second stimulus ("Stim 2", typically a positive control that should activate healthy cells).

The question the pipeline answers: did the cells respond to Stim 1? If so, which cells responded, how strongly, and how quickly? Stim 2 tells us whether the cells were alive and able to respond at all.

## Part 1: The Automated Pipeline

The following steps run in order, each building on the outputs of the previous step.

---

### Pre-Pipeline Step: Organizing the Raw Files
**Script: `phenix_reorg.py`**

Before any analysis can happen, the raw data files that come off the microscope need to be organized into a predictable folder structure. This step is run manually by the analyst and simply renames and reorganizes the files so that all the downstream analysis steps know exactly where to look for them.

Think of it like sorting a pile of unsorted receipts into labeled folders before you start doing the accounting.

No data is changed or filtered at this step — it's purely organizational.

---

### Step 1: Removing Background Glow
**Script: `compute_fov_background.py`** *(optional — run when a background CSV does not already exist)*

Even in areas of the plate where there are no cells, the camera picks up a faint glow. This "background" glow comes from the dye itself scattering light and from slight imperfections in illumination across the plate. It also varies from one field of view to another — the corners of a well may look slightly different from the center.

If we don't account for this background, we'd be adding it into every cell's measurement, making our readings slightly inaccurate and inconsistent across the plate.

This step measures the background glow in each section of each well using images taken before any stimulus is applied (when the cells are just sitting quietly). It estimates the background as the middle value (median) of all the pixel brightness readings in that area. The result is saved as a spreadsheet with one background value per well-section combination.

All subsequent steps use these values to correct the raw measurements.

---

### Step 2: Converting Raw Data Files
**Script: `phenix_to_xlsx`**

The microscope software (Harmony) exports raw data in plain text files — rows and rows of numbers that are difficult to work with directly. This step reads those files and converts them into organized Excel workbooks, one per imaging experiment, with a separate tab for each well.

Think of it as converting a raw data dump into a clean, organized spreadsheet.

No filtering or calculation happens here — this is a pure format conversion step. If the output files already exist from a previous run, this step is skipped to save time.

---

### Step 3: Finding When Stimulations Happened
**Script: `detect_stim_times.py`**

The recording for each well is divided into timed phases. In each recording:
- The first phase measures the cells quietly before anything is added (the "baseline")
- The second phase records the response after Test Stimulus (Stim 1) is added
- The third phase records the response after the drug cocktail (Stim 2) is added

This step reads the timing information from the recording structure and writes the exact start time of each phase into the Excel workbook. These time markers are used by every downstream step to correctly separate "before stimulation" data from "during stimulation" data.

If these times are wrong, the entire analysis would be shifted — like measuring the wrong portion of a race.

---

### Step 4: Measuring Each Cell's Calcium Signal
**Script: `per_object_ff0.py`**

This is the core of the pipeline. Every cell identified by the imaging software is processed individually through the following sequence of steps.

#### 4a. Removing Incomplete or Unusable Data Points
First, any measurement that is missing key information — like the time it was taken, the brightness value, or which well it came from — is dropped.

#### 4b. Filtering Out Objects That Aren't Cells
The imaging software outlines each object it finds in the images, but not every outlined object is actually a cell. Some are debris, dye clumps, out-of-focus particles, or accidentally merged cell clusters.

To screen for this, we use the size of each outlined object. Real cells fall within a specific size range. Objects that are too small (likely debris) or too large (likely overlapping cells or imaging artifacts) are removed. We also remove objects that are unusually bright — which can indicate saturated pixels or off-target material.

Think of it as sorting photographs and discarding the ones that are clearly not what you were trying to photograph.

#### 4c. Subtracting the Background
For each cell, we subtract the background glow (measured in Step 1) from every time point. This gives us the cell's "true" fluorescence — only the signal coming from the cell itself.

#### 4d. Calculating Each Cell's Resting Level (F₀)
Because cells start at different brightness levels, we can't compare them directly. A cell that starts dim and doubles its brightness is just as responsive as one that starts bright and doubles — but their raw numbers look very different.

To make fair comparisons, we calculate each cell's personal "resting level" (called F₀) by averaging its brightness across the last few time points before any stimulus was applied. We then divide every subsequent measurement by this resting level. The result (called F/F₀) equals approximately 1.0 when the cell is at rest, and rises above 1.0 when it becomes active. A value of 2.0 means the cell doubled its glow — a strong response.

Think of it like describing how much brighter a lamp gets compared to its normal setting, rather than measuring raw wattage — this lets you fairly compare a dim desk lamp to a bright floor lamp.

#### 4e. Checking Baseline Stability
A healthy, quiet cell should have a steady, flat F/F₀ before any stimulus is applied. If a cell's resting period is already jumping around — flashing and dimming erratically — it suggests the cell is not in a stable state, and any apparent response after stimulation might just be noise.

We check each cell's resting period against the overall average for that well. If more than half of a cell's resting time points are far outside the expected range, the cell is removed from further analysis.

#### 4f. Calculating Response Statistics
For each cell that passes the checks above, we calculate several measurements within a defined window of time after each stimulation:
- **Average brightness** during the response window
- **Peak brightness** reached and when it occurred
- **Total response area** — the cumulative calcium elevation over time (how much and for how long the cell was active)
- **Slope** — whether the signal was still rising, already falling, or flat

These numbers are saved for every cell in the output workbook.

#### 4g. Classifying Responders
Each cell is then labeled:
- **General responder:** had any significant calcium rise above the well's resting average
- **Stim 1 responder:** specifically responded during the Stim 1 window, with a peak at least twice the resting level, and had a stable resting state
- **Stim 2 (Stim 2) responder:** responded to the drug cocktail applied second
- **Ambiguous Stim 2:** was still elevated from Stim 1 when Stim 2 was applied, so we can't tell whether it truly responded to Stim 2
- **Well viability check:** each well must have at least 3 cells, and at least 30% of them must respond to either stimulation — otherwise the cells in that well may not have been healthy enough and the well is excluded

---

### Step 5: A Second Round of Quality Checking
**Script: `filter_post_stats.py`**

After Step 4, a second independent round of quality checks runs on every cell that passed the first round. This catches problems that only become visible after the full analysis is complete.

Cells are removed from the final results if any of the following are true:
- Their F/F₀ values went negative at more than two time points (which shouldn't happen for a live cell)
- The corrected fluorescence (after background removal) was persistently negative — suggesting the background was overestimated
- The total number of time points doesn't match what was expected for this experiment (usually 27)
- The cell never reached even twice its resting level at any point in the entire recording — meaning it never showed a meaningful response to anything

A final check re-examines baseline stability using the same logic as Step 4e, but recalculated from the refined post-analysis well-level data. An absolute upper limit on the resting F/F₀ is also applied (a value of 1.9) — cells hovering near 2.0 during their supposed "resting" period are not truly at rest.

---

### Step 6: Plotting Individual Cell Traces
**Script: `per_object_ff0_plots.py`**

For every cell that passed the filters, this step generates a line graph of F/F₀ over time — a "trace" showing how the cell's calcium signal changed from start to finish. Traces are saved into two folders: one for all cells that passed the first round of filtering (raw) and one for those that also passed the second round (filtered).

These plots allow the analyst or collaborator to visually inspect individual cells and confirm the analysis is working correctly.

---

### Step 7: Overlaid "Spaghetti" Plots Per Well
**Script: `spaghetti_plot_per_well.py`**

This step generates one plot per well showing all the individual cell traces layered on top of each other — like a tangle of spaghetti. It also draws a bold average line on top showing the typical response across all cells in that well, with shading indicating how much variation there is.

These plots give a quick visual summary of how an entire well behaved. Wells where every cell was filtered out are skipped.

---

### Step 8: Summary Bar Charts
**Script: `responder_bar_charts.py`**

This step calculates the percentage of cells that responded to each stimulus and generates bar charts summarizing the results by experimental condition. Three key percentages are computed:
- What fraction of cells responded specifically to the test stimulus (Stim 1)?
- What fraction responded to the drug cocktail (Stim 2)?
- Of all cells that responded to anything, what fraction specifically responded to Stim 1?

These bar charts are the primary visual output used to compare conditions and samples.

---

### Step 9: Combining Results Across Multiple Experiment Dates
**Script: `combine_date_summaries.py`**

Since imaging experiments are run across many different days, this step merges the summary results from all dates into a single combined spreadsheet. It also generates a table showing how many wells were lost at each filtering step across all dates — a useful quality control overview that shows whether one date had an unusually high failure rate.

---

## Part 2: Scripts Run Separately (Outside the Automated Pipeline)

These scripts are run manually by the analyst after the main pipeline completes.

---

### Deciding Which Wells Are Usable
**Script: `build_plate_manifest.py`**

A well is considered "scorable" — reliable enough to include in summary analyses — only if it has more than 20 responding cells. Wells with fewer than 20 responders don't provide enough statistical confidence to draw conclusions.

Additionally, if more than 80% of all cells on an entire plate responded to the test stimulus, the whole plate is flagged as suspect. Such a high response rate suggests something went wrong technically (like non-specific dye activation or contamination), not a genuine biological signal. All wells on a flagged plate are automatically marked as unusable.

---

### Publication-Style Average Trace Plots
**Script: `phenix_plots_from_xlsx.py`**

Generates clean, publication-quality plots showing the average calcium trace across all qualifying cells in a condition, with error shading. Also calculates the total area under the response curve — a single number summarizing how large and sustained the calcium response was.

---

### Filtered Spaghetti Plots with Stricter Trace Criteria
**Script: `spaghetti_plot_filtered.py`**

An enhanced version of the Step 7 spaghetti plots that applies additional trace-level checks before including a cell in the overlay. Notably, it removes traces from cells that appear to be dying or detaching — these show up as prolonged downward dips during the second stimulation window, which are not biological responses but rather the cell falling apart.

---

## Quick Reference: Key Quality Thresholds

| What is being checked | Threshold | Where it's applied |
|---|---|---|
| Background estimation | Median pixel brightness | Step 1 |
| ROI minimum area | 50 µm² | Step 4 |
| ROI maximum area | 2,000 µm² | Step 4 |
| Baseline stability | More than 50% of resting time points outside normal range → exclude | Steps 4, 5 |
| Minimum response to count as general responder | Well average + 2× standard deviation | Step 4 |
| Minimum peak to count as Stim 1 responder | 2× resting level | Step 4 |
| Well viability minimum | ≥3 cells AND ≥30% respond | Step 4 |
| Expected time points per cell | 27 | Step 5 |
| Minimum peak to survive final filter | 2× resting level at any point | Step 5 |
| Minimum responders for a usable well | >20 responding cells | build_plate_manifest |
| Plate rejection threshold | ≥80% of cells on plate responded to Stim 1 | build_plate_manifest |

---

*Document version: April 2026*
