from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

from origin_hybridizer.geometry import Rect
from origin_hybridizer.svg_document import (
    Detection, SvgDocument, local_name, qt_preview_bytes, render_qt_svg_preview,
)
from tests.test_hybridizer import make_origin_svg


APP = QApplication.instance() or QApplication([])
VIEW_BOX = Rect(0, 0, 100, 100)
SYMBOL_LIST = "Symbol,'MS PGothic',SimSun"


def svg(body: str) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" '
        'viewBox="0 0 100 100">' + body + '</svg>'
    ).encode()


def label(attributes: str, fill: str = "black") -> str:
    return f'<text x="20" y="85" font-size="80" fill="{fill}" {attributes}>D</text>'


def render(data: bytes) -> QImage:
    return render_qt_svg_preview(data, VIEW_BOX, max_side=100)


class SvgFontNormalizationTests(unittest.TestCase):
    def test_only_first_symbol_family_is_normalized_in_render_copy(self) -> None:
        source = svg(
            '<g id="inherited" font-family="Symbol, Arial">'
            + label('id="quoted" font-family="&quot;sYmBoL&quot;, Arial"')
            + label('id="styled" style="fill:red; FONT-FAMILY: \'Symbol\', Arial !important;opacity:0.5"')
            + label('id="latin" font-family="Arial,Symbol" style="font-family:Arial,Symbol;fill:blue"')
            + label('id="other" font-family="Symbolic,Arial"')
            + label('id="comma" font-family="\'Symbol, Alternate\',Arial"')
            + '</g>'
        )
        root = ET.fromstring(qt_preview_bytes(source))
        elements = {element.get("id"): element for element in root.iter()}
        self.assertEqual(elements["inherited"].get("font-family"), "Symbol")
        self.assertEqual(elements["quoted"].get("font-family"), "Symbol")
        self.assertEqual(elements["styled"].get("style"),
                         "fill:red; FONT-FAMILY: Symbol !important;opacity:0.5")
        self.assertEqual(elements["latin"].get("font-family"), "Arial,Symbol")
        self.assertEqual(elements["latin"].get("style"), "font-family:Arial,Symbol;fill:blue")
        self.assertEqual(elements["other"].get("font-family"), "Symbolic,Arial")
        self.assertEqual(elements["comma"].get("font-family"), "'Symbol, Alternate',Arial")
        self.assertTrue(all(element.text == "D" for element in root.iter()
                            if local_name(element.tag) == "text"))
        self.assertIn(b'font-family="Symbol, Arial"', source)

    def test_preview_and_public_export_keep_source_text_and_fonts_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.svg"
            _, source = make_origin_svg(path)
            source = source.replace(
                b'<text id="x-label" x="274" y="290" font-family="Arial">Vbg (V)</text>',
                f'<text id="x-label" x="274" y="290" font-family="{SYMBOL_LIST}">D</text>'.encode(),
            )
            path.write_bytes(source)
            document = SvgDocument(path)
            text_before = [ET.tostring(element) for element in document.root.iter()
                           if local_name(element.tag) == "text"]
            detection = document.detect_heatmap(minimum_primitives=30)
            document.render_preview(max_side=300)
            document.render_review_preview(detection, max_side=300)
            output, _, _ = document.hybrid_bytes(detection, dpi=96)
            text_after = [ET.tostring(element) for element in ET.fromstring(output).iter()
                          if local_name(element.tag) == "text"]
            self.assertEqual(text_before, text_after)
            self.assertEqual(path.read_bytes(), source)
            self.assertEqual(document.source_bytes, source)
            self.assertEqual(next(element for element in document.root.iter()
                                  if element.get("id") == "x-label").get("font-family"), SYMBOL_LIST)
            preview_label = next(element for element in ET.fromstring(document.preview_bytes).iter()
                                 if element.get("id") == "x-label")
            self.assertEqual(preview_label.get("font-family"), "Symbol")


class SvgSymbolRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for filename in ("symbol.ttf", "arial.ttf"):
            path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        if not {"Symbol", "Arial"}.issubset(QFontDatabase.families()):
            raise unittest.SkipTest("Symbol and Arial fonts are required for controlled glyph rendering")

    def test_symbol_family_list_renders_delta_like_single_family_reference(self) -> None:
        reference = render(svg(label('font-family="Symbol"')))
        latin = render(svg(label('font-family="Arial"')))
        self.assertNotEqual(reference, latin, "the two reference fonts must produce distinct glyphs")
        for body in (
            label(f'font-family="{SYMBOL_LIST}"'),
            label('style="font-family:\'sYmBoL\', Arial"'),
            f'<g font-family="{SYMBOL_LIST}">' + label("") + '</g>',
        ):
            with self.subTest(body=body):
                self.assertEqual(render(qt_preview_bytes(svg(body))), reference)

    def test_non_symbol_first_family_keeps_latin_d(self) -> None:
        source = svg(label('font-family="Arial,Symbol"'))
        preview = render(qt_preview_bytes(source))
        self.assertEqual(preview, render(svg(label('font-family="Arial"'))))
        self.assertNotEqual(preview, render(svg(label('font-family="Symbol"'))))

    def test_document_original_and_review_preview_render_delta(self) -> None:
        source = svg(f'<svg width="100" height="100" viewBox="0 0 100 100">'
                     f'<g font-family="{SYMBOL_LIST}">' + label("") + '</g></svg>')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.svg"
            path.write_bytes(source)
            document = SvgDocument(path)
            self.assertEqual(document.render_preview(max_side=100),
                             render(svg(label('font-family="Symbol"'))))
            detection = Detection(VIEW_BOX, frozenset(), "preview", 1.0)
            self.assertEqual(document.render_review_preview(detection, max_side=100),
                             render(svg(label('font-family="Symbol"', fill="#169c52"))))
            self.assertEqual(path.read_bytes(), source)

    def test_fallback_raster_render_copy_honors_resource_symbol_font(self) -> None:
        def resource(family: str) -> bytes:
            return svg(f'<defs><g id="glyph" font-family="{family}">' + label("")
                       + '</g></defs><use id="tile" href="#glyph"/>')

        source = resource(SYMBOL_LIST)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.svg"
            path.write_bytes(source)
            document = SvgDocument(path)
            png, width, height, method = document._raster_png(frozenset({"tile"}), VIEW_BOX, dpi=96)
            self.assertEqual((width, height, method), (100, 100, "svg-renderer"))
            actual = QImage.fromData(png, "PNG").convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            self.assertEqual(actual, render(resource("Symbol")))
            self.assertEqual(path.read_bytes(), source)


if __name__ == "__main__":
    unittest.main()
