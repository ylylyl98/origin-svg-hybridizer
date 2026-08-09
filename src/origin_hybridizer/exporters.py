from __future__ import annotations

from pathlib import Path
import base64
import os
import shutil
import subprocess

from PySide6.QtCore import QByteArray, QRectF, QSizeF
from PySide6.QtGui import QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgRenderer

from .geometry import Rect, length_to_inches


def find_inkscape() -> str | None:
    found = shutil.which("inkscape") or shutil.which("inkscape.exe")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Inkscape/bin/inkscape.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Inkscape/bin/inkscape.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def powerpoint_available() -> bool:
    if os.name != "nt":
        return False
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Microsoft Office/root/Office16/POWERPNT.EXE",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Microsoft Office/root/Office16/POWERPNT.EXE",
    ]
    return any(candidate.is_file() for candidate in candidates)


def export_emf_with_powerpoint(svg_path: Path, output_path: Path) -> None:
    """Use desktop PowerPoint's native SVG importer and EMF exporter."""
    if os.name != "nt" or not powerpoint_available():
        raise RuntimeError(
            "Neither Inkscape nor desktop PowerPoint was found. Install either application "
            "to enable optional EMF export."
        )
    # Paths travel through environment variables so spaces, apostrophes and
    # non-ASCII characters cannot alter the PowerShell program.
    script = r'''
$ErrorActionPreference = "Stop"
$ppt = $null
$presentation = $null
$slide = $null
$shape = $null
$createdApplication = $false
try {
    try {
        $ppt = [Runtime.InteropServices.Marshal]::GetActiveObject("PowerPoint.Application")
    } catch {
        $ppt = New-Object -ComObject PowerPoint.Application
        $createdApplication = $true
    }
    $ppt.DisplayAlerts = 1
    $presentation = $ppt.Presentations.Add(0)
    $slide = $presentation.Slides.Add(1, 12)
    $shape = $slide.Shapes.AddPicture($env:ORIGIN_HYBRID_INPUT, 0, -1, 0, 0)
    $shape.Export($env:ORIGIN_HYBRID_OUTPUT, 5)
    if (-not (Test-Path -LiteralPath $env:ORIGIN_HYBRID_OUTPUT)) {
        throw "PowerPoint did not create the EMF file."
    }
} finally {
    if ($presentation -ne $null) { $presentation.Close() }
    if ($shape -ne $null) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($shape) }
    if ($slide -ne $null) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($slide) }
    if ($presentation -ne $null) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($presentation) }
    if ($createdApplication -and $ppt -ne $null) { $ppt.Quit() }
    if ($ppt -ne $null) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($ppt) }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
'''
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    environment = os.environ.copy()
    environment["ORIGIN_HYBRID_INPUT"] = str(svg_path.resolve())
    environment["ORIGIN_HYBRID_OUTPUT"] = str(output_path.resolve())
    powershell = os.environ.get("SystemRoot", "C:/Windows") + "/System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size < 100:
        detail = (result.stderr or result.stdout or "Unknown PowerPoint automation error").strip()
        raise RuntimeError(f"PowerPoint could not create the EMF file:\n{detail}")


def export_with_inkscape(svg_path: Path, output_path: Path, export_type: str) -> None:
    executable = find_inkscape()
    if not executable:
        raise RuntimeError(
            "Inkscape was not found. Install Inkscape and restart the app to enable "
            f"{export_type.upper()} export. The hybrid SVG itself does not require Inkscape."
        )
    result = subprocess.run(
        [
            executable,
            str(svg_path),
            f"--export-type={export_type}",
            f"--export-filename={output_path}",
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not output_path.is_file():
        detail = (result.stderr or result.stdout or "Unknown Inkscape error").strip()
        raise RuntimeError(f"Inkscape could not create the {export_type.upper()} file:\n{detail}")


def export_pdf_with_qt(svg_bytes: bytes, output_path: Path, view_box: Rect, width: str | None, height: str | None) -> None:
    """Vector-preserving PDF fallback when Inkscape is unavailable."""
    from .svg_document import qt_preview_bytes

    svg_bytes = qt_preview_bytes(svg_bytes)
    width_in = length_to_inches(width, view_box.width)
    height_in = length_to_inches(height, view_box.height)
    writer = QPdfWriter(str(output_path))
    writer.setResolution(600)
    writer.setPageSize(QPageSize(
        # QPageSize accepts millimetres here.
        QSizeF(width_in * 25.4, height_in * 25.4),
        QPageSize.Unit.Millimeter,
        "Origin figure",
        QPageSize.SizeMatchPolicy.ExactMatch,
    ))
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if not renderer.isValid():
        raise RuntimeError("Qt could not render the generated hybrid SVG")
    painter = QPainter(writer)
    try:
        target = QRectF(0, 0, writer.width(), writer.height())
        renderer.render(painter, target)
    finally:
        painter.end()
