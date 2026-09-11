#!/usr/bin/env python3
"""Generate a small, fully synthetic Opera Phenix / Harmony export for trying PTHTS.

Nothing here is derived from real measurements: every trace is drawn from a simple
kinetic model with a fixed random seed. The output mimics what Harmony writes when a
kinetic (time-series) analysis is exported as text:

    <out>/<YYYY>/<MMDDYY>/
    ├── Analysis/Experiment_<MMDDYY>_1/
    │   ├── indexfile.txt                      image index: Row/Column/Field/Timepoint/Sequence/Channel/URL
    │   └── Evaluation1/
    │       ├── PlateResults.txt               one row per well x timepoint (well-level readouts)
    │       └── Objects_Population - Cells.txt one row per object x timepoint (per-object readouts)
    └── Experiment_<MMDDYY>_1/Images/          optional (--with-images): tiny 16-bit TIFF frames named
                                               r02c02f01p01-ch1sk<T>fk1fl1.tiff, as the Phenix names them

Acquisition model (27 timepoints, 2.1 s apart):
    Sequence 1  timepoint 1        pre-scan
    Sequence 2  timepoints 2-6     baseline
    Sequence 3  timepoints 7-20    Stim 1 (test stimulus added at timepoint 7)
    Sequence 4  timepoints 21-27   Stim 2 (positive control added at timepoint 21)

Plate design (6 wells, 2 fields each):
    B2, B3   "Compound A 10 uM"  ~60 % of objects respond to Stim 1
    B4       "Compound A 1 uM"   ~25 %
    C2, C3   "Vehicle"           ~5 %
    C4       "Vehicle", low-viability well (few objects, poor Stim 2 response) -> fails well QC
About 85 % of healthy objects respond to Stim 2. A few objects are deliberately odd (very large or
very small bounding boxes, a slow decline, missing timepoints) so the QC filters have something to do.

Usage:
    python tools/make_example_data.py                       # writes examples/example_data (text files only)
    python tools/make_example_data.py --with-images         # also writes the TIFF frames (~3 MB)
    python tools/make_example_data.py --out /tmp/demo --date 010126 --seed 3
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Acquisition constants
# ---------------------------------------------------------------------------
N_TIMEPOINTS = 27
INTERVAL_S = 2.1
SEQUENCE_OF_TP = {1: 1, **{tp: 2 for tp in range(2, 7)}, **{tp: 3 for tp in range(7, 21)}, **{tp: 4 for tp in range(21, 28)}}
STIM1_TP = 7
STIM2_TP = 21
N_FIELDS = 2
FIELD_PX = 1080          # field of view in pixels
UM_PER_PX = 0.6
POPULATION = "Cells"
CHANNEL_NAME = "Alexa 488"
CHANNEL_ID = 1
PLANE = 1

# well -> (condition, stim1 responder fraction, viable)
WELLS = {
    "B2": ("Compound A 10 uM", 0.60, True),
    "B3": ("Compound A 10 uM", 0.60, True),
    "B4": ("Compound A 1 uM", 0.25, True),
    "C2": ("Vehicle", 0.05, True),
    "C3": ("Vehicle", 0.05, True),
    "C4": ("Vehicle", 0.05, False),
}
STIM2_LABEL = "Positive control"


def well_to_rc(well: str) -> tuple[int, int]:
    return ord(well[0].upper()) - ord("A") + 1, int(well[1:])


def kernel(t_rel: np.ndarray, tau: float) -> np.ndarray:
    """Fast-rise, exponential-decay response starting at t_rel == 0 (one frame rise)."""
    k = np.where(t_rel >= 0, np.exp(-np.clip(t_rel, 0, None) / tau), 0.0)
    k = np.where((t_rel >= 0) & (t_rel < INTERVAL_S), 0.7 * k, k)  # partial first frame
    return k


def simulate_object(rng: np.random.Generator, stim1_responder: bool, stim2_responder: bool,
                    background: float, dying: bool) -> np.ndarray:
    """Return raw mean intensity for timepoints 1..N (includes background)."""
    t = (np.arange(1, N_TIMEPOINTS + 1) - 1) * INTERVAL_S
    f0 = float(rng.lognormal(mean=np.log(2600), sigma=0.25))
    f0 = float(np.clip(f0, 1200, 6000))
    a1 = rng.uniform(1.2, 3.2) if stim1_responder else rng.uniform(0.0, 0.10)
    a2 = rng.uniform(2.0, 6.0) if stim2_responder else rng.uniform(0.0, 0.15)
    r = (a1 * kernel(t - (STIM1_TP - 1) * INTERVAL_S, tau=rng.uniform(2.5, 4.5))
         + a2 * kernel(t - (STIM2_TP - 1) * INTERVAL_S, tau=rng.uniform(8, 14)))
    drift = np.linspace(1.0, rng.uniform(0.55, 0.7), N_TIMEPOINTS) if dying else 1.0
    signal = f0 * (1.0 + r) * drift
    # Small, temporally correlated measurement noise (AR(1)); a region-mean intensity is smooth.
    white = rng.normal(0.0, 0.005 * f0, N_TIMEPOINTS)
    noise = np.empty(N_TIMEPOINTS)
    noise[0] = white[0]
    for i in range(1, N_TIMEPOINTS):
        noise[i] = 0.5 * noise[i - 1] + white[i]
    return background + signal + noise


def make_bbox(rng: np.random.Generator, kind: str) -> tuple[int, int, int, int]:
    if kind == "huge":
        w, h = rng.integers(140, 200, 2)
    elif kind == "tiny":
        w, h = rng.integers(4, 7, 2)
    else:
        w, h = rng.integers(18, 42, 2)
    x1 = int(rng.integers(20, FIELD_PX - 220)); y1 = int(rng.integers(20, FIELD_PX - 220))
    return x1, y1, x1 + int(w), y1 + int(h)


def harmony_header(kind: str, measurement: str, evaluation: str, extra: dict | None = None) -> str:
    rows = [
        ("Database name", "PTHTS example"),
        ("Database link", "local"),
        ("Evaluation signature", "synthetic-" + measurement),
        ("Plate Name", "Example plate"),
        ("Barcode", "EXAMPLE-0001"),
        ("Measurement", measurement),
        ("Evaluation", evaluation),
        ("Export type", kind),
    ]
    if extra:
        rows += list(extra.items())
    return "\n".join(f"{k}\t{v}" for k, v in rows) + "\n[Data]\n"


def build_dataset(seed: int, date6: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    t0 = datetime.strptime(date6, "%m%d%y").replace(hour=10, minute=15)
    obj_rows: list[dict] = []
    per_field_bg: dict[tuple[str, int], float] = {}
    truth: dict[str, dict] = {}

    for well, (condition, p_stim1, viable) in WELLS.items():
        r, c = well_to_rc(well)
        for field in range(1, N_FIELDS + 1):
            background = float(rng.normal(850, 40))
            per_field_bg[(well, field)] = background
            n_obj = int(rng.integers(5, 8)) if not viable else int(rng.integers(18, 27))
            for obj in range(1, n_obj + 1):
                stim2_resp = bool(rng.random() < (0.20 if not viable else 0.85))
                stim1_resp = bool(stim2_resp and rng.random() < p_stim1)
                dying = bool(rng.random() < 0.03)
                kind = "huge" if rng.random() < 0.03 else ("tiny" if rng.random() < 0.03 else "normal")
                trace = simulate_object(rng, stim1_resp, stim2_resp, background, dying)
                x1, y1, x2, y2 = make_bbox(rng, kind)
                area_um2 = round((x2 - x1) * (y2 - y1) * UM_PER_PX ** 2, 2)
                drop_tps: set[int] = set()
                if rng.random() < 0.05:
                    drop_tps = set(rng.choice(np.arange(2, N_TIMEPOINTS + 1), size=int(rng.integers(1, 4)), replace=False).tolist())
                truth[f"{well}_F{field}_obj{obj}"] = {"stim1_responder": stim1_resp, "stim2_responder": stim2_resp,
                                                     "dying": dying, "bbox": kind, "missing_tps": sorted(drop_tps)}
                for tp in range(1, N_TIMEPOINTS + 1):
                    if tp in drop_tps:
                        continue
                    obj_rows.append({
                        "Row": r, "Column": c, "Plane": PLANE, "Timepoint": tp, "Field": field,
                        "Object No": obj,
                        "X": int((x1 + x2) / 2), "Y": int((y1 + y2) / 2),
                        "Bounding Box": f"[{x1}, {y1}, {x2}, {y2}]",
                        "Position X [µm]": round((x1 + x2) / 2 * UM_PER_PX, 2),
                        "Position Y [µm]": round((y1 + y2) / 2 * UM_PER_PX, 2),
                        f"{POPULATION} - Intensity Cell {CHANNEL_NAME} Mean": round(float(trace[tp - 1]), 2),
                        f"{POPULATION} - Cell Area [µm²]": area_um2,
                        f"{POPULATION} - Cell Roundness": round(float(rng.uniform(0.55, 0.98)), 3),
                        "Compound": condition, "Concentration": "", "Cell Type": "Example cells",
                    })

    objects = pd.DataFrame(obj_rows).sort_values(["Row", "Column", "Timepoint", "Field", "Object No"]).reset_index(drop=True)

    # PlateResults: one row per well x timepoint
    inten_col = f"{POPULATION} - Intensity Cell {CHANNEL_NAME} Mean"
    pr_rows = []
    for well, (condition, _, _) in WELLS.items():
        r, c = well_to_rc(well)
        sub = objects[(objects["Row"] == r) & (objects["Column"] == c)]
        for tp in range(1, N_TIMEPOINTS + 1):
            s = sub[sub["Timepoint"] == tp]
            pr_rows.append({
                "Row": r, "Column": c, "Plane": PLANE, "Timepoint": tp,
                "Time [s]": round((tp - 1) * INTERVAL_S, 3),
                "Number of Analyzed Fields": N_FIELDS,
                "Height [µm]": 3.2, "Temperature": 37.0, "Target Temperature": 37.0, "CO2": 5.0, "Target CO2": 5.0,
                "Compound": condition, "Concentration": "", "Cell Type": "Example cells",
                f"{POPULATION} - Number of Objects": int(len(s)),
                f"{inten_col} - Mean per Well": round(float(s[inten_col].mean()), 2) if len(s) else "",
            })
    plate = pd.DataFrame(pr_rows)

    # indexfile: one row per image (well x field x timepoint x channel)
    idx_rows = []
    for well in WELLS:
        r, c = well_to_rc(well)
        for field in range(1, N_FIELDS + 1):
            for tp in range(1, N_TIMEPOINTS + 1):
                fname = f"r{r:02d}c{c:02d}f{field:02d}p{PLANE:02d}-ch{CHANNEL_ID}sk{tp}fk1fl1.tiff"
                idx_rows.append({
                    "Row": r, "Column": c, "Field": field, "Plane": PLANE, "Timepoint": tp,
                    "Sequence": SEQUENCE_OF_TP[tp],
                    "Channel ID": CHANNEL_ID, "Channel Name": CHANNEL_NAME, "Channel Type": "Fluorescence",
                    "Time Stamp": (t0 + timedelta(seconds=(tp - 1) * INTERVAL_S)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
                    "Position X [µm]": round((field - 1) * 700.0, 1), "Position Y [µm]": 0.0, "Position Z [µm]": 3.2,
                    "URL": f"Images/{fname}",
                })
    index = pd.DataFrame(idx_rows)
    return objects, plate, index, {"truth": truth, "background": per_field_bg}


def write_text(path: Path, header: str, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(header)
        df.to_csv(fh, sep="\t", index=False, lineterminator="\n")


def write_images(images_dir: Path, objects: pd.DataFrame, per_field_bg: dict, size: int, rng: np.random.Generator) -> int:
    from PIL import Image

    inten_col = f"{POPULATION} - Intensity Cell {CHANNEL_NAME} Mean"
    scale = size / FIELD_PX
    yy, xx = np.mgrid[0:size, 0:size]
    n = 0
    for (r, c, field), grp in objects.groupby(["Row", "Column", "Field"]):
        well = f"{chr(ord('A') + r - 1)}{c}"
        bg = per_field_bg[(well, field)]
        objs = grp.drop_duplicates("Object No")[["Object No", "X", "Y", "Bounding Box"]]
        for tp in range(1, N_TIMEPOINTS + 1):
            frame = rng.normal(bg, 12.0, (size, size))
            sub = grp[grp["Timepoint"] == tp].set_index("Object No")
            for _, o in objs.iterrows():
                if o["Object No"] not in sub.index:
                    continue
                amp = float(sub.loc[o["Object No"], inten_col]) - bg
                cx, cy = o["X"] * scale, o["Y"] * scale
                nums = [float(v) for v in str(o["Bounding Box"]).strip("[]").split(",")]
                sigma = max(0.8, 0.35 * (nums[2] - nums[0]) * scale)
                frame += amp * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2))
            arr = np.clip(frame, 0, 65535).astype(np.uint16)
            fname = f"r{r:02d}c{c:02d}f{field:02d}p{PLANE:02d}-ch{CHANNEL_ID}sk{tp}fk1fl1.tiff"
            Image.fromarray(arr).save(images_dir / fname)
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "examples" / "example_data"),
                    help="Output root (default: examples/example_data in the repository)")
    ap.add_argument("--date", default="090826", help="Date code MMDDYY for the date folder (default: 090826)")
    ap.add_argument("--seed", type=int, default=0, help="Random seed (default: 0)")
    ap.add_argument("--with-images", action="store_true", help="Also write synthetic TIFF frames for background estimation")
    ap.add_argument("--image-size", type=int, default=64, help="Pixel size of the square synthetic frames (default: 64)")
    ap.add_argument("--clean", action="store_true", help="Delete the date folder first if it exists")
    args = ap.parse_args(argv)

    date6 = args.date
    year_dir = Path(args.out).expanduser().resolve() / f"20{date6[-2:]}"
    date_dir = year_dir / date6
    exp_name = f"Experiment_{date6}_1"
    if args.clean and date_dir.exists():
        shutil.rmtree(date_dir)

    objects, plate, index, meta = build_dataset(args.seed, date6)
    exp_dir = date_dir / "Analysis" / exp_name
    eval_dir = exp_dir / "Evaluation1"
    write_text(eval_dir / "PlateResults.txt",
               harmony_header("PlateResults", exp_name, "Evaluation1"), plate)
    write_text(eval_dir / f"Objects_Population - {POPULATION}.txt",
               harmony_header("Objects", exp_name, "Evaluation1", {"Population": POPULATION}), objects)
    exp_dir.mkdir(parents=True, exist_ok=True)
    index.to_csv(exp_dir / "indexfile.txt", sep="\t", index=False, lineterminator="\n")

    # Ground truth for anyone who wants to check the pipeline's calls against the simulation
    truth = pd.DataFrame.from_dict(meta["truth"], orient="index").rename_axis("object").reset_index()
    truth["well"] = truth["object"].str.split("_").str[0]
    truth["condition"] = truth["well"].map({w: v[0] for w, v in WELLS.items()})
    truth.to_csv(date_dir / f"{date6}_simulation_truth.csv", index=False)

    n_img = 0
    if args.with_images:
        images_dir = date_dir / exp_name / "Images"
        images_dir.mkdir(parents=True, exist_ok=True)
        n_img = write_images(images_dir, objects, meta["background"], args.image_size, np.random.default_rng(args.seed + 1))

    print(f"Wrote synthetic example to {date_dir}")
    print(f"  {len(WELLS)} wells x {N_FIELDS} fields, {objects['Object No'].groupby([objects['Row'], objects['Column'], objects['Field']]).nunique().sum()} objects, "
          f"{N_TIMEPOINTS} timepoints (Stim 1 at tp {STIM1_TP}, Stim 2 at tp {STIM2_TP})")
    print(f"  Objects rows: {len(objects)}   PlateResults rows: {len(plate)}   indexfile rows: {len(index)}"
          + (f"   TIFF frames: {n_img}" if n_img else ""))
    print("Try it:")
    print(f"  pt-run \"{date_dir}\" --expected-n {N_TIMEPOINTS}")
    if n_img:
        print(f"  pt-run \"{date_dir}\" --expected-n {N_TIMEPOINTS} --compute-backgrounds "
              f"--raw-data-root \"{Path(args.out).expanduser().resolve()}\" --background-channel \"{CHANNEL_NAME}\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
