#!/bin/bash
# Launch the PTHTS graphical interface in the pt-py310 conda environment.
# Usage: ./run_gui.sh [streamlit options, e.g. --server.port 8502]
set -e
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate pt-py310
exec pt-gui "$@"
