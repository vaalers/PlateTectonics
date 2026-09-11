from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape
import zipfile


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "PT_Equations_By_Script.docx"


SECTIONS: list[tuple[str, list[str]]] = [
    (
        "compute_fov_background.py",
        [
            "background = mode(pixel intensities), median(pixel intensities), mean(pixel intensities), or percentile_p(pixel intensities)",
            "std = standard deviation of selected pixel intensities",
            "pct_removed(before, after) = ((before - after) / before) * 100",
            "pct_images_missing_for_group = (missing_images / expected_images) * 100",
            "mode (float images) = center of densest histogram bin",
        ],
    ),
    (
        "background_method_report.py",
        [
            "mode = estimate_mode(pixel intensities)",
            "mean = mean(pixel intensities)",
            "median = median(pixel intensities)",
            "std = std(pixel intensities)",
            "mad = median(|x - median|)",
            "threshold_3mad = median + 3 * mad",
            "bright_tail_fraction_3mad = mean(x > threshold_3mad)",
            "bright_tail_fraction_p99 = mean(x >= p99)",
            "pearson_skew = 3 * (mean - median) / std",
            "mean_minus_median = mean - median",
            "supports_mode_over_mean = (pearson_skew >= 0.25) or (bright_tail_fraction_3mad >= 0.03) or (|mode - mean| > max(1.0, mad))",
            "fraction_supporting_mode = mean(supports_mode_over_mean)",
        ],
    ),
    (
        "detect_stim_times.py",
        [
            "stim_timepoints = first timepoint for each sequence >= 3",
            "baseline_range = [min(timepoint where sequence = 2), max(timepoint where sequence = 2)]",
            "time-rank fallback = rank(Time [s]) mapped to 1..N when explicit timepoint is absent",
        ],
    ),
    (
        "per_object_ff0.py",
        [
            "Bounding-box width = max(x2 - x1, 0)",
            "Bounding-box height = max(y2 - y1, 0)",
            "area = width * height",
            "IQR = Q3 - Q1",
            "lower bound = Q1 - k * IQR",
            "upper bound = Q3 + k * IQR",
            "F_corr = raw intensity - background",
            "F0_per_object = mean(F_corr at baseline timepoints for one ROI)",
            "F/F0 = F_corr / F0_per_object",
            "mean_ff0_win = mean(F/F0 within stats window)",
            "std_ff0_win = std(F/F0 within stats window)",
            "AUC_above_1 = trapezoid integral of (F/F0 - 1) over time",
            "slope_ff0_win = least-squares slope of F/F0 vs time in stats window",
            "peak_ff0_win = max(F/F0 in stats window)",
            "baseline_mean_ff0 = mean(F/F0 at baseline)",
            "baseline_std_ff0 = std(F/F0 at baseline)",
            "baseline_threshold_ff0 = baseline_mean_ff0 + 2 * baseline_std_ff0",
            "general responder = peak_global_ff0 >= baseline_threshold_ff0",
            "stim1 responder = general responder and peak_stim1_ff0 >= 2.0",
            "stim2_pre_mean = mean(F/F0 in stim2 pre-window)",
            "stim2_pre_std = std(F/F0 in stim2 pre-window)",
            "stim2_threshold = max(baseline_threshold_ff0, stim2_pre_mean + 2 * stim2_pre_std)",
            "stim2 responder = max(F/F0 after stim2) >= stim2_threshold",
            "stim2_pre_high_frac = mean(F/F0 before stim2 > baseline_threshold_ff0)",
            "stim2 ambiguous = stim2_pre_high_frac >= 0.5",
        ],
    ),
    (
        "filter_post_stats.py",
        [
            "well_base_mean = mean(all baseline F/F0 values across the well)",
            "well_base_std = sample std(all baseline F/F0 values across the well)",
            "outlier_threshold = well_base_mean + baseline_sigma * well_base_std",
            "baseline outlier point = (F/F0 > outlier_threshold) or (F/F0 > baseline_abs)",
            "outlier_fraction = mean(baseline points flagged as outliers)",
            "exclude object if outlier_fraction > baseline_outlier_frac",
            "exclude object if peak_global_ff0 <= peak_threshold",
        ],
    ),
    (
        "spaghetti_plot_per_well.py",
        [
            "Bounding-box area = max(x2 - x1, 0) * max(y2 - y1, 0)",
            "IQR = Q3 - Q1",
            "lower bound = Q1 - k * IQR",
            "upper bound = Q3 + k * IQR",
            "F_corr = raw intensity - background",
            "ROI baseline F0 = mean(F_corr at baseline timepoints for one ROI)",
            "global baseline F0 = mean(F_corr at baseline timepoints) when ROI-specific F0 is unavailable",
            "F/F0 = F_corr / F0",
            "SEM = std / sqrt(n)",
            "mean trace = mean(F/F0 across ROIs at each timepoint)",
        ],
    ),
    (
        "combine_date_summaries.py",
        [
            "stim1_over_general_pct = (stim1_responders / general_responders) * 100",
            "general_nonresponders = general_total - general_responders",
            "stim2_nonresponders = stim2_valid_total - stim2_responders",
            "stim1_nonresponders = n_objects - stim1_responders",
            "stim2_unavailable = n_objects - stim2_available",
        ],
    ),
]


def paragraph(text: str, *, bold: bool = False) -> str:
    safe = escape(text)
    if bold:
        run_props = "<w:rPr><w:b/></w:rPr>"
    else:
        run_props = ""
    return (
        "<w:p>"
        "<w:r>"
        f"{run_props}<w:t xml:space=\"preserve\">{safe}</w:t>"
        "</w:r>"
        "</w:p>"
    )


def build_document_xml() -> str:
    parts: list[str] = []
    parts.append(paragraph("PT Equations By Script", bold=True))
    parts.append(paragraph("This document groups the main analysis equations used in the PT codebase by script."))
    for script_name, equations in SECTIONS:
        parts.append(paragraph(""))
        parts.append(paragraph(script_name, bold=True))
        for eq in equations:
            parts.append(paragraph(f"- {eq}"))
    body = "".join(parts)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:wpc=\"http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas\" "
        "xmlns:mc=\"http://schemas.openxmlformats.org/markup-compatibility/2006\" "
        "xmlns:o=\"urn:schemas-microsoft-com:office:office\" "
        "xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" "
        "xmlns:m=\"http://schemas.openxmlformats.org/officeDocument/2006/math\" "
        "xmlns:v=\"urn:schemas-microsoft-com:vml\" "
        "xmlns:wp14=\"http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing\" "
        "xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" "
        "xmlns:w10=\"urn:schemas-microsoft-com:office:word\" "
        "xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" "
        "xmlns:w14=\"http://schemas.microsoft.com/office/word/2010/wordml\" "
        "xmlns:w15=\"http://schemas.microsoft.com/office/word/2012/wordml\" "
        "xmlns:wpg=\"http://schemas.microsoft.com/office/word/2010/wordprocessingGroup\" "
        "xmlns:wpi=\"http://schemas.microsoft.com/office/word/2010/wordprocessingInk\" "
        "xmlns:wne=\"http://schemas.microsoft.com/office/word/2006/wordml\" "
        "xmlns:wps=\"http://schemas.microsoft.com/office/word/2010/wordprocessingShape\" "
        "mc:Ignorable=\"w14 w15 wp14\">"
        f"<w:body>{body}<w:sectPr><w:pgSz w:w=\"12240\" w:h=\"15840\"/><w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\" w:header=\"708\" w:footer=\"708\" w:gutter=\"0\"/></w:sectPr></w:body>"
        "</w:document>"
    )


def write_docx(out_path: Path) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""
    core = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>PT Equations By Script</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
</cp:coreProperties>
"""
    app = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
</Properties>
"""
    document_xml = build_document_xml()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/core.xml", core)
        zf.writestr("docProps/app.xml", app)
        zf.writestr("word/document.xml", document_xml)


if __name__ == "__main__":
    write_docx(OUT)
    print(OUT)
