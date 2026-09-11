#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build per-well Excel workbooks for any Evaluation* under a given root.
Usage:
  python3 phenix_to_xlsx_batch.py "<root path>"

Root can be:
- a date folder (.../082125)
- an Analysis folder (.../082125/Analysis)
- a single Experiment folder (.../Analysis/Experiment_082125_2)
- a single Evaluation folder (.../Experiment_082125_2/Evaluation3)

For each Evaluation:
- If <EvaluationX>_OperaPhenix_per-well.xlsx exists -> skip
- Else, if PlateResults*.txt and Objects_Population*.txt exist -> build Excel
  (if Harmony exported several populations, choose one with --population or the
  PT_OBJECTS_POPULATION environment variable)
Return code:
- 0 if at least one Evaluation was processed/found under root
- 1 if root not found or no Evaluation* found
"""

import os, sys, re
from pathlib import Path
import argparse
import pandas as pd
import statistics as stats
from pt.pt_utils import first_header_like, row_to_letter

ENCODINGS = ["utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be"]
DELIMS    = ["\t", ",", ";"]

EXP_RE   = re.compile(r"^Experiment_\d{6}_\d+$", re.I)
EVAL_RE  = re.compile(r"^Evaluation[ _]?\d+$", re.I)
ROW_LETTERS = "ABCDEFGH"

def eprint(*a, **k): print(*a, file=sys.stderr, **k)

# Harmony writes one "Objects_Population - <population name>.txt" per exported population.
# The population name is user-defined in the Harmony analysis sequence, so nothing here
# assumes a particular name. Set PT_OBJECTS_POPULATION (or --population) to pick one
# when several were exported.
POPULATION_ENV = "PT_OBJECTS_POPULATION"
OBJECTS_POPULATION: str | None = os.environ.get(POPULATION_ENV) or None


def find_objects_file(eval_dir: Path, population: str | None = None) -> Path | None:
    """Locate the Harmony per-object export in an Evaluation folder."""
    population = population or OBJECTS_POPULATION
    hits = sorted(eval_dir.glob("Objects_Population*.txt"))
    if not hits:
        return None
    if population:
        sel = [h for h in hits if population.lower() in h.name.lower()]
        if sel:
            return sel[0]
        eprint(f"[WARN] No Objects_Population file matching '{population}' in {eval_dir}; using {hits[0].name}")
    elif len(hits) > 1:
        eprint(f"[WARN] Multiple Objects_Population files in {eval_dir}; using {hits[0].name} "
               f"(use --population to choose)")
    return hits[0]


def pick_intensity_column(cols) -> str | None:
    """Choose the per-object intensity column from a Harmony Objects export.

    Preference order: an exact 'Intensity' column; '<Population> - Intensity <Region|Cell|...> ... Mean';
    any 'Intensity ... Mean'; any column mentioning Intensity. Per-well means, counts and
    dispersion statistics are never chosen.
    """
    cols = [str(c) for c in cols]
    if "Intensity" in cols:
        return "Intensity"
    cands = [c for c in cols if re.search(r"Intensity", c, re.I)
             and not re.search(r"Mean per Well|Number of Objects|StdDev|Std\b|\bCV\b|\bSum\b|\bMax\b|\bMin\b", c, re.I)]
    for pat in (r"Intensity\s+(Region|Cell|Nucleus|Cytoplasm|Membrane).*Mean", r"Intensity.*Mean", r"Intensity"):
        for c in cands:
            if re.search(pat, c, re.I):
                return c
    return None

def is_experiment_dir(p: Path) -> bool:
    return p.is_dir() and EXP_RE.match(p.name) is not None

def is_eval_dir(p: Path) -> bool:
    return p.is_dir() and EVAL_RE.match(p.name) is not None

def sniff_delim(lines):
    nonempty = [ln for ln in lines if ln and ln.strip()]
    if not nonempty:
        return "\t"
    scores = {}
    head = nonempty[:100]
    for ln in nonempty:
        txt = ln.strip()
        if txt.startswith('"') and txt.endswith('"'):
            continue
        head.append(ln)
        if len(head) > 200:
            break

    for d in DELIMS:
        per = [ln.count(d) for ln in head]
        scores[d] = float(stats.median(per)) if per else 0.0
    return max(scores, key=scores.get)

def find_header_after_data_tag(lines, delim):
    # header is the line immediately following a line equal to "[Data]"
    for i, ln in enumerate(lines[:99]):
        if ln.strip().strip('"') == "[Data]":
            return i + 1
    # fallback: first line containing both Row and Column tokens
    for i, ln in enumerate(lines[:100]):
        cols = [c.strip() for c in ln.split(delim)]
        if "Row" in cols and "Column" in cols:
            return i
    return 0

def normalize_headers(cols):
    out = []
    for c in cols:
        c = str(c)
        c = c.replace("¬µ", "µ")
        c = re.sub(r"\s+", " ", c).strip()
        out.append(c)
    return out

def find_col(cols, pattern):
    """Return first column whose name matches the regex pattern (case-insensitive)."""
    for c in cols:
        if re.search(pattern, str(c), flags=re.I):
            return c
    return None

def read_table_robust(path: Path) -> pd.DataFrame:
    # Always use tab delimiter
    for enc in ENCODINGS:
        try:
            text = path.read_text(encoding=enc, errors="replace")
            lines = text.splitlines()
            hdr = find_header_after_data_tag(lines, "\t")
            df = pd.read_csv(path, sep="\t", encoding=enc, engine="python",
                             skiprows=hdr, header=0, dtype=str)
            df.columns = normalize_headers(df.columns)
            return df
        except Exception:
            continue
    raise RuntimeError(f"Could not read {path.name} with supported encodings.")

def _row_to_letter(val):
    s = str(val).strip()
    if s.isdigit():
        i = int(s)
        if 1 <= i <= len(ROW_LETTERS):
            return ROW_LETTERS[i-1]
    if len(s) == 1 and s.upper() in ROW_LETTERS:
        return s.upper()
    return s

def compute_well(df: pd.DataFrame) -> pd.DataFrame:
    # Prefer Row/Column; otherwise keep existing Well if present
    cols = {c: c for c in df.columns}
    row_col = cols.get("Row") or cols.get("row")
    col_col = cols.get("Column") or cols.get("Col") or cols.get("column")
    if row_col and col_col:
        row_letters = df[row_col].map(_row_to_letter)
        try:
            col_nums = df[col_col].astype(int).astype(str)
        except Exception:
            col_nums = df[col_col].astype(str).str.extract(r"(\d+)")[0].fillna(df[col_col].astype(str))
        df["Well"] = (row_letters.fillna("")) + (col_nums.fillna(""))
    return df

def find_evaluations(root: Path):
    if is_eval_dir(root):
        return [root]
    out = []
    if is_experiment_dir(root):
        out.extend([p for p in root.iterdir() if is_eval_dir(p)])
    else:
        # date or analysis or higher
        # direct Analysis
        if (root / "Analysis").is_dir():
            root = root / "Analysis"
        # immediate experiments
        for exp in root.iterdir() if root.is_dir() else []:
            if is_experiment_dir(exp):
                out.extend([p for p in exp.iterdir() if is_eval_dir(p)])
        # recursive fallback
        out.extend([p for p in root.glob("**/Analysis/Experiment_*/*") if is_eval_dir(p)])
    # de-dup
    seen = set(); uniq = []
    for p in sorted(out):
        if p not in seen:
            seen.add(p); uniq.append(p)
    return uniq

def has_raw_pair(eval_dir: Path) -> bool:
    pr = list(eval_dir.glob("PlateResults*.txt"))
    obj = find_objects_file(eval_dir)
    return bool(pr and obj)

def build_xlsx_for_eval(eval_dir: Path) -> Path | None:
    # Name file with experiment + evaluation
    exp_name = eval_dir.parent.name              # e.g., Experiment_082825_2
    eval_name = eval_dir.name                    # e.g., Evaluation2
    xlsx = eval_dir / f"{exp_name}_{eval_name}_per-well.xlsx"

    if xlsx.exists():
        return xlsx
    if not has_raw_pair(eval_dir):
        return None

    # Locate raw files
    obj_path = find_objects_file(eval_dir)
    pr_candidates = list(eval_dir.glob("PlateResults*.txt"))
    pr_path = pr_candidates[0] if pr_candidates else None

    try:
        df = read_table_robust(obj_path)
        pr = read_table_robust(pr_path) if pr_path else None

        # ---- Clean up the Objects table: drop plate/environment metadata that Harmony
        # repeats on every object row, plus any per-well means (those come from PlateResults).
        z = ["Plane", "Number of Analyzed Fields", "Height [µm]", "Temperature", "Target Temperature", "CO2",
             "Target CO2", "Compound", "Concentration", "Cell Type", "Cell Count", "Unnamed: 17"]
        z += [c for c in df.columns if re.search(r"Mean per Well", str(c))]
        to_drop = [c for c in z if c in df.columns]
        if to_drop:
            df = df.drop(columns=to_drop)
        # Standardize the per-object intensity column name to "Intensity" (downstream scripts rely on it).
        inten = pick_intensity_column(df.columns)
        if inten is None:
            eprint(f"[WARN] No per-object intensity column found in {obj_path.name}; columns: {list(df.columns)[:12]}...")
        elif inten != "Intensity":
            df = df.rename(columns={inten: "Intensity"})
        # Merge object-level with per-well metrics
        merged = df.merge(pr, on=["Row", "Column", "Timepoint"], how="left", validate="m:1")

        # replace your conversion loop with this
        NUMERIC_SAFE = ["Timepoint", "Field", "Object No", "X", "Y", "Time [s]", "Intensity"]
        for c in NUMERIC_SAFE:
            if c in merged.columns:
                merged[c] = pd.to_numeric(merged[c], errors="coerce")
        # leave "Bounding Box" as-is; it's a string like "[x1,y1,x2,y2]"
        merged = merged.sort_values(["Row", "Column", "Timepoint", "Field"], kind="stable")

        merged["_Well"] = merged.apply(
            lambda r: f"{row_to_letter(r['Row'])}{int(r['Column']) if pd.notna(r['Column']) else r['Column']}",
            axis=1
        )
        # Column order (tolerant to µ variants)
        posx = first_header_like(merged.columns, r'^Position\s*X', r'X\s*\[\s*µ?m\s*\]')
        posy = first_header_like(merged.columns, r'^Position\s*Y', r'Y\s*\[\s*µ?m\s*\]')
        desired = ["Row", "Column", "Timepoint", "Field", "Object No", "X", "Y", "Bounding Box"]
        CAN_TIME = "Time [s]"
        CAN_NUMOBJS = next((c for c in merged.columns if re.search(r"Number\s*of\s*Objects", str(c), re.I)),
                           "Number of Objects")
        CAN_MEANWELL = "Intensity"
        if posx: desired.append(posx)
        if posy: desired.append(posy)
        desired += [CAN_TIME, CAN_NUMOBJS, CAN_MEANWELL]
        final_cols = [c for c in desired if c in merged.columns] + [c for c in merged.columns if
                                                                    c not in desired and c != "_Well"]

    except Exception as e:
        eprint(f"[WARN] Could not parse raw files in {eval_dir.name}: {e}")
        return None

    # Ensure Well is present
    df = compute_well(merged)

    # =========================
    # Build Plate_Overview data
    # =========================
    # 1) Try from PlateResults: flexible match for "Number of Objects"
    overview_vals = {}  # (row_letter, col_str) -> value (int)
    n_rows_guess, n_cols_guess = 8, 12

    def letter_index(L):
        L = str(L).strip().upper()
        return ROW_LETTERS.index(L) + 1 if L in ROW_LETTERS else None

    used_rows, used_cols = set(), set()

    if pr is not None:
        col_row = find_col(pr.columns, r"^Row$")
        col_col = find_col(pr.columns, r"^Column$")
        # allow many instrument variants for Num Objects
        col_numobjs = find_col(pr.columns, r"Number\s*of\s*Objects(\b|[^a-zA-Z])")


        if col_row and col_col and col_numobjs:
            pr_num = pr[[col_row, col_col, col_numobjs]].copy()
            pr_num[col_row] = pd.to_numeric(pr_num[col_row], errors="coerce")
            pr_num[col_col] = pd.to_numeric(pr_num[col_col], errors="coerce")
            pr_num["val"]   = pd.to_numeric(pr_num[col_numobjs], errors="coerce")

            # median across timepoints/fields if duplicated
            well_summary = (
                pr_num.dropna(subset=[col_row, col_col])  # keep only real wells
                     .groupby([col_row, col_col], as_index=False)["val"].median()
            )

            for _, r in well_summary.iterrows():
                rL = _row_to_letter(r[col_row])
                cS = str(int(r[col_col]))
                if rL and re.match(r"^[A-Za-z]$", rL) and re.match(r"^\d+$", cS):
                    used_rows.add(rL); used_cols.add(int(cS))
                    v = r["val"]
                    if pd.notna(v):
                        overview_vals[(rL, cS)] = int(round(float(v)))

    # 2) Fallback from Objects: count rows per Well
    if not overview_vals:
        if "Well" in df.columns:
            counts = df.groupby("Well", dropna=True).size()
            for well, cnt in counts.items():
                m = re.match(r"([A-Za-z]+)(\d+)", str(well))
                if not m:
                    continue
                rL = m.group(1).upper()
                cS = m.group(2)
                if len(rL) == 1 and rL in ROW_LETTERS:
                    used_rows.add(rL); used_cols.add(int(cS))
                    overview_vals[(rL, cS)] = int(cnt)

    # dimensions
    if used_rows:
        n_rows_guess = max(n_rows_guess, max(letter_index(r) for r in used_rows if letter_index(r)))
    if used_cols:
        n_cols_guess = max(n_cols_guess, max(used_cols))

    row_letters = ROW_LETTERS[:n_rows_guess]
    overview = pd.DataFrame(index=list(row_letters), columns=[str(i) for i in range(1, n_cols_guess+1)], dtype="float")

    for (rL, cS), v in overview_vals.items():
        if rL in overview.index and cS in overview.columns:
            overview.loc[rL, cS] = v

    overview = overview.map(lambda x: int(x) if pd.notna(x) else x)

    # =========================
    # Write Excel (summary + per-well, no 'all')
    # =========================
    try:
        with pd.ExcelWriter(xlsx) as xw:
            overview.to_excel(xw, sheet_name="Plate_Overview")
            if "Well" in merged.columns:
                def sort_key(w):
                    m = re.match(r"([A-Za-z]+)(\d+)", str(w))
                    if not m:
                        return (str(w), 0)
                    return (m.group(1).upper(), int(m.group(2)))

                wells = sorted(merged["Well"].dropna().unique(), key=sort_key)
                for w in wells:
                    dfw = merged.loc[merged["Well"] == w, final_cols]
                    sheet = str(w)[:31]
                    dfw.to_excel(xw, sheet_name=sheet, index=False)
            else:
                df.to_excel(xw, sheet_name="Unknown_Well", index=False)
    except Exception as e:
        eprint(f"[WARN] Failed writing {xlsx.name}: {e}")
        return None

    eprint(f"[OK] Wrote {xlsx} (Plate_Overview + per-well; no 'all')")
    return xlsx


def main():
    ap = argparse.ArgumentParser(description="Convert Opera Phenix / Harmony text exports to per-well Excel workbooks.")
    ap.add_argument("root", help="Date / Analysis / Experiment / Evaluation path")
    ap.add_argument("--population", default=None,
                    help="Substring of the Harmony population name to use when several "
                         "'Objects_Population - <name>.txt' files exist (default: env PT_OBJECTS_POPULATION, "
                         "else the first file found)")
    args = ap.parse_args()
    global OBJECTS_POPULATION
    if args.population:
        OBJECTS_POPULATION = args.population
    root = Path(args.root).expanduser().resolve()
    if not root.exists():
        eprint(f"Not found: {root}")
        sys.exit(1)

    evals = find_evaluations(root)
    if not evals:
        eprint(f"No Evaluation* folders found under {root}")
        sys.exit(1)

    any_processed = False
    for ed in evals:
        # new name includes experiment + evaluation
        out_new = ed / f"{ed.parent.name}_{ed.name}_per-well.xlsx"
        if out_new.exists():
            eprint(f"[SKIP] {out_new.name} already exists in {ed}")
            any_processed = True
            continue
        if has_raw_pair(ed):
            eprint(f"[BUILD] {ed}")
            res = build_xlsx_for_eval(ed)
            any_processed = any_processed or (res is not None)
        else:
            eprint(f"[MISS] {ed} lacks PlateResults/Objects files; skipping")

    sys.exit(0 if any_processed else 1)

if __name__ == "__main__":
    main()
