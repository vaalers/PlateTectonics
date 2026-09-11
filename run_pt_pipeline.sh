#!/bin/bash
# Wrapper: run the PT pipeline with the pt-py310 conda environment activated.
# Usage: ./run_pt_pipeline.sh <date_folder> [pt-run options...]
set -e
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate pt-py310

echo "Current conda environment: $CONDA_DEFAULT_ENV"
echo "Python: $(which python3)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/src/pt/run_pt_pipeline.py" "$@"
