# Origin SVG Hybridizer

A small Windows desktop app that converts a large, all-vector Origin heatmap SVG into a compact **hybrid SVG**:

- dense colormap/heatmap cells become one embedded high-resolution PNG;
- axes, tick marks, labels, arrows, annotations, legends, and other graphics remain the original editable SVG objects;
- the original SVG file is never overwritten;
- the result opens directly in PowerPoint and Adobe Illustrator;
- optional EMF output is available through Inkscape, and PDF remains a secondary option.

The app modifies the exported Origin SVG object tree. It does not recreate or redraw the figure.

Version 1.3.3 uses one publication-quality export path. Dense heatmap cells are the only automatic raster target; text, axes, ticks, arrows, annotations, curves, markers, legends, frames, and color-bar artwork remain original SVG objects. A color-coded review shows the raster area in orange, retained vectors in blue, and live text in green. Rectangle tools can replace the raster choice, add another dense area, or explicitly keep an area editable. Every export runs the full safety check that the page geometry, SVG text, retained-vector count, coordinates, colors, line widths, and styles match the source.

Version 1.3.3 also prevents moire/interference patterns in very dense regular Origin meshes. It recognizes the original color grid (for example, 1024 × 141 samples), reconstructs it without polygon-edge seams, and interpolates it once to the final 600-dpi bitmap—the same visual model used by Origin's smooth PNG export. Irregular heatmaps retain the high-resolution vector-rendering fallback. The final DPI, page size, source colors, live text, and retained vectors are not reduced or changed.

The app also supports Origin exports that wrap plot objects in a nested SVG viewport, including correct previews, visible embedded colormaps, PowerPoint-compatible image placement, non-empty PDF output, and PowerPoint-based EMF conversion when Inkscape is unavailable. Large-file loading, detection, manual selection, preview generation, and export run in background workers so Windows does not mark the app as unresponsive. A 0-100% progress window reports the current processing stage and continuously updated elapsed time.

## Download the Windows app — no Python required

Most users should download the ready-to-use Windows build. You do **not** need to install Python or build the app yourself.

1. Open [Build Windows application](https://github.com/ylylyl98/origin-svg-hybridizer/actions/workflows/build-windows.yml?query=branch%3Amain).
2. Open the newest successful run marked with a green check.
3. Scroll to **Artifacts** and download `origin-svg-hybridizer-windows-v1.3.3`.
4. Right-click the downloaded ZIP and select **Extract All**.
5. Open the extracted `Origin SVG Hybridizer` folder and double-click `Origin SVG Hybridizer.exe`.

Keep the complete extracted folder together. The EXE uses the accompanying `_internal` files, although users never need to open or edit those files. You can create a Desktop shortcut to the EXE or pin it to the taskbar.

> GitHub may require you to sign in before downloading an Actions artifact. The artifact is retained for 30 days; every new build creates a fresh download.

## Use the app

1. Drop an Origin `.svg` file onto the preview, or click **Open SVG**.
2. Review the editability colors: **orange** is the raster area, **blue** is retained vector artwork, and **green** is live SVG text.
3. If the automatic choice needs adjustment, enable **Adjust with a rectangle**, choose **Replace raster selection**, **Add area to raster**, or **Keep area editable**, then drag over the relevant area.
4. Leave **Raster DPI** at 600 for publication work, then click **Export hybrid SVG**.

The source SVG remains unchanged. The default output name ends in `_hybrid.svg`.

## Recommended PowerPoint and Illustrator workflow

### PowerPoint

Use **Insert > Pictures > This Device** and choose the hybrid SVG. Supported Microsoft 365 and recent desktop Office versions can [insert and edit SVG graphics](https://support.microsoft.com/en-au/office/edit-svg-images-in-microsoft-365-69f29d39-194a-4072-8c35-dbe5e7ea528c), while the embedded heatmap stays compact. If a particular Office workflow needs the older Enhanced Metafile format, use **EMF...** in the app.

### Illustrator

Open or place the hybrid SVG. The heatmap appears as one embedded image; SVG text, paths, axes, arrows, and annotations remain separate objects. Install the same fonts used by Origin to avoid font substitution.

For maximum editability, keep the hybrid SVG as the master figure even if you also make an EMF or PDF.

## Optional EMF support

EMF export uses the free desktop version of [Inkscape](https://inkscape.org/release/) when available. If Inkscape is absent but desktop PowerPoint is installed, version 1.1.3 uses PowerPoint's native SVG importer and Enhanced Metafile exporter automatically. Hybrid SVG export does **not** need either converter.

The EMF contains the same embedded raster heatmap plus vector artwork. EMF text handling depends on the installed font and Inkscape version, so the SVG should remain the editable master.

## Run from source — developers only

This section is only for people who want to edit the Python code. Ordinary Windows users should download the prebuilt app above.

Install 64-bit [Python 3.10 or newer](https://www.python.org/downloads/windows/). During setup, enable **Add Python to PATH**. Then double-click `run_app.bat`; on the first run, it creates a private environment and installs PySide6. Later starts are immediate.

Alternatively, open PowerShell in the project folder and run:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m origin_hybridizer
```

You can also open an SVG immediately:

```powershell
.\.venv\Scripts\python.exe -m origin_hybridizer "C:\path\to\figure.svg"
```

## Build the standalone Windows app

Double-click `build_windows.bat`. It installs PyInstaller into the private `.venv` and creates:

```text
dist\Origin SVG Hybridizer\Origin SVG Hybridizer.exe
```

The build is windowed, uses the icon in `packaging`, and includes the Python/PySide6 runtime. Keep the complete `Origin SVG Hybridizer` folder together; the `.exe` inside can be used to create a Desktop shortcut or pinned to the taskbar. Generated `build`, `dist`, and `.spec` files are intentionally excluded from Git.

Every push to `main` also runs the **Build Windows application** workflow on GitHub. After it finishes, download the `origin-svg-hybridizer-windows-v1.3.3` artifact using the instructions at the top of this README. This provides the same standalone application as a ZIP without requiring local build tools.

## How automatic detection works

The detector looks for nested SVG groups containing many repeated, filled vector primitives. It ignores text, stroke-only axes, lines, images, gradients, definitions, large page backgrounds, and named annotation artwork such as arrows, labels, ticks, legends, markers, and scale objects. A spatial-density fallback handles flatter SVG structures.

For the supplied Origin figure, the automatic result rasterizes 10,206 heatmap polygons while keeping 39 text objects and 166 other vector objects editable. The 122 polygon segments that form the color-bar ramp remain vector because that ramp is already compact; its outline, ticks, numbers, and title also remain editable.

Only the selected filled primitives are rendered to a transparent PNG. The PNG is inserted at the original stacking position, and only those source primitives are removed. Everything else comes directly from the Origin SVG.

The manual rectangle is useful when:

- a figure contains an unusually small heatmap;
- Origin exported the heatmap without a dedicated group;
- several dense decorative regions confuse automatic detection;
- you want to rasterize one panel instead of all detected panels.

## Output size and DPI

600 dpi is the default because it is appropriate for most publication figures. DPI is calculated from the SVG's physical `width` and `height`. If Origin omitted physical units, the SVG standard of 96 screen pixels per inch is used.

The app refuses extremely large raster allocations and asks for a tighter rectangle or lower DPI instead of exhausting system memory.

SVGs around 30 MB or larger can still take several seconds to parse and preview. Version 1.3.1 removes the previous quadratic slowdown when deleting very large heatmap groups: a 30 MB, 180,000-cell stress figure completes the full 600-dpi publication workflow in about 12 seconds on the development machine, compared with about 134 seconds previously. The staged progress window reports file reading, XML parsing, object-ID preparation, geometry indexing, heatmap detection, review rendering, rasterization, PNG compression, full safety verification, and final output. Wait for it to reach 100% rather than opening the same file again.

## Optional PDF

PDF is intentionally secondary. If Inkscape is installed, the app uses it. Otherwise, the PySide6 vector PDF backend is used. Always keep the hybrid SVG for later editing.

## Run the tests

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests generate both ordinary and nested-viewport Origin-style vector heatmaps, verify automatic and manual selection, confirm that the heatmap cells are replaced by one embedded PNG, confirm that text and axes remain SVG objects, and validate the exported SVG with Qt's renderer.

## Troubleshooting

- **Automatic detection selected the wrong area:** select **Adjust with a rectangle** and use **Replace raster selection**, **Add area to raster**, or **Keep area editable**. The color bar remains vector automatically in the supplied figure.
- **No objects were detected:** re-export from Origin as SVG rather than a bitmap format, then use manual selection.
- **Text looks different in PowerPoint or Illustrator:** install the font used in Origin, or choose a common TrueType/OpenType font in Origin before export.
- **EMF export is unavailable:** install either desktop PowerPoint or Inkscape, then restart the app.
- **The file is still large:** draw a tighter heatmap rectangle, confirm it includes the dense cells, and export again.

## Project layout

```text
src/origin_hybridizer/app.py          PySide6 interface and preview
src/origin_hybridizer/svg_document.py SVG analysis and hybrid export
src/origin_hybridizer/geometry.py     rectangles, units, and transforms
src/origin_hybridizer/exporters.py    optional EMF/PDF backends
tests/test_hybridizer.py               conversion tests
examples/make_demo_svg.py              creates a demonstration SVG
```
