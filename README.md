# Origin SVG Hybridizer

A small Windows desktop app that converts a large, all-vector Origin heatmap SVG into a compact **hybrid SVG**:

- dense colormap/heatmap cells become one embedded high-resolution PNG;
- confidently recognized, finely sampled color bars become an editable continuous SVG gradient;
- axes, tick marks, labels, arrows, annotations, legends, and other graphics remain the original editable SVG objects;
- the original SVG file is never overwritten;
- the result opens directly in PowerPoint and Adobe Illustrator;
- optional EMF output is available through Inkscape, and PDF remains a secondary option.

The app modifies the exported Origin SVG object tree. Heatmap cells keep their exact colors; continuous color bars interpolate between the original cell-center colors in sRGB. Frames, ticks, labels, and black or white endcaps remain original SVG objects.

## Download the Windows app

**[Download the latest Windows version from GitHub Releases](https://github.com/ylylyl98/origin-svg-hybridizer/releases/latest)**

1. Open the link above and expand **Assets** if necessary.
2. Download `Origin-SVG-Hybridizer-Windows-vX.Y.Z.zip` - do **not** download the automatically generated "Source code" ZIP.
3. Right-click the downloaded ZIP and choose **Extract All**.
4. Open the extracted `Origin SVG Hybridizer` folder and run `Origin SVG Hybridizer.exe`.

No Python installation is required for the downloadable Windows app. Keep the complete extracted folder together because the `.exe` uses the included runtime files. If Windows SmartScreen appears for an unsigned community application, choose **More info > Run anyway** only after confirming that the download came from this repository.

Version 1.4.5 converts confidently recognized, finely sampled color bars into editable continuous sRGB gradients while preserving their frames, ticks, text, and black or white endcaps. The heatmap remains one lossless PNG with exact cell colors. Qt previews now select Symbol explicitly when it is the first requested font family, so Origin's Symbol `D` displays as Delta; exported SVG text and font lists remain unchanged.

This release also adds six export correctness safeguards: it rejects the source path and existing filesystem aliases before rendering, applies shared ancestor opacity only once, rejects selected cells interrupted by retained artwork when one PNG cannot preserve paint order, preserves reflected raster orientation, and uses the compatible SVG renderer for stylesheet-driven fills and rounded rectangles. Unsupported shared filters, blending, and explicitly inherited compositing effects also fail clearly before raster work. Exact-cell PNG generation caches at most one constructed row, bounding cache memory while reusing repeated rows.

Version 1.4.4 keeps partially visible edge rows instead of dropping them. Origin masks often clip through the first or last heatmap row, and the app previously treated a cell as visible only when its center fell inside the masked area, so an edge row cut in half could be removed during export without ever being rasterized. Detection now treats any cell whose rectangle overlaps the visible area as visible: the visible half of a clipped edge row is preserved in the raster, fully hidden off-plot cells are still removed, and the output remains one lossless PNG that renders pixel-identical to the original figure.

Version 1.4.3 additionally removes the invisible masked-out heatmap cells that Origin writes behind its clip. Detection now selects the full same-series cell grid, keeps the visible region as the raster, and deletes the hidden clipped cells during export instead of leaving them embedded in the SVG. The rendered figure is unchanged, the heatmap remains one lossless PNG, and the output size for a ~30 MB masked Origin heatmap drops from about 9 MB to roughly 0.3 MB. Color bars, axes, text, and other artwork stay original SVG objects, and export now fails the safety check if any detected heatmap cell is left behind.

Version 1.4.2 understands Origin plot masks before running general density detection. It rasterizes the complete cell matrix visible inside the plot, ignores off-canvas cells hidden by Origin and keeps color bars separate. Export now stops instead of replacing vectors when a generated raster is less than 2% visible, preventing nearly transparent hybrid figures. Complete matrices still use exact SVG fill colors with no polygon-edge antialiasing, color interpolation, smoothing, or newly blended colors. Text, axes, ticks, arrows, annotations, curves, markers, legends, frames, and color-bar artwork remain original SVG objects.

Version 1.4.1 added the resizable preview/sidebar divider, scrollable settings, screen-aware window sizing, always-visible export controls, and exact-cell matrix conversion.

The app also supports Origin exports that wrap plot objects in a nested SVG viewport, including correct previews, visible embedded colormaps, PowerPoint-compatible image placement, non-empty PDF output, and PowerPoint-based EMF conversion when Inkscape is unavailable. Large-file loading, detection, manual selection, preview generation, and export run in background workers so Windows does not mark the app as unresponsive. A 0-100% progress window reports the current processing stage and continuously updated elapsed time.

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

## Run from source for development

To edit the code or run directly from Python, install 64-bit [Python 3.10 or newer](https://www.python.org/downloads/windows/), open PowerShell in this folder, and run:

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

Every push to `main` also runs the **Build Windows application** workflow on GitHub. Tagged versions such as `v1.4.5` are published automatically on the repository's [Releases page](https://github.com/ylylyl98/origin-svg-hybridizer/releases), where users can download the Windows ZIP without installing build tools. Ordinary development builds remain available from the workflow's artifact section for 30 days.

## How automatic detection works

The detector looks for nested SVG groups containing many repeated, filled vector primitives. It ignores text, stroke-only axes, lines, images, gradients, definitions, large page backgrounds, and named annotation artwork such as arrows, labels, ticks, legends, markers, and scale objects. A spatial-density fallback handles flatter SVG structures.

The heatmap selection keeps color bars separate. During export, a dense, contiguous horizontal or vertical color-bar ramp becomes one vector rectangle filled by an SVG `linearGradient`. Each stop uses an original cell's exact color at its geometric center, with that color extended constantly to the nearest endpoint. Black or white endcaps stay separate, and the original outline, ticks, numbers, title, transforms, viewport, and stacking order remain editable and unchanged. This intentionally smooths colors between centers; the heatmap PNG still uses exact cell colors without interpolation.

Recognition is conservative: a ramp needs at least 16 fine rectangular interior cells, many distinct gradually changing colors, and specific color-bar naming or a nearby frame with perpendicular ticks. Small or categorical legends, gapped or overlapping bars, masked heatmap edge strips, selected raster cells, existing gradients, referenced cells, CSS, animation, scripts, and unsupported cell paint or effects are retained unchanged. Export verifies each approved replacement and all retained artwork, and its editable-vector count reflects the actual output.

When the selected primitives form a complete Origin matrix, the app reads the exact color of every cell and writes a lossless PNG without rasterizing shared polygon edges. Every opaque bitmap pixel is assigned to one original cell, so the heatmap introduces no interpolation or antialiased seam colors. If the selection is irregular, incomplete, transformed, overlapping, or uses complex SVG painting, the app safely falls back to its general SVG renderer. The PNG is inserted at the original stacking position, and only the selected source primitives and validated color-bar interiors are replaced. All other artwork comes directly from the Origin SVG. SVG, PDF, and EMF exports share this conversion pipeline.

The manual rectangle is useful when:

- a figure contains an unusually small heatmap;
- Origin exported the heatmap without a dedicated group;
- several dense decorative regions confuse automatic detection;
- you want to rasterize one panel instead of all detected panels.

## Output size and DPI

600 dpi is the default because it is appropriate for most publication figures. DPI is calculated from the SVG's physical `width` and `height`. If Origin omitted physical units, the SVG standard of 96 screen pixels per inch is used.

The app refuses extremely large raster allocations and asks for a tighter rectangle or lower DPI instead of exhausting system memory.

SVGs around 30 MB or larger can still take several seconds to parse and preview. Version 1.4.2 displays staged progress for file reading, XML parsing, object-ID preparation, geometry indexing, mask-aware exact-matrix verification, cell-color mapping, PNG compression, safety verification, and final output. Wait for it to reach 100% rather than opening the same file again.

## Optional PDF

PDF is intentionally secondary. If Inkscape is installed, the app uses it. Otherwise, the PySide6 vector PDF backend is used. Always keep the hybrid SVG for later editing.

## Run the tests

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The tests generate both ordinary and nested-viewport Origin-style vector heatmaps, verify automatic and manual selection, confirm that complete matrices introduce no antialiased or interpolated colors, exercise the compatible fallback for irregular selections, confirm that text and axes remain SVG objects, verify that masked-out Origin cells are removed and partially visible edge rows are kept, and validate the exported SVG with Qt's renderer.

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
