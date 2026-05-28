"""Generate PDF reports from structured SRE report data using LaTeX + Tectonic."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class SnapshotTable(BaseModel):
    """A data table within a snapshot section."""

    headers: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)


class SnapshotSection(BaseModel):
    """One Coral SQL snapshot block in the report."""

    title: str = ""
    sql_query: str = ""
    description: str = ""
    table: SnapshotTable | None = None


class SreReport(BaseModel):
    """Structured data model for the SRE incident report."""

    incident_id: str = ""
    alert_name: str = ""
    pipeline_service: str = ""
    severity: str = "WARNING"
    investigation_duration: str = ""
    status: str = "RESOLVED & CAPTURED"
    executive_summary: str = ""
    validated_findings: list[str] = Field(default_factory=list)
    non_validated_claims: list[str] = Field(default_factory=list)
    snapshot_sections: list[SnapshotSection] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    cited_evidence: list[str] = Field(default_factory=list)


def _escape_latex(text: str) -> str:
    """Escape special LaTeX characters in plain text."""
    replacements = [
        ("\\", "\\textbackslash{}"),
        ("&", "\\&"),
        ("%", "\\%"),
        ("$", "\\$"),
        ("#", "\\#"),
        ("_", "\\_"),
        ("{", "\\{"),
        ("}", "\\}"),
        ("~", "\\textasciitilde{}"),
        ("^", "\\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text


def _escape_latex_code(text: str) -> str:
    """Minimal escaping for lstlisting content (backslash and braces)."""
    return text.replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")


def _generate_tex_content(report: SreReport) -> str:
    """Build LaTeX source from an SreReport model."""
    lang = os.getenv("OPENSRE_LANGUAGE", "en").strip().lower()
    is_vi = lang in ("vi", "vietnamese")

    # Section titles in Vietnamese or English
    titles = {
        "executive_summary": "Tóm tắt" if is_vi else "Executive Summary",
        "validated_findings": "Phát hiện đã xác thực" if is_vi else "Validated Findings",
        "non_validated_claims": "Giả thuyết chưa xác thực"
        if is_vi
        else "Non-Validated Claims (Inferred / Hypotheses)",
        "snapshot": "Snapshot hệ thống (Coral SQL)"
        if is_vi
        else "Coral Query State Capture (System Snapshot)",
        "recommended_actions": "Khuyến nghị" if is_vi else "Recommended Actions",
        "cited_evidence": "Bằng chứng" if is_vi else "Cited Evidence",
    }

    lines: list[str] = [
        r"\documentclass[10pt,a4paper]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage[margin=0.8in]{geometry}",
        r"\usepackage{xcolor}",
        r"\usepackage{titlesec}",
        r"\usepackage{fancyhdr}",
        r"\usepackage{tcolorbox}",
        r"\usepackage{tabularx}",
        r"\usepackage{booktabs}",
        r"\usepackage{listings}",
        r"\usepackage{enumitem}",
        r"\usepackage{hyperref}",
        r"\definecolor{primary}{HTML}{1A365D}",
        r"\definecolor{secondary}{HTML}{0D9488}",
        r"\definecolor{accent}{HTML}{E11D48}",
        r"\definecolor{bglight}{HTML}{F8FAFC}",
        r"\definecolor{textdark}{HTML}{334155}",
        r"\definecolor{bordergray}{HTML}{E2E8F0}",
        r"\pagestyle{fancy}",
        r"\fancyhf{}",
        r"\fancyhead[L]{\fontsize{8}{10}\selectfont \color{textdark}\textbf{VSRE} | "
        + ("Báo cáo sự cố" if is_vi else "Incident Investigation Report")
        + "}",
        r"\fancyhead[R]{\fontsize{8}{10}\selectfont \color{textdark}"
        + ("Mã sự cố" if is_vi else "Incident ID")
        + r": \texttt{"
        + _escape_latex(report.incident_id)
        + r"}}",
        r"\fancyfoot[C]{\fontsize{8}{10}\selectfont \color{textdark}Page \thepage}",
        r"\renewcommand{\headrulewidth}{0.5pt}",
        r"\setlength{\headheight}{12pt}",
        r"\titleformat{\section}{\color{primary}\normalfont\large\bfseries}{}{0pt}{}",
        r"\titlespacing*{\section}{0pt}{14pt}{6pt}",
        r"\lstset{basicstyle=\ttfamily\small\color{textdark},breaklines=true,backgroundcolor=\color{bglight},frame=single,rulecolor=\color{bordergray},keywordstyle=\color{primary}\bfseries,commentstyle=\color{gray},stringstyle=\color{secondary},language=SQL,showstringspaces=false}",
        r"\renewcommand{\familydefault}{\sfdefault}",
        r"\begin{document}",
        r"\begin{center}",
        r"{\fontsize{18}{22}\selectfont \color{primary}\textbf{"
        + ("BÁO CÁO ĐIỀU TRA SỰ CỐ" if is_vi else "INCIDENT INVESTIGATION REPORT")
        + r"}} \\",
        r"\vspace{2pt}",
        r"{\fontsize{10}{12}\selectfont \color{textdark}Generated automatically by VSRE Pipeline | \today}",
        r"\end{center}",
        r"\vspace{10pt}",
        r"\begin{tcolorbox}[colback=bglight,colframe=primary,leftrule=4pt,arc=3pt,boxrule=0.5pt,left=12pt,right=12pt,top=10pt,bottom=10pt]",
        r"\begin{tabularx}{\textwidth}{@{} l X @{}}",
        r"\textbf{"
        + ("Tên cảnh báo" if is_vi else "Alert Name")
        + r":} & \color{primary}\textbf{"
        + _escape_latex(report.alert_name)
        + r"} \\",
        r"\textbf{"
        + ("Dịch vụ" if is_vi else "Pipeline/Service")
        + r":} & \texttt{"
        + _escape_latex(report.pipeline_service)
        + r"} \\",
        r"\textbf{"
        + ("Mức độ nghiêm trọng" if is_vi else "Severity Level")
        + r":} & \color{accent}\textbf{"
        + _escape_latex(report.severity)
        + r"} \\",
        r"\textbf{"
        + ("Thời gian điều tra" if is_vi else "Investigation Duration")
        + r":} & "
        + _escape_latex(report.investigation_duration)
        + r" \\",
        r"\textbf{"
        + ("Trạng thái" if is_vi else "Status")
        + r":} & \color{secondary}\textbf{"
        + _escape_latex(report.status)
        + r"} \\",
        r"\end{tabularx}",
        r"\end{tcolorbox}",
        r"\vspace{5pt}",
        r"\section{" + titles["executive_summary"] + "}",
        _escape_latex(report.executive_summary),
        r"\vspace{5pt}",
        r"\section{" + titles["validated_findings"] + "}",
        r"\begin{itemize}[leftmargin=1.5em, itemsep=2pt]",
    ]

    for finding in report.validated_findings:
        lines.append(r"    \item " + _escape_latex(finding))
    lines.append(r"\end{itemize}")
    lines.append(r"\vspace{5pt}")

    if report.non_validated_claims:
        lines.append(r"\section{" + titles["non_validated_claims"] + "}")
        lines.append(r"\begin{itemize}[leftmargin=1.5em, itemsep=2pt]")
        for claim in report.non_validated_claims:
            lines.append(r"    \item " + _escape_latex(claim))
        lines.append(r"\end{itemize}")
        lines.append(r"\vspace{5pt}")

    if report.snapshot_sections:
        lines.append(r"\newpage")
        lines.append(r"\section{" + titles["snapshot"] + "}")
        for section in report.snapshot_sections:
            if section.title:
                lines.append(r"\textbf{" + _escape_latex(section.title) + r"}")
            if section.description:
                lines.append(_escape_latex(section.description))
            if section.sql_query:
                lines.append(r"\begin{lstlisting}")
                lines.append(_escape_latex_code(section.sql_query))
                lines.append(r"\end{lstlisting}")
            if section.table and section.table.headers:
                lines.append(r"\vspace{8pt}")
                lines.append(
                    r"\noindent\begin{tabularx}{\textwidth}{@{} "
                    + " l " * len(section.table.headers)
                    + " X @{}}"
                )
                lines.append(r"    \toprule")
                lines.append(
                    r"    "
                    + " & ".join(
                        r"\textbf{" + _escape_latex(h) + "}" for h in section.table.headers
                    )
                    + r" \\"
                )
                lines.append(r"    \midrule")
                for row in section.table.rows:
                    escaped_row = [r"\texttt{" + _escape_latex(str(cell)) + "}" for cell in row]
                    lines.append(r"    " + " & ".join(escaped_row) + r" \\")
                lines.append(r"    \bottomrule")
                lines.append(r"\end{tabularx}")
                lines.append(r"\vspace{10pt}")

    if report.recommended_actions:
        lines.append(r"\section{" + titles["recommended_actions"] + "}")
        lines.append(r"\begin{enumerate}[leftmargin=1.5em, itemsep=2pt]")
        for action in report.recommended_actions:
            lines.append(r"    \item " + _escape_latex(action))
        lines.append(r"\end{enumerate}")
        lines.append(r"\vspace{5pt}")

    if report.cited_evidence:
        lines.append(r"\section{" + titles["cited_evidence"] + "}")
        lines.append(r"\begin{itemize}[leftmargin=1.5em, itemsep=1pt]")
        for evidence in report.cited_evidence:
            lines.append(r"    \item " + _escape_latex(evidence))
        lines.append(r"\end{itemize}")

    lines.append(r"\end{document}")
    return "\n".join(lines)


def _find_tectonic_binary() -> str | None:
    """Find tectonic binary: project-local > env > PATH."""
    project_bin = Path(__file__).parent.parent.parent / "tools" / "tectonic.exe"
    if project_bin.exists():
        return str(project_bin)
    if env_path := os.environ.get("TECTONIC_BINARY"):
        return env_path if Path(env_path).exists() else None
    return shutil.which("tectonic")


def generate_pdf(report: SreReport | dict[str, Any], output_dir: str | None = None) -> Path:
    """Generate a PDF from SreReport data.

    Args:
        report: An SreReport model or dict matching its schema.
        output_dir: Directory to write the PDF. Defaults to a temp dir.

    Returns:
        Path to the generated PDF file.
    """
    if isinstance(report, dict):
        report = SreReport.model_validate(report)

    tex_content = _generate_tex_content(report)

    out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="sre_report_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    incident_id = report.incident_id or "unknown"
    safe_id = "".join(c if c.isalnum() else "_" for c in incident_id)
    tex_path = out_dir / f"{safe_id}.tex"
    pdf_path = out_dir / f"{safe_id}.pdf"

    tex_path.write_text(tex_content, encoding="utf-8")

    tectonic = _find_tectonic_binary()
    if not tectonic:
        raise RuntimeError(
            "tectonic binary not found. Set TECTONIC_BINARY or place tectonic.exe in tools/"
        )

    result = subprocess.run(
        [tectonic, "--outfmt", "pdf", str(tex_path)],
        capture_output=True,
        text=True,
        cwd=str(out_dir),
    )
    if result.returncode != 0:
        raise RuntimeError(f"tectonic failed: {result.stderr}")

    if not pdf_path.exists():
        # Tectonic sometimes names output differently
        candidates = list(out_dir.glob("*.pdf"))
        if candidates:
            pdf_path = candidates[0]
        else:
            raise RuntimeError(f"PDF not generated. tectonic stdout: {result.stdout}")

    return pdf_path
