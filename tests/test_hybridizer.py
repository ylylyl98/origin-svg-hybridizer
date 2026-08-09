from __future__ import annotations

import base64
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from origin_hybridizer.geometry import Rect
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


class HybridizerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
