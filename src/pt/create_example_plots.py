# -*- coding: utf-8 -*-
# -*- coding: utf-8 -*-
"""
Create 100% stacked bar charts for responder vs non-responder data.

Creates two types of charts:
1. Per-well stacked bars showing stim2 and stim1 responder proportions
2. Per-well stacked bars showing FOV (Field) proportions
"""
from __future__ import annotations
from typing import Optional
import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pt.pt_utils import OKABE_ITO

# Create example data
np.random.seed(42)

# Example 1: Per-row plot (like the image)
# Rows H1-H12 with responder categories
rows = [f"H{i}" for i in range(1, 13)]
# Mutually exclusive categories including stim1 responders
categories = [
    "Stim2 & Stim1 Responders",  # Both stim2 and stim1
    "Stim2 Only Responders",  # Stim2 but not stim1
    "Stim1 Only Responders",  # Stim1 but not stim2
    "Stim2 Non-responders",  # Valid stim2 status but not responder
    "Stim2 Ambiguous",  # Ambiguous pre-high status
    "Stim2 Unavailable"  # No valid stim2 status
]

# Generate example proportions (must sum to 1.0 for each row)
row_data = {}
for row in rows:
    # Random proportions that sum to 1.0 (6 categories now)
    props = np.random.dirichlet([1.5, 1.0, 0.8, 1.2, 0.5, 0.3], size=1)[0]
    row_data[row] = {
        categories[0]: props[0],
        categories[1]: props[1],
        categories[2]: props[2],
        categories[3]: props[3],
        categories[4]: props[4],
        categories[5]: props[5]
    }

# Create per-row plot
fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
x_pos = np.arange(len(rows))
width = 0.8

# Stack bars for each category
bottoms = np.zeros(len(rows))
# Colors for 6 categories: both, stim2 only, stim1 only, non-responders, ambiguous, unavailable
colors = [OKABE_ITO[2], OKABE_ITO[3], OKABE_ITO[4], OKABE_ITO[5], OKABE_ITO[1], OKABE_ITO[7]]

for i, cat in enumerate(categories):
    values = [row_data[row][cat] for row in rows]
    ax.bar(x_pos, values, width, bottom=bottoms, label=cat, color=colors[i], alpha=0.8)
    bottoms += np.array(values)

ax.set_xlabel("Row", fontsize=12)
ax.set_ylabel("Fraction of Objects", fontsize=12)
ax.set_title("Example: Per-Row Responder Categories\n(100% Stacked)", fontsize=13, fontweight="bold")
ax.set_xticks(x_pos)
ax.set_xticklabels(rows, rotation=45, ha="right")
ax.set_ylim(0, 1.0)
ax.legend(loc="best", frameon=True, fontsize=10)
ax.grid(axis="y", alpha=0.3, linestyle="--")
plt.tight_layout()

outdir = Path("example_plots")
outdir.mkdir(exist_ok=True)
fig.savefig(outdir / "example_per_row_plot.png", dpi=150, bbox_inches="tight")
plt.close(fig)
print(f"Saved: {outdir / 'example_per_row_plot.png'}")

# Example 2: Per-FOV plot (one plot per FOV/Field)
# Interpretation: For each Field (1-6), create a plot showing Fields 1-6 on x-axis
# This shows how responder categories are distributed across all Fields for objects in this FOV
fields = [1, 2, 3, 4, 5, 6]
fovs = [1, 2, 3, 4, 5, 6]  # One plot per Field/FOV

for fov in fovs:
    fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
    x_pos = np.arange(len(fields))
    width = 0.8

    # Generate example proportions for this FOV across different fields
    # This represents: for objects in Field {fov}, how are responder categories distributed across Fields 1-6?
    field_data = {}
    for field in fields:
        props = np.random.dirichlet([1.5, 1.0, 0.8, 1.2, 0.5, 0.3], size=1)[0]
        field_data[field] = {
            categories[0]: props[0],
            categories[1]: props[1],
            categories[2]: props[2],
            categories[3]: props[3],
            categories[4]: props[4],
            categories[5]: props[5]
        }

    # Stack bars for each category
    bottoms = np.zeros(len(fields))

    for i, cat in enumerate(categories):
        values = [field_data[field][cat] for field in fields]
        ax.bar(x_pos, values, width, bottom=bottoms, label=cat, color=colors[i], alpha=0.8)
        bottoms += np.array(values)

    ax.set_xlabel("Field", fontsize=12)
    ax.set_ylabel("Fraction of Objects", fontsize=12)
    ax.set_title(f"Example: Field {fov} (FOV {fov}) - Responder Categories Across Fields\n(100% Stacked)",
                 fontsize=13, fontweight="bold")
    ax.set_xticks(x_pos)
    ax.set_xticklabels([f"Field {f}" for f in fields], rotation=45, ha="right")
    ax.set_ylim(0, 1.0)
    ax.legend(loc="best", frameon=True, fontsize=10)
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    plt.tight_layout()

    fig.savefig(outdir / f"example_fov_{fov}_plot.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {outdir / f'example_fov_{fov}_plot.png'}")

print("\nExample plots created! Please review to confirm this matches your requirements.")
