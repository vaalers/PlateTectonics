# Example data

`example_data/` is a **fully synthetic** Opera Phenix / Harmony export made by
[`tools/make_example_data.py`](../tools/make_example_data.py). No real cells were imaged: every
trace comes from a small kinetic model with a fixed random seed, so the files can be shared freely
and regenerated at any time.

```
example_data/
└── 2026/
    └── 090826/                                   <- a "date folder" (MMDDYY)
        ├── 090826_simulation_truth.csv           <- which simulated objects were responders
        ├── Analysis/
        │   └── Experiment_090826_1/
        │       ├── indexfile.txt                 <- image index (sequence = baseline / Stim 1 / Stim 2)
        │       └── Evaluation1/
        │           ├── PlateResults.txt          <- well-level readouts per timepoint
        │           └── Objects_Population - Cells.txt   <- per-object readouts per timepoint
        └── Experiment_090826_1/Images/           <- TIFF frames (generated, not committed)
```

**Design.** Six wells, two fields each, 27 timepoints 2.1 s apart. Timepoint 1 is a pre-scan,
2–6 the baseline, 7–20 follow a test stimulus (Stim 1), 21–27 follow a positive control (Stim 2).
Wells B2/B3 ("Compound A 10 uM") have ~60 % Stim 1 responders, B4 ("Compound A 1 uM") ~25 %,
C2/C3 ("Vehicle") ~5 %, and C4 is a deliberately poor well that should fail well-level QC. A few
objects have odd bounding boxes, a slow decline, or missing timepoints so the filters have work to do.

## Run the pipeline on it

```bash
conda activate pt-py310
pt-run examples/example_data/2026/090826 --expected-n 27
```

Outputs land next to the inputs: per-well and filtered workbooks, spaghetti plots, facet pages,
responder bar charts, and date-level summaries under `090826/Analysis/`. The GUI (`pt-gui`) can
point at `examples/example_data/2026` as its data root.

## With raw images (background estimation)

```bash
python tools/make_example_data.py --with-images
pt-run examples/example_data/2026/090826 --expected-n 27 --no-phenix \
    --compute-backgrounds --raw-data-root examples/example_data --background-channel "Alexa 488"
```

The frames are tiny (64 × 64 px) but follow the Phenix naming scheme
(`r02c02f01p01-ch1sk<T>fk1fl1.tiff`), so the FOV background step runs exactly as it would on real data.

## Regenerate or vary it

```bash
python tools/make_example_data.py --clean                 # rebuild the text exports
python tools/make_example_data.py --date 010126 --seed 3  # another date / random draw
python tools/make_example_data.py --out /path/elsewhere   # write outside the repository
```

`--clean` deletes the date folder first, which also clears any pipeline outputs written into it.
