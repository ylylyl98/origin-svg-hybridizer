from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from origin_hybridizer.geometry import Rect
from origin_hybridizer.svg_document import DRAWABLE_TAGS, SvgDocument, local_name, render_qt_svg_preview


APP = QApplication.instance() or QApplication([])
COLORS = tuple(f"#{32 + i * 4:02x}{64 + i * 3:02x}{192 - i * 4:02x}" for i in range(24))


def ramp(*, vertical=False, name="colorbar", caps=True, count=24, cell_extra="", frame=True):
    parts = [f'<g id="{name}">']
    colors = (["black"] if caps else []) + list(COLORS[:count]) + (["white"] if caps else [])
    for i, color in enumerate(colors):
        x, y, width, height = (110, 10 + i * 2, 10, 2) if vertical else (100 + i * 2, 80, 2, 10)
        parts.append(f'<rect id="{name}-c{i}" x="{x}" y="{y}" width="{width}" height="{height}" fill="{color}" stroke="none" {cell_extra}/>')
    if frame:
        if vertical:
            parts.extend([
                f'<rect id="{name}-frame" x="110" y="10" width="10" height="{len(colors) * 2}" fill="none" stroke="black"/>',
                f'<line id="{name}-tick0" x1="120" y1="12" x2="125" y2="12" stroke="black"/>',
                f'<line id="{name}-tick1" x1="120" y1="58" x2="125" y2="58" stroke="black"/>',
            ])
        else:
            parts.extend([
                f'<rect id="{name}-frame" x="100" y="80" width="{len(colors) * 2}" height="10" fill="none" stroke="black"/>',
                f'<line id="{name}-tick0" x1="102" y1="80" x2="102" y2="75" stroke="black"/>',
                f'<line id="{name}-tick1" x1="148" y1="80" x2="148" y2="75" stroke="black"/>',
            ])
    parts.append(f'<text id="{name}-label" x="100" y="70">Intensity</text></g>')
    return "".join(parts)


def fixture(path: Path, artwork: str, *, before="", nested=False):
    heatmap = '<g id="heatmap">' + "".join(
        f'<rect id="h{row}_{col}" x="{10 + col * 10}" y="{10 + row * 10}" width="10" height="10" fill="#{col * 31:02x}{row * 31:02x}80"/>'
        for row in range(6) for col in range(6)
    ) + '</g>'
    body = heatmap + artwork + '<path id="annotation" d="M160 20 L170 30" stroke="red" fill="none"/>'
    if nested:
        body = '<svg id="viewport" x="7" y="9" width="180" height="140" viewBox="0 0 200 160" preserveAspectRatio="none"><g id="transformed" transform="translate(2 3) scale(.8)">' + body + '</g></svg>'
    data = (before + '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="160" viewBox="0 0 200 160">' + body + '</svg>').encode()
    path.write_bytes(data)
    document = SvgDocument(path)
    selected = frozenset(f"h{r}_{c}" for r in range(6) for c in range(6))
    from origin_hybridizer.svg_document import Detection
    detection = Detection(Rect.union_all([document.info_by_id[uid].bbox for uid in selected]), selected, "Test matrix", 1)
    return document, detection, data


def gradients(root):
    return [e for e in root.iter() if local_name(e.tag) == "linearGradient"]


class ColorbarGradientTests(unittest.TestCase):
    def test_export_uses_exact_center_stops_and_preserves_caps_and_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.svg"
            document, detection, original = fixture(path, ramp())
            source_root = deepcopy(document.root)
            destination = path.with_name("hybrid.svg")
            stats = document.export_hybrid_svg(destination, detection, dpi=96)
            output = ET.fromstring(destination.read_bytes())
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(ET.tostring(document.root), ET.tostring(source_root))
            self.assertEqual(len(gradients(output)), 1, "The public export must replace a dense ramp")
            gradient, = gradients(output)
            self.assertEqual(gradient.get("color-interpolation"), "sRGB")
            self.assertEqual(gradient.get("gradientUnits"), "userSpaceOnUse")
            self.assertEqual([float(gradient.get(k)) for k in ("x1", "y1", "x2", "y2")], [102, 80, 150, 80])
            stops = list(gradient)
            self.assertEqual(len(stops), 26)
            self.assertEqual([stop.get("stop-color") for stop in stops], [COLORS[0], *COLORS, COLORS[-1]])
            for index, stop in enumerate(stops[1:-1]):
                self.assertAlmostEqual(float(stop.get("offset")), (index * 2 + 1) / 48, places=11)
            self.assertEqual([float(stops[i].get("offset")) for i in (0, -1)], [0, 1])
            rectangle, = [e for e in output.iter() if e.get("fill") == f'url(#{gradient.get("id")})']
            self.assertEqual([float(rectangle.get(k)) for k in ("x", "y", "width", "height")], [102, 80, 48, 10])
            for uid in ("colorbar-c0", "colorbar-c25", "colorbar-frame", "colorbar-tick0", "colorbar-tick1", "colorbar-label", "annotation"):
                source = next(e for e in source_root.iter() if e.get("id") == uid)
                retained = next(e for e in output.iter() if e.get("id") == uid)
                self.assertEqual(ET.tostring(retained), ET.tostring(source))
            self.assertFalse(any(e.get("id") == "colorbar-c1" for e in output.iter()))
            self.assertEqual(stats.editable_vector_objects, sum(local_name(e.tag) in DRAWABLE_TAGS for e in output.iter()))
            self.assertIn("colorbar", stats.verification_message.lower())
            image, = [e for e in output.iter() if local_name(e.tag) == "image"]
            self.assertEqual(image.get("data-origin-rasterization"), "exact-cell-matrix")
            png = base64.b64decode(next(value for key, value in image.attrib.items() if local_name(key) == "href").split(",", 1)[1])
            bitmap = QImage.fromData(png)
            self.assertEqual((bitmap.width(), bitmap.height()), (60, 60))
            self.assertEqual(bitmap.pixelColor(5, 5).getRgb(), (0, 0, 128, 255))
            self.assertEqual(bitmap.pixelColor(55, 55).getRgb(), (155, 155, 128, 255))

    def test_vertical_ramp_keeps_nested_viewport_and_transform(self):
        with tempfile.TemporaryDirectory() as directory:
            document, detection, _ = fixture(Path(directory) / "source.svg", ramp(vertical=True), nested=True)
            data, _, _ = document.hybrid_bytes(detection, dpi=96)
            output = ET.fromstring(data)
            self.assertEqual(len(gradients(output)), 1, "The shared pipeline must smooth vertical ramps")
            gradient, = gradients(output)
            self.assertEqual([float(gradient.get(k)) for k in ("x1", "y1", "x2", "y2")], [110, 12, 110, 60])
            for uid in ("viewport", "transformed"):
                old = next(e for e in document.root.iter() if e.get("id") == uid)
                new = next(e for e in output.iter() if e.get("id") == uid)
                self.assertEqual(new.attrib, old.attrib)
            parent_map = {child: parent for parent in output.iter() for child in parent}
            rectangle = next(e for e in output.iter() if e.get("fill") == f'url(#{gradient.get("id")})')
            self.assertEqual(parent_map[rectangle].get("id"), "colorbar")

    def test_unlabelled_ramp_requires_near_frame_and_ticks(self):
        with tempfile.TemporaryDirectory() as directory:
            document, detection, _ = fixture(Path(directory) / "source.svg", ramp(name="series2"))
            data, _, _ = document.hybrid_bytes(detection, dpi=96)
            self.assertEqual(len(gradients(ET.fromstring(data))), 1)

    def test_render_is_continuous_and_heatmap_png_bytes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.svg"
            document, detection, _ = fixture(path, ramp())
            output, _, _ = document.hybrid_bytes(detection, dpi=96)
            plain, plain_detection, _ = fixture(path.with_name("plain.svg"), "")
            plain_output, _, _ = plain.hybrid_bytes(plain_detection, dpi=96)
            def png_href(data):
                image = next(e for e in ET.fromstring(data).iter() if local_name(e.tag) == "image")
                return next(value for key, value in image.attrib.items() if local_name(key) == "href")
            self.assertEqual(png_href(output), png_href(plain_output))
            preview = render_qt_svg_preview(output, document.view_box, max_side=800)
            self.assertFalse(preview.isNull())
            rgb = preview.pixelColor(416, 340).getRgb()
            self.assertEqual(rgb[3], 255)
            self.assertNotIn(rgb[:3], [(32 + i * 4, 64 + i * 3, 192 - i * 4) for i in range(24)])
            self.assertTrue(32 < rgb[0] < 36, rgb)

    def test_verification_rejects_unrelated_and_approved_gradient_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            document, detection, _ = fixture(Path(directory) / "source.svg", ramp())
            data, _, _ = document.hybrid_bytes(detection, dpi=96)
            output = ET.fromstring(data)
            self.assertEqual(len(gradients(output)), 1)
            for kind in ("vector", "stop", "parent", "order", "extra-gradient"):
                with self.subTest(kind=kind):
                    changed = deepcopy(output)
                    if kind == "vector":
                        next(e for e in changed.iter() if e.get("id") == "annotation").set("d", "M1 1 L2 2")
                    elif kind == "stop":
                        gradients(changed)[0][1].set("stop-color", "#ff0000")
                    elif kind == "parent":
                        next(e for e in changed.iter() if e.get("id") == "colorbar").set("transform", "translate(1)")
                    elif kind == "order":
                        group = next(e for e in changed.iter() if e.get("id") == "colorbar")
                        group[:] = list(reversed(list(group)))
                    else:
                        changed.append(deepcopy(gradients(changed)[0]))
                    with self.assertRaisesRegex(RuntimeError, "Safety check"):
                        document._verify_hybrid_output(ET.tostring(changed), detection)

    def test_multiple_bars_have_unique_ids_and_conversion_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.svg"
            document, detection, _ = fixture(path, '<defs><linearGradient id="__origin_colorbar_1"/></defs>' + ramp() + ramp(name="colourbar", vertical=True))
            data, _, _ = document.hybrid_bytes(detection, dpi=96)
            output = ET.fromstring(data)
            self.assertEqual(len(gradients(output)), 3)
            ids = [e.get("id") for e in output.iter() if e.get("id")]
            self.assertEqual(len(ids), len(set(ids)))
            # A source containing an existing gradient is retained on later exports.
            second, second_detection, _ = fixture(path.with_name("second.svg"), ET.tostring(next(e for e in output if e.get("id") == "colorbar"), encoding="unicode"))
            second_data, _, _ = second.hybrid_bytes(second_detection, dpi=96)
            self.assertEqual(len(gradients(ET.fromstring(second_data))), 1)

    def test_generated_ids_do_not_resolve_unrelated_dangling_references(self):
        with tempfile.TemporaryDirectory() as directory:
            artwork = ramp() + '<rect id="dangling" x="175" y="110" width="10" height="10" fill="url(#__origin_colorbar_1)"/><use href="#__origin_colorbar_1_rect"/>'
            document, detection, _ = fixture(Path(directory) / "source.svg", artwork)
            data, _, _ = document.hybrid_bytes(detection, dpi=96)
            output = ET.fromstring(data)
            ids = {e.get("id") for e in output.iter()}
            self.assertNotIn("__origin_colorbar_1", ids)
            self.assertNotIn("__origin_colorbar_1_rect", ids)
            self.assertEqual(next(e for e in output.iter() if e.get("id") == "dangling").get("fill"), "url(#__origin_colorbar_1)")

    def test_conservative_skips_keep_original_cells(self):
        cases = {
            "small categories": ramp(count=5),
            "heatmap edge strip": ramp(name="series2", frame=False),
            "per-cell opacity": ramp(cell_extra='opacity="0.5"'),
            "per-cell filter": ramp(cell_extra='filter="url(#fx)"'),
            "internal css": '<style>.colorbar{fill:red}</style>' + ramp(),
            "external css": ramp(),
            "referenced cell": ramp() + '<use href="#colorbar-c12"/>',
            "gapped bar": ramp().replace('x="124"', 'x="124.5"', 1),
            "overlapping bar": ramp().replace('x="124"', 'x="123.5"', 1),
            "percent geometry": ramp().replace('x="124"', 'x="124%"', 1),
            "physical geometry": ramp().replace('width="2"', 'width="2mm"'),
            "inherited borders": '<g stroke="black">' + ramp().replace(' stroke="none"', '') + '</g>',
            "inherited styled borders": '<g style="stroke:red">' + ramp().replace(' stroke="none"', '') + '</g>',
            "animated cells": ramp().replace('/><rect id="colorbar-c2"', '><animate attributeName="fill" values="red;blue" dur="1s"/></rect><rect id="colorbar-c2"', 1),
            "scripted cells": '<script>document.getElementById("colorbar-c1").setAttribute("fill","red")</script>' + ramp(),
            "interactive cells": ramp(cell_extra='onclick="changeColor()"'),
            "bowtie polygon": ramp().replace('<rect id="colorbar-c12" x="124" y="80" width="2" height="10"', '<polygon id="colorbar-c12" points="124,80 126,90 126,80 124,90"'),
            "categorical dense": ramp(caps=False).replace(COLORS[3], COLORS[0]).replace(COLORS[4], COLORS[0]),
            "masked edge strip": '<defs><mask id="m"><rect width="200" height="160" fill="white"/></mask></defs><g mask="url(#m)">' + ramp(name="series2") + '</g>',
        }
        # Repeated categorical colors are deliberately dense, but have no ramp.
        for color in COLORS[1:]:
            cases["categorical dense"] = cases["categorical dense"].replace(color, COLORS[0])
        with tempfile.TemporaryDirectory() as directory:
            for index, (name, artwork) in enumerate(cases.items()):
                with self.subTest(name=name):
                    before = '<?xml-stylesheet href="palette.css" type="text/css"?>' if name == "external css" else ""
                    document, detection, _ = fixture(Path(directory) / f"skip{index}.svg", artwork, before=before)
                    data, _, _ = document.hybrid_bytes(detection, dpi=96)
                    output = ET.fromstring(data)
                    self.assertEqual(gradients(output), [])
                    self.assertIsNotNone(next((e for e in output.iter() if e.get("id") == "colorbar-c1" or e.get("id") == "series2-c1"), None))

    def test_selected_ramp_cells_remain_raster_selection(self):
        from origin_hybridizer.svg_document import Detection
        with tempfile.TemporaryDirectory() as directory:
            document, _, _ = fixture(Path(directory) / "selected.svg", ramp())
            selected = frozenset(f"colorbar-c{i}" for i in range(26))
            detection = Detection(Rect(100, 80, 52, 10), selected, "User selected bar", 1)
            data, _, _ = document.hybrid_bytes(detection, dpi=96)
            self.assertEqual(gradients(ET.fromstring(data)), [])


if __name__ == "__main__":
    unittest.main()
