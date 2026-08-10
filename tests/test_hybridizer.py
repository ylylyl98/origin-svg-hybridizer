from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, QPoint, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from origin_hybridizer.geometry import Rect
from origin_hybridizer.app import MainWindow
from origin_hybridizer.svg_document import SvgDocument, local_name, qt_preview_bytes


APP = QApplication.instance() or QApplication([])


def make_origin_svg(path: Path) -> tuple[int, bytes]:
    cols, rows = 36, 24
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="6in" height="4in" viewBox="0 0 600 400">',
        '<rect id="page" x="0" y="0" width="600" height="400" fill="white"/>',
        '<g id="plot-layer">',
        '<g id="heatmap" transform="translate(10 5)">',
    ]
    for row in range(rows):
        for col in range(cols):
            color = f"#{(col * 7) % 256:02x}{(row * 11) % 256:02x}{((row + col) * 5) % 256:02x}"
            parts.append(
                f'<rect x="{120 + col * 8}" y="{55 + row * 8}" width="8.1" height="8.1" fill="{color}"/>'
            )
    parts.extend([
        '<polygon id="in-plot-arrow-head" points="330,105 320,104 326,96" fill="white"/>',
        '</g>',
        '<g id="axes" fill="none" stroke="#111"><path id="axis-path" d="M130 60 V252 H418"/></g>',
        '<text id="x-label" x="274" y="290" font-family="Arial">Vbg (V)</text>',
        '<path id="arrow-line" d="M300 90 L330 105" fill="none" stroke="white"/>',
        '<polygon id="arrow-head" points="330,105 320,104 326,96" fill="white"/>',
        '</g>',
        '</svg>',
    ])
    data = "".join(parts).encode("utf-8")
    path.write_bytes(data)
    return cols * rows, data


def make_nested_origin_svg(path: Path) -> tuple[int, bytes]:
    cols, rows = 34, 24
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0,0 600,400" width="6in" height="4in">',
        '<svg viewBox="0,0 1200,800" width="600" height="400" preserveAspectRatio="none">',
        '<defs><mask id="plot-mask"><rect x="180" y="100" width="850" height="520" fill="white"/></mask></defs>',
        '<g id="nested-heatmap" mask="url(#plot-mask)">',
    ]
    for row in range(rows):
        for col in range(cols):
            x, y = 200 + col * 24, 120 + row * 20
            color = f"#{(col * 7) % 256:02x}{(row * 11) % 256:02x}{((row + col) * 5) % 256:02x}"
            parts.append(
                f'<polygon points="{x},{y} {x + 24},{y} {x + 24},{y + 20} {x},{y + 20}" fill="{color}" stroke="none"/>'
            )
    parts.extend([
        '</g>',
        '<path id="nested-axis" d="M200 120 V600 H1016" fill="none" stroke="black"/>',
        '<text id="nested-label" x="600" y="670">Editable label</text>',
        '</svg>',
        '</svg>',
    ])
    data = "".join(parts).encode("utf-8")
    path.write_bytes(data)
    return cols * rows, data


def make_masked_overflow_svg(path: Path) -> tuple[int, bytes]:
    """Origin-style grid whose off-plot cells would dominate ordinary density detection."""
    cols, rows = 40, 12
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="4in" height="4in">',
        '<svg viewBox="0 0 1000 1000" width="100" height="100" preserveAspectRatio="none">',
        '<defs><mask id="visible-plot"><rect x="200" y="100" width="600" height="600" fill="white"/></mask></defs>',
        '<g id="overflow-grid" mask="url(#visible-plot)">',
    ]
    for row in range(rows):
        for col in range(cols):
            x, y = -300 + col * 40, 100 + row * 50
            color = f"#{(col * 13) % 256:02x}{(row * 19) % 256:02x}{((row + col) * 7) % 256:02x}"
            parts.append(
                f'<polygon points="{x},{y} {x + 40},{y} {x + 40},{y + 50} {x},{y + 50}" fill="{color}" stroke="none"/>'
            )
    parts.extend([
        '</g>',
        '<polygon id="colorbar-cell-1" points="700,40 750,40 750,70 700,70" fill="#0044ff"/>',
        '<polygon id="colorbar-cell-2" points="750,40 800,40 800,70 750,70" fill="#ff2200"/>',
        '<path id="axis" d="M200 100 V700 H800" fill="none" stroke="black"/>',
        '<text id="label" x="500" y="760">Editable label</text>',
        '</svg>',
        '</svg>',
    ])
    data = "".join(parts).encode("utf-8")
    path.write_bytes(data)
    visible_columns = 16
    return visible_columns * rows, cols * rows, data


def make_masked_partial_edge_svg(path: Path) -> tuple[int, bytes]:
    """Origin-style grid whose mask clips through the first row of cells."""
    cols, rows = 12, 8
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="4in" height="4in">',
        '<svg viewBox="0 0 1000 1000" width="100" height="100" preserveAspectRatio="none">',
        '<defs><mask id="visible-plot"><rect x="200" y="240" width="600" height="600" fill="white"/></mask></defs>',
        '<g id="edge-grid" mask="url(#visible-plot)">',
    ]
    for row in range(rows):
        for col in range(cols):
            x, y = 200 + col * 50, 200 + row * 50
            color = f"#{(col * 13) % 256:02x}{(row * 19) % 256:02x}{((row + col) * 7) % 256:02x}"
            parts.append(
                f'<polygon points="{x},{y} {x + 50},{y} {x + 50},{y + 50} {x},{y + 50}" fill="{color}" stroke="none"/>'
            )
    parts.extend([
        '</g>',
        '<path id="axis" d="M200 240 V840 H800" fill="none" stroke="black"/>',
        '<text id="label" x="500" y="880">Editable label</text>',
        '</svg>',
        '</svg>',
    ])
    data = "".join(parts).encode("utf-8")
    path.write_bytes(data)
    return cols * rows, data




class HybridizerTests(unittest.TestCase):
    def test_masked_overflow_grid_removes_clipped_cells_and_keeps_visible_region(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "masked-overflow.svg"
            _, expected_total_cells, source_bytes = make_masked_overflow_svg(source)
            document = SvgDocument(source)
            detection = document.detect_heatmap(minimum_primitives=30)

            # The full Origin grid behind the mask is selected so the clipped
            # (invisible) cells are removed during export as well.
            self.assertEqual(detection.count, expected_total_cells)
            self.assertTrue(document._exact_cell_matrix(detection.element_ids))
            self.assertAlmostEqual(detection.rect.x, 20, delta=0.2)
            self.assertAlmostEqual(detection.rect.y, 10, delta=0.2)
            self.assertAlmostEqual(detection.rect.width, 60, delta=0.2)
            self.assertAlmostEqual(detection.rect.height, 60, delta=0.2)
            self.assertNotIn("colorbar-cell-1", detection.element_ids)
            self.assertNotIn("colorbar-cell-2", detection.element_ids)

            output = Path(directory) / "masked-overflow-hybrid.svg"
            stats = document.export_hybrid_svg(output, detection, dpi=150)
            root = ET.fromstring(output.read_bytes())
            image = next(e for e in root.iter() if e.get("data-origin-hybridizer") == "heatmap-raster")
            self.assertEqual(image.get("data-origin-rasterization"), "exact-cell-matrix")
            href = next(value for key, value in image.attrib.items() if key.endswith("href"))
            raster = QImage.fromData(base64.b64decode(href.split(",", 1)[1]), "PNG")
            alpha_values = [
                raster.pixelColor(x, y).alpha()
                for y in range(raster.height())
                for x in range(raster.width())
            ]
            self.assertGreater(sum(alpha == 255 for alpha in alpha_values) / len(alpha_values), 0.99)
            self.assertFalse(any(0 < alpha < 255 for alpha in alpha_values))
            self.assertEqual(stats.rasterization_method, "Exact Origin cells (no interpolation)")
            remaining_ids = {e.get("id") for e in root.iter() if e.get("id")}
            self.assertFalse(remaining_ids & set(detection.element_ids))
            remaining_polygons = [e for e in root.iter() if local_name(e.tag) == "polygon"]
            self.assertEqual(len(remaining_polygons), 2)  # only the color-bar cells survive
            self.assertIsNotNone(next((e for e in root.iter() if e.get("id") == "colorbar-cell-1"), None))
            self.assertIsNotNone(next((e for e in root.iter() if e.get("id") == "label"), None))
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_masked_grid_keeps_partially_visible_edge_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "masked-partial-edge.svg"
            expected_cells, source_bytes = make_masked_partial_edge_svg(source)
            document = SvgDocument(source)
            detection = document.detect_heatmap(minimum_primitives=30)

            self.assertEqual(detection.count, expected_cells)
            self.assertTrue(document._exact_cell_matrix(detection.element_ids))
            # The mask clips through the first row: its center lies above the
            # visible rectangle, but its lower half is visible, so the raster
            # region must start at the mask edge instead of skipping the row.
            self.assertAlmostEqual(detection.rect.y, 24, delta=0.3)

            output = Path(directory) / "masked-partial-edge-hybrid.svg"
            document.export_hybrid_svg(output, detection, dpi=150)
            root = ET.fromstring(output.read_bytes())
            image = next(e for e in root.iter() if e.get("data-origin-hybridizer") == "heatmap-raster")
            self.assertEqual(image.get("data-origin-rasterization"), "exact-cell-matrix")
            href = next(value for key, value in image.attrib.items() if key.endswith("href"))
            raster = QImage.fromData(base64.b64decode(href.split(",", 1)[1]), "PNG")
            self.assertFalse(raster.isNull())
            top_opaque = next(
                (
                    y
                    for y in range(raster.height())
                    if any(raster.pixelColor(x, y).alpha() > 0 for x in range(raster.width()))
                ),
                None,
            )
            self.assertIsNotNone(top_opaque, "the partially visible first row must be rasterized")
            remaining_ids = {e.get("id") for e in root.iter() if e.get("id")}
            self.assertFalse(remaining_ids & set(detection.element_ids))
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_nearly_transparent_raster_is_rejected(self) -> None:
        image = QImage(100, 100, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        image.setPixelColor(0, 0, QColor("red"))
        with self.assertRaisesRegex(RuntimeError, "visible raster"):
            SvgDocument._require_visible_raster(image)

    def test_nested_origin_svg_viewport_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "nested-origin.svg"
            expected_cells, source_bytes = make_nested_origin_svg(source)
            load_updates = []
            document = SvgDocument(source, lambda value, message: load_updates.append((value, message)))
            self.assertEqual(load_updates[-1][0], 100)
            self.assertTrue(all(a[0] <= b[0] for a, b in zip(load_updates, load_updates[1:])))
            detection_updates = []
            detection = document.detect_heatmap(
                minimum_primitives=30,
                progress=lambda value, message: detection_updates.append((value, message)),
            )
            self.assertEqual(detection_updates[-1][0], 100)
            self.assertEqual(len(document.infos), expected_cells)
            self.assertEqual(detection.count, expected_cells)
            self.assertAlmostEqual(detection.rect.x, 100, delta=1)
            self.assertAlmostEqual(detection.rect.y, 60, delta=1)

            output = Path(directory) / "nested-hybrid.svg"
            export_updates = []
            document.export_hybrid_svg(
                output,
                detection,
                dpi=150,
                progress=lambda value, message: export_updates.append((value, message)),
            )
            self.assertEqual(export_updates[-1][0], 100)
            self.assertTrue(all(a[0] <= b[0] for a, b in zip(export_updates, export_updates[1:])))
            output_bytes = output.read_bytes()
            root = ET.fromstring(output_bytes)
            images = [e for e in root.iter() if local_name(e.tag) == "image"]
            self.assertEqual(len(images), 1)
            self.assertEqual(images[0].get("data-origin-rasterization"), "exact-cell-matrix")
            self.assertIsNone(images[0].get("transform"), "PowerPoint-safe nested images should not need a transform")
            self.assertAlmostEqual(float(images[0].get("x", "0")), 200, delta=2)
            self.assertAlmostEqual(float(images[0].get("y", "0")), 120, delta=2)
            self.assertEqual(sum(local_name(e.tag) == "polygon" for e in root.iter()), 0)
            self.assertIsNotNone(next((e for e in root.iter() if e.get("id") == "nested-axis"), None))
            self.assertIsNotNone(next((e for e in root.iter() if e.get("id") == "nested-label"), None))
            self.assertTrue(QSvgRenderer(QByteArray(output_bytes)).isValid())

            href = next(value for key, value in images[0].attrib.items() if key.endswith("href"))
            raster = QImage.fromData(base64.b64decode(href.split(",", 1)[1]), "PNG")
            self.assertFalse(raster.isNull())
            source_colors = {
                QColor(element.get("fill", "")).rgba()
                for element in document.root.iter()
                if element.get("id") in detection.element_ids
            }
            raster_colors = {
                raster.pixelColor(x, y).rgba()
                for y in range(raster.height())
                for x in range(raster.width())
                if raster.pixelColor(x, y).alpha() > 0
            }
            self.assertTrue(raster_colors)
            self.assertTrue(
                raster_colors <= source_colors,
                "Exact matrix export must not invent antialiased or interpolated colors",
            )
            sampled = {
                raster.pixelColor(x, y).rgba()
                for y in range(0, raster.height(), max(1, raster.height() // 12))
                for x in range(0, raster.width(), max(1, raster.width() // 12))
                if raster.pixelColor(x, y).alpha() > 0
            }
            self.assertGreater(len(sampled), 4, "The embedded heatmap must contain visible color data")

            preview_renderer = QSvgRenderer(QByteArray(qt_preview_bytes(source_bytes)))
            preview = QImage(600, 400, QImage.Format.Format_ARGB32_Premultiplied)
            preview.fill(QColor(0, 0, 0, 0))
            painter = QPainter(preview)
            preview_renderer.render(painter, QRectF(0, 0, 600, 400))
            painter.end()
            visible_samples = sum(
                preview.pixelColor(x, y).alpha() > 0
                for y in range(0, preview.height(), 10)
                for x in range(0, preview.width(), 10)
            )
            self.assertGreater(visible_samples, 100, "The nested Origin SVG preview must not be blank")
            self.assertEqual(source.read_bytes(), source_bytes)

    def test_auto_detection_and_hybrid_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "origin.svg"
            expected_cells, source_bytes = make_origin_svg(source)
            document = SvgDocument(source)
            detection = document.detect_heatmap(minimum_primitives=30)

            self.assertEqual(detection.count, expected_cells)
            self.assertNotIn("arrow-head", detection.element_ids)
            self.assertNotIn("in-plot-arrow-head", detection.element_ids)
            self.assertGreater(detection.rect.x, 125)
            self.assertLess(detection.rect.x, 132)
            self.assertGreater(detection.rect.y, 55)
            self.assertLess(detection.rect.y, 62)

            output = Path(directory) / "hybrid.svg"
            stats = document.export_hybrid_svg(output, detection, dpi=150)
            output_bytes = output.read_bytes()
            root = ET.fromstring(output_bytes)
            images = [e for e in root.iter() if local_name(e.tag) == "image"]
            rects = [e for e in root.iter() if local_name(e.tag) == "rect"]
            texts = [e for e in root.iter() if local_name(e.tag) == "text"]

            self.assertEqual(len(images), 1)
            self.assertEqual(
                images[0].get("data-origin-rasterization"),
                "svg-renderer",
                "Overlapping rectangles must use the established compatible fallback",
            )
            self.assertEqual(len(rects), 1, "Only the original page rectangle should remain")
            self.assertEqual(len(texts), 1)
            self.assertEqual(texts[0].text, "Vbg (V)")
            self.assertIsNotNone(next((e for e in root.iter() if e.get("id") == "axis-path"), None))
            self.assertIsNotNone(next((e for e in root.iter() if e.get("id") == "arrow-head"), None))
            self.assertIsNotNone(next((e for e in root.iter() if e.get("id") == "in-plot-arrow-head"), None))
            self.assertNotIn(b"__origin_hybridizer_", output_bytes)
            href = next(value for key, value in images[0].attrib.items() if key.endswith("href"))
            self.assertTrue(href.startswith("data:image/png;base64,"))
            encoded = href.split(",", 1)[1]
            self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertGreater(stats.raster_width, 400)
            self.assertGreater(stats.raster_height, 250)
            self.assertTrue(QSvgRenderer(QByteArray(output_bytes)).isValid())
            self.assertEqual(source.read_bytes(), source_bytes, "The original SVG must not be changed")
            summary = document.classification_summary(detection)
            self.assertEqual(summary.raster_objects, expected_cells)
            self.assertEqual(summary.editable_text_objects, 1)
            self.assertGreaterEqual(summary.editable_vector_objects, 5)
            self.assertGreaterEqual(summary.protected_filled_objects, 3)

            review = document.render_review_preview(detection, max_side=500)
            self.assertFalse(review.isNull())
            self.assertGreater(review.width(), 0)
            self.assertGreater(review.height(), 0)

    def test_manual_rectangle_keeps_vectors_outside_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "origin.svg"
            expected_cells, _ = make_origin_svg(source)
            document = SvgDocument(source)
            detection = document.select_rectangle(Rect(125, 55, 300, 205))
            self.assertEqual(detection.method, "Manual rectangle")
            self.assertEqual(detection.count, expected_cells)

    def test_too_small_manual_rectangle_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "origin.svg"
            make_origin_svg(source)
            document = SvgDocument(source)
            with self.assertRaises(ValueError):
                document.select_rectangle(Rect(5, 5, 3, 3))

    def test_manual_add_and_keep_editable_adjustments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "origin.svg"
            expected_cells, _ = make_origin_svg(source)
            document = SvgDocument(source)
            detection = document.detect_heatmap(minimum_primitives=30)
            left_half = Rect(
                detection.rect.x,
                detection.rect.y,
                detection.rect.width / 2,
                detection.rect.height,
            )
            reduced = document.adjust_detection(detection, left_half, "exclude")
            self.assertGreater(reduced.count, 0)
            self.assertLess(reduced.count, expected_cells)
            restored = document.adjust_detection(reduced, left_half, "add")
            self.assertEqual(restored.count, expected_cells)


class ResponsiveUiTests(unittest.TestCase):
    def test_export_footer_stays_visible_when_sidebar_content_overflows(self) -> None:
        window = MainWindow()
        try:
            window.resize(760, 520)
            window.result_label.setText(
                "Long export result information " * 120
            )
            window.result_card.show()
            window.show()
            APP.processEvents()

            self.assertGreater(
                window.sidebar_scroll.verticalScrollBar().maximum(),
                0,
                "Overflowing settings should activate the sidebar scrollbar",
            )
            self.assertGreaterEqual(window.sidebar_panel.width(), 300)
            central = window.centralWidget()
            self.assertIsNotNone(central)
            for button in (window.svg_button, window.emf_button, window.pdf_button):
                self.assertTrue(button.isVisible())
                top_left = button.mapTo(central, QPoint(0, 0))
                bottom_right = button.mapTo(
                    central,
                    QPoint(max(0, button.width() - 1), max(0, button.height() - 1)),
                )
                self.assertTrue(central.rect().contains(top_left))
                self.assertTrue(central.rect().contains(bottom_right))
        finally:
            window.close()
            APP.processEvents()

    def test_sidebar_can_be_resized_without_collapsing_preview(self) -> None:
        window = MainWindow()
        try:
            window.resize(900, 620)
            window.show()
            APP.processEvents()
            window.splitter.setSizes([520, 340])
            APP.processEvents()

            preview_width, sidebar_width = window.splitter.sizes()
            self.assertGreaterEqual(preview_width, 360)
            self.assertGreaterEqual(sidebar_width, 300)
            self.assertLessEqual(sidebar_width, 440)
        finally:
            window.close()
            APP.processEvents()


if __name__ == "__main__":
    unittest.main()
