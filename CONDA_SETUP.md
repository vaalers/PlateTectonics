# Conda Environment Setup

PTHTS is developed and tested in a conda environment named `pt-py310` (Python 3.10).

## Create or update the environment

```bash
# create
conda env create -f environment.yml

# or update an existing one after pulling changes
conda env update -f environment.yml --prune
```

`environment.yml` ends with a `pip: -e .` entry, so the `pt` package and its console
commands (`pt-run`, `pt-reorg`, `phenix-to-xlsx-batch`, ...) are installed in editable mode.
If you created the environment another way, run `pip install -e .` from the repository root.

## Run commands

Activate the environment, then use the console commands directly:

```bash
conda activate pt-py310
pt-run /path/to/Analyzed/081825 --help
```

Or run without activating:

```bash
conda run -n pt-py310 pt-run /path/to/Analyzed/081825
```

Wrappers are provided for convenience:

| Script | Platform | Purpose |
|---|---|---|
| `run_pt_pipeline.sh` | macOS / Linux | Activates `pt-py310` and runs the main pipeline |
| `run_gui.sh` / `run_gui.bat` | macOS, Linux / Windows | Launches the graphical interface (`pt-gui`) |
| `run_all_dates.bat` | Windows | Runs `pt-run` for every `MMDDYY` folder under a root |
| `run_build_sample_manifest.bat` | Windows | Runs `build-sample-manifest` |

The batch files locate `conda.exe` via `CONDA_EXE`, then common install locations
(`%USERPROFILE%\miniconda3`, `%LOCALAPPDATA%\miniconda3`, `%USERPROFILE%\anaconda3`,
`%ProgramData%\miniconda3`), then `PATH`.

## Troubleshooting

- **`conda: command not found`** — add conda to your shell:
  `export PATH="$HOME/miniconda3/bin:$PATH"` (or `anaconda3`), or open an Anaconda Prompt on Windows.
- **Environment does not activate in a script** — check `conda --version`, `conda env list`,
  and that `source "$(conda info --base)/etc/profile.d/conda.sh"` runs before `conda activate`.
- **Permission denied on `.sh` wrappers** — `chmod +x run_pt_pipeline.sh`.
