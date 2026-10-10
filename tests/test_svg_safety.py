from __future__ import annotations

import base64
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from origin_hybridizer.geometry import Rect
from origin_hybridizer.svg_document import SvgDocument, local_name, render_qt_svg_preview


APP = QApplication.instance() or QApplication([])


def cells(*, prefix: str = "c", extra: str = "", color=None, rows=range(6)) -> str:
    return "".join(
        f'<rect id="{prefix}{row}_{column}" x="{column * 10}" y="{row * 10}" '
        f'width="10" height="10" fill="{color(row, column) if color else "red"}" {extra}/>'
        for row in rows for column in range(6)
    )


def write_svg(path: Path, body: str) -> bytes:
    data = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60" viewBox="0 0 60 60">'
        + body + '</svg>'
    ).encode()
    path.write_bytes(data)
    return data


class SvgSafetyTests(unittest.TestCase):
    def assert_pixel(self, image: QImage, x: int, y: int, expected, tolerance=0) -> None:
        actual = image.pixelColor(x, y).getRgb()
        for component, wanted in zip(actual, expected):
            self.assertAlmostEqual(component, wanted, delta=tolerance, msg=f"pixel ({x}, {y}): {actual}")

    def convert(self, path: Path, body: str):
        source = write_svg(path, body)
        document = SvgDocument(path)
        detection = document.select_rectangle(Rect(0, 0, 60, 60))
        output, _, _ = document.hybrid_bytes(detection, dpi=96)
        before = render_qt_svg_preview(source, document.view_box, max_side=60)
        after = render_qt_svg_preview(output, document.view_box, max_side=60)
        return before, after, ET.fromstring(output)

    def test_export_rejects_source_path_and_preserves_file_before_raster_work(self) -> None:
        for alias in ("same", "relative", "case", "hardlink", "symlink"):
            with self.subTest(alias=alias), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source.svg"
                original = write_svg(source, '<g>' + cells() + '</g>')
                document = SvgDocument(source)
                detection = document.select_rectangle(Rect(0, 0, 60, 60))
                output = source
                if alias == "relative":
                    output = Path(os.path.relpath(source))
                elif alias == "case":
                    if os.name != "nt":
                        continue
                    output = source.with_name("SOURCE.svg")
                elif alias == "hardlink":
                    output = source.with_name("hardlink.svg")
                    os.link(source, output)
                elif alias == "symlink":
                    output = source.with_name("symlink.svg")
                    try:
                        output.symlink_to(source)
                    except OSError:
                        continue
                progress = []
                try:
                    with self.assertRaisesRegex(ValueError, "source|original"):
                        document.export_hybrid_svg(output, detection, dpi=96, progress=lambda *args: progress.append(args))
                finally:
                    self.assertEqual(source.read_bytes(), original, "Rejected export must preserve source bytes")
                self.assertEqual(progress, [], "Reject source aliases before raster allocation or rendering")

    def test_fallback_applies_shared_opacity_once(self) -> None:
        cases = (
            '<g id="paint" opacity="0.5">',
            '<g id="paint" style="opacity:0.5;fill-opacity:0.5">',
            '<style>g{opacity:0.5}</style><g id="paint">',
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, opening in enumerate(cases):
                with self.subTest(opening=opening):
                    before, after, root = self.convert(Path(directory) / f"opacity{index}.svg", opening + cells(extra='stroke="red"') + '</g>')
                    expected_alpha = 63 if "fill-opacity" in opening else 127
                    self.assert_pixel(before, 5, 5, (255, 0, 0, expected_alpha), tolerance=1)
                    self.assert_pixel(after, 5, 5, (255, 0, 0, expected_alpha), tolerance=1)
                    group = next(e for e in root.iter() if e.get("id") == "paint")
                    if "opacity=\"" in opening:
                        self.assertEqual(group.get("opacity"), "0.5")

    def test_fallback_keeps_opacity_below_shared_insertion_parent(self) -> None:
        body = ('<g opacity="0.5"><g opacity="0.5">' + cells(rows=range(3), extra='stroke="red"')
                + '</g><g opacity="0.25">' + cells(rows=range(3, 6), extra='stroke="red"') + '</g></g>')
        with tempfile.TemporaryDirectory() as directory:
            before, after, _ = self.convert(Path(directory) / "child-opacity.svg", body)
            self.assert_pixel(before, 5, 5, (255, 0, 0, 63), tolerance=1)
            self.assert_pixel(before, 5, 55, (255, 0, 0, 31), tolerance=1)
            self.assert_pixel(after, 5, 5, (255, 0, 0, 63), tolerance=1)
            self.assert_pixel(after, 5, 55, (255, 0, 0, 31), tolerance=1)

    def test_interleaved_retained_artwork_is_rejected_before_raster_work(self) -> None:
        png = QImage(1, 1, QImage.Format.Format_ARGB32)
        png.fill(0xffffffff)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        self.assertTrue(png.save(buffer, "PNG"))
        png_href = base64.b64encode(bytes(buffer.data())).decode("ascii")
        artwork_cases = (
            '<path id="axis" d="M0 30 H60" fill="none" stroke="white" stroke-width="10"/>',
            '<g><g><path id="axis" d="M0 30 H60" fill="none" stroke="white" stroke-width="10"/></g></g>',
            '<text id="label" x="15" y="30">axis</text>',
            f'<image width="60" height="60" href="data:image/png;base64,{png_href}"/>',
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, artwork in enumerate(artwork_cases):
                with self.subTest(artwork=artwork):
                    source = Path(directory) / f"order{index}.svg"
                    write_svg(source, '<g>' + cells(prefix="r") + '</g>' + artwork + '<g>' + cells(prefix="b", color=lambda *_: "blue") + '</g>')
                    document = SvgDocument(source)
                    detection = document.select_rectangle(Rect(0, 0, 60, 60))
                    self.assertEqual(detection.count, 72)
                    progress = []
                    with self.assertRaisesRegex(ValueError, "uninterrupted|layer|paint order"):
                        document.hybrid_bytes(detection, dpi=96, progress=lambda *args: progress.append(args))
                    self.assertEqual(progress, [], "Reject interleaving before raster work")

    def test_nested_artwork_before_first_selected_cell_is_rejected(self) -> None:
        # The single root-level image would move ahead of the retained path
        # inside its first selected branch, changing their original order.
        body = ('<g><path id="axis" d="M0 30 H60" fill="none" stroke="white"/>'
                + cells(rows=range(3)) + '</g><g>' + cells(rows=range(3, 6)) + '</g>')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "nested-prefix.svg"
            write_svg(source, body)
            document = SvgDocument(source)
            detection = document.select_rectangle(Rect(0, 0, 60, 60))
            with self.assertRaisesRegex(ValueError, "uninterrupted|layer|paint order"):
                document.hybrid_bytes(detection, dpi=96)

    def test_unsupported_shared_effects_fail_clearly(self) -> None:
        cases = (
            ('<g filter="url(#soft)">', cells()),
            ('<g style="filter:url(#soft)">', cells()),
            ('<style>g{filter:url(#soft)}</style><g>', cells()),
            ('<g style="mix-blend-mode:multiply">', cells()),
            ('<g opacity="0.5">', cells(extra='style="opacity:inherit;stroke:red"')),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (opening, grid) in enumerate(cases):
                with self.subTest(opening=opening, grid_style=grid[:150]):
                    source = Path(directory) / f"effect{index}.svg"
                    write_svg(source, '<defs><filter id="soft"><feGaussianBlur stdDeviation="1"/></filter></defs>' + opening + grid + '</g>')
                    document = SvgDocument(source)
                    detection = document.select_rectangle(Rect(0, 0, 60, 60))
                    with self.assertRaisesRegex(ValueError, "effect|filter|inherit|blend"):
                        document.hybrid_bytes(detection, dpi=96)

    def test_resource_and_empty_groups_do_not_interrupt_selection(self) -> None:
        body = ('<rect id="background" width="60" height="60" fill="white"/>'
                '<g>' + cells(rows=range(3)) + '<defs><rect width="1" height="1"/></defs><g/>'
                + cells(rows=range(3, 6)) + '</g><text id="label" x="20" y="30">axis</text>')
        with tempfile.TemporaryDirectory() as directory:
            _, _, root = self.convert(Path(directory) / "safe-order.svg", body)
            self.assertEqual(sum(local_name(e.tag) == "image" for e in root.iter()), 1)
            self.assertTrue(any(e.get("id") == "label" for e in root.iter()))

    def test_reflected_fallback_keeps_pixel_orientation(self) -> None:
        cases = (
            ('translate(60,0) scale(-1,1)', lambda row, col: "red" if col < 3 else "blue", ((5, 5, (0, 0, 255, 255)), (55, 5, (255, 0, 0, 255)))),
            ('translate(0,60) scale(1,-1)', lambda row, col: "red" if row < 3 else "blue", ((5, 5, (0, 0, 255, 255)), (5, 55, (255, 0, 0, 255)))),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (transform, color, expectations) in enumerate(cases):
                with self.subTest(transform=transform):
                    before, after, _ = self.convert(Path(directory) / f"reflection{index}.svg", f'<g transform="{transform}">' + cells(color=color) + '</g>')
                    for x, y, expected in expectations:
                        self.assert_pixel(before, x, y, expected)
                        self.assert_pixel(after, x, y, expected)

    def test_tag_and_id_stylesheets_use_renderer_colors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for index, selector in enumerate(("rect", "#c0_0")):
                with self.subTest(selector=selector):
                    before, after, root = self.convert(Path(directory) / f"stylesheet{index}.svg", f'<style>{selector}{{fill:blue}}</style><g>' + cells() + '</g>')
                    self.assert_pixel(before, 5, 5, (0, 0, 255, 255))
                    self.assert_pixel(after, 5, 5, (0, 0, 255, 255))
                    image = next(e for e in root.iter() if local_name(e.tag) == "image")
                    self.assertEqual(image.get("data-origin-rasterization"), "svg-renderer")

    def test_rounded_cells_preserve_transparent_corners(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            before, after, root = self.convert(Path(directory) / "rounded.svg", '<g>' + cells(extra='rx="3"') + '</g>')
            self.assert_pixel(before, 0, 0, (255, 0, 0, 7), tolerance=1)
            self.assert_pixel(after, 0, 0, (255, 0, 0, 7), tolerance=1)
            self.assert_pixel(after, 5, 5, (255, 0, 0, 255))
            image = next(e for e in root.iter() if local_name(e.tag) == "image")
            self.assertEqual(image.get("data-origin-rasterization"), "svg-renderer")


if __name__ == "__main__":
    unittest.main()
