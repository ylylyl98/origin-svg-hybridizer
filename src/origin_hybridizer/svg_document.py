from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import base64
import math
import re
from typing import Callable
import xml.etree.ElementTree as ET

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

from .geometry import (
    IDENTITY,
    Matrix,
    Rect,
    inverse,
    length_to_inches,
    map_rect,
    matrix_string,
    multiply,
    parse_transform,
)


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

ProgressCallback = Callable[[int, str], None]


def _report(progress: ProgressCallback | None, value: int, message: str) -> None:
    if progress is not None:
        progress(max(0, min(100, int(value))), message)

DRAWABLE_TAGS = {"path", "rect", "polygon", "polyline", "circle", "ellipse", "line", "use"}
TEXT_TAGS = {"text", "tspan", "textPath"}
CONTAINER_TAGS = {"svg", "g", "a", "switch", "symbol"}
RESOURCE_TAGS = {
    "defs", "style", "clipPath", "mask", "pattern", "marker", "linearGradient",
    "radialGradient", "filter", "metadata", "title", "desc",
}

# Names commonly used by Origin, Illustrator, and scientific drawing tools for
# objects that should stay editable. Automatic and rectangle-based selection
# both honor these protections. The user can still rasterize an unnamed dense
# region, but a labelled arrowhead or legend swatch is never swept up merely
# because it happens to sit inside the heatmap rectangle.
PROTECTED_NAME_TOKENS = {
    "annotation", "arrow", "axis", "curve", "errorbar", "frame", "label",
    "leader", "legend", "marker", "scale", "symbol", "text", "tick",
}


def local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _parse_view_box(root: ET.Element, renderer: QSvgRenderer | None = None) -> Rect:
    raw = root.get("viewBox")
    if raw:
        values = [float(v) for v in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", raw)]
        if len(values) == 4 and values[2] > 0 and values[3] > 0:
            return Rect(*values)
    if renderer is not None:
        size = renderer.defaultSize()
        if size.width() > 0 and size.height() > 0:
            return Rect(0.0, 0.0, float(size.width()), float(size.height()))
    width, height = _number(root.get("width")), _number(root.get("height"))
    if width <= 0 or height <= 0:
        raise ValueError("The SVG has no usable viewBox, width, or height")
    return Rect(0.0, 0.0, width, height)


def _style_map(element: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for declaration in (element.get("style") or "").split(";"):
        if ":" in declaration:
            key, value = declaration.split(":", 1)
            result[key.strip().lower()] = value.strip().lower()
    return result


_SVG_NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _number(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    match = _SVG_NUMBER_RE.search(value)
    return float(match.group(0)) if match else default


def _basic_geometry_bbox(element: ET.Element) -> Rect | None:
    """Bounds for Origin primitives when Qt cannot index a nested SVG element."""
    tag = local_name(element.tag)
    if tag == "rect":
        return Rect(
            _number(element.get("x")),
            _number(element.get("y")),
            max(0.0, _number(element.get("width"))),
            max(0.0, _number(element.get("height"))),
        )
    if tag == "circle":
        radius = max(0.0, _number(element.get("r")))
        return Rect(_number(element.get("cx")) - radius, _number(element.get("cy")) - radius, 2 * radius, 2 * radius)
    if tag == "ellipse":
        rx, ry = max(0.0, _number(element.get("rx"))), max(0.0, _number(element.get("ry")))
        return Rect(_number(element.get("cx")) - rx, _number(element.get("cy")) - ry, 2 * rx, 2 * ry)
    if tag == "line":
        x1, y1 = _number(element.get("x1")), _number(element.get("y1"))
        x2, y2 = _number(element.get("x2")), _number(element.get("y2"))
        return Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
    if tag in {"polygon", "polyline"}:
        values = [float(value) for value in _SVG_NUMBER_RE.findall(element.get("points") or "")]
        if len(values) < 4:
            return None
        xs, ys = values[0::2], values[1::2]
        pair_count = min(len(xs), len(ys))
        xs, ys = xs[:pair_count], ys[:pair_count]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
    return None


def _nested_svg_viewport_matrix(element: ET.Element) -> Matrix:
    """Map a nested SVG's viewBox into its parent user coordinate system."""
    x, y = _number(element.get("x")), _number(element.get("y"))
    raw_view_box = element.get("viewBox")
    values = [float(value) for value in _SVG_NUMBER_RE.findall(raw_view_box or "")]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        return (1.0, 0.0, 0.0, 1.0, x, y)
    vx, vy, view_width, view_height = values
    width = _number(element.get("width"), view_width)
    height = _number(element.get("height"), view_height)
    if width <= 0 or height <= 0:
        return IDENTITY
    scale_x, scale_y = width / view_width, height / view_height
    preserve = (element.get("preserveAspectRatio") or "xMidYMid meet").strip()
    tokens = [token for token in preserve.split() if token.lower() != "defer"]
    if tokens and tokens[0].lower() == "none":
        return multiply(
            multiply((1.0, 0.0, 0.0, 1.0, x, y), (scale_x, 0.0, 0.0, scale_y, 0.0, 0.0)),
            (1.0, 0.0, 0.0, 1.0, -vx, -vy),
        )
    alignment = tokens[0] if tokens else "xMidYMid"
    mode = tokens[1].lower() if len(tokens) > 1 else "meet"
    scale = max(scale_x, scale_y) if mode == "slice" else min(scale_x, scale_y)
    extra_x, extra_y = width - view_width * scale, height - view_height * scale
    x_factor = 0.0 if "xMin" in alignment else 1.0 if "xMax" in alignment else 0.5
    y_factor = 0.0 if "YMin" in alignment else 1.0 if "YMax" in alignment else 0.5
    return multiply(
        multiply(
            (1.0, 0.0, 0.0, 1.0, x + extra_x * x_factor, y + extra_y * y_factor),
            (scale, 0.0, 0.0, scale, 0.0, 0.0),
        ),
        (1.0, 0.0, 0.0, 1.0, -vx, -vy),
    )


def _flatten_nested_svgs_for_qt(root: ET.Element) -> None:
    """Convert nested SVG viewports to equivalent groups for Qt rendering.

    QSvgRenderer accepts these Origin documents as valid but silently paints an
    empty page. This transformation is used only in private preview/raster copies;
    the exported document retains the original nested SVG structure.
    """
    for element in list(root.iter()):
        if element is root or local_name(element.tag) != "svg":
            continue
        viewport = _nested_svg_viewport_matrix(element)
        combined = multiply(parse_transform(element.get("transform")), viewport)
        element.tag = f"{{{SVG_NS}}}g"
        for attribute in ("x", "y", "width", "height", "viewBox", "preserveAspectRatio"):
            element.attrib.pop(attribute, None)
        if any(abs(a - b) > 1e-12 for a, b in zip(combined, IDENTITY)):
            element.set("transform", matrix_string(combined))
        else:
            element.attrib.pop("transform", None)


def qt_preview_bytes(svg_bytes: bytes) -> bytes:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(svg_bytes, parser=parser)
    _flatten_nested_svgs_for_qt(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def render_qt_svg_preview(svg_bytes: bytes, view_box: Rect, max_side: int = 1600) -> QImage:
    """Render a bounded-size preview image; safe to call from a worker thread."""
    scale = min(max_side / view_box.width, max_side / view_box.height)
    width = max(1, round(view_box.width * scale))
    height = max(1, round(view_box.height * scale))
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    if not renderer.isValid():
        raise ValueError("Qt could not render the SVG preview")
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    try:
        renderer.render(painter, QRectF(0, 0, width, height))
    finally:
        painter.end()
    return image


def _is_filled_primitive(element: ET.Element) -> bool:
    tag = local_name(element.tag)
    if tag not in DRAWABLE_TAGS:
        return False
    style = _style_map(element)
    opacity = style.get("opacity", element.get("opacity", "1")).strip()
    fill_opacity = style.get("fill-opacity", element.get("fill-opacity", "1")).strip()
    if opacity in {"0", "0.0"} or fill_opacity in {"0", "0.0"}:
        return False
    fill = style.get("fill", (element.get("fill") or "").strip().lower())
    if fill in {"none", "transparent"}:
        return False
    if tag == "polyline" and not fill:
        return False
    # Origin normally writes fill:none for axes and strokes. This extra rule also
    # protects minimally styled line-like paths from accidental rasterization.
    stroke = style.get("stroke", (element.get("stroke") or "").strip().lower())
    if tag == "path" and not fill and stroke and stroke != "none":
        return False
    return True


@dataclass
class ElementInfo:
    uid: str
    element: ET.Element
    parent: ET.Element
    bbox: Rect
    tag: str


@dataclass(frozen=True)
class Detection:
    rect: Rect
    element_ids: frozenset[str]
    method: str
    confidence: float

    @property
    def count(self) -> int:
        return len(self.element_ids)


@dataclass(frozen=True)
class ExportStats:
    output_path: Path
    rasterized_elements: int
    raster_width: int
    raster_height: int
    source_bytes: int
    output_bytes: int
    region: Rect
    editable_text_objects: int = 0
    editable_vector_objects: int = 0
    verification_message: str = ""


@dataclass(frozen=True)
class ClassificationSummary:
    raster_objects: int
    editable_text_objects: int
    editable_vector_objects: int
    protected_filled_objects: int


class SvgDocument:
    """An Origin SVG plus analysis needed to replace only dense filled primitives."""

    def __init__(self, path: str | Path, progress: ProgressCallback | None = None):
        self.path = Path(path)
        _report(progress, 2, "Reading SVG file")
        self.source_bytes = self.path.read_bytes()
        self._estimated_drawables = max(1, sum(
            self.source_bytes.count(f"<{tag}".encode("ascii"))
            for tag in DRAWABLE_TAGS
        ))
        _report(progress, 10, "Parsing SVG XML")
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        self.root = ET.fromstring(self.source_bytes, parser=parser)
        if local_name(self.root.tag) != "svg":
            raise ValueError("The selected file is not an SVG document")

        self._temp_ids: set[str] = set()
        _report(progress, 20, "Preparing editable SVG object IDs")
        self._ensure_analysis_ids(
            lambda fraction: _report(progress, 20 + round(fraction * 18), "Preparing editable SVG object IDs")
        )
        nested_origin_viewport = any(
            element is not self.root and local_name(element.tag) == "svg"
            for element in self.root.iter()
        )
        _report(progress, 40, "Preparing the Origin-compatible render copy")
        analysis_root = deepcopy(self.root)
        _flatten_nested_svgs_for_qt(analysis_root)
        self._analysis_bytes = self._serialize(analysis_root)
        # Indexing every element through QSvgRenderer is very expensive for a
        # 30-100 MB Origin polygon map. Nested Origin maps use simple geometry,
        # so parse their bounds directly and construct Qt's renderer only once,
        # later, for the bounded-size preview.
        analysis_renderer = None if nested_origin_viewport else QSvgRenderer(QByteArray(self._analysis_bytes))
        if analysis_renderer is not None and not analysis_renderer.isValid():
            raise ValueError("Qt could not read this SVG. Re-export it from Origin as plain SVG.")
        _report(progress, 55, "Indexing vector geometry")
        self.view_box = _parse_view_box(self.root, analysis_renderer)
        self.parent_map = {child: parent for parent in self.root.iter() for child in parent}
        self.infos = self._collect_candidates(
            analysis_renderer,
            lambda fraction: _report(progress, 55 + round(fraction * 40), "Indexing vector geometry"),
        )
        self.info_by_id = {info.uid: info for info in self.infos}
        self.protected_info_ids = frozenset(
            info.uid for info in self.infos if self._is_semantically_protected(info.element)
        )
        _report(progress, 100, f"Indexed {len(self.infos):,} filled vector objects")

    @property
    def preview_bytes(self) -> bytes:
        return self._analysis_bytes

    def render_preview(self, max_side: int = 1600) -> QImage:
        return render_qt_svg_preview(self._analysis_bytes, self.view_box, max_side)

    def render_review_preview(self, detection: Detection, max_side: int = 1600) -> QImage:
        """Render a temporary color-coded review without changing export art.

        Orange is supplied by the canvas selection overlay. Here, editable
        vector strokes are blue and live SVG text is green. The heatmap keeps
        its original colors so the selected area can still be visually checked.
        """
        root = deepcopy(self.root)
        selected = detection.element_ids
        parent_map = self._clone_parent_map(root)

        def inside_resource(element: ET.Element) -> bool:
            current = parent_map.get(element)
            while current is not None:
                if local_name(current.tag) in RESOURCE_TAGS:
                    return True
                current = parent_map.get(current)
            return False

        for element in root.iter():
            tag = local_name(element.tag)
            if inside_resource(element):
                continue
            if tag in TEXT_TAGS:
                self._append_preview_style(element, "fill:#169c52;stroke:none")
            elif tag in DRAWABLE_TAGS and element.get("id") not in selected:
                # Keep filled vector colors (especially the color-bar ramp), but
                # give editable objects a visible blue edge in the review only.
                if tag in {"path", "line", "polyline"}:
                    self._append_preview_style(element, "stroke:#1769e0")
                else:
                    self._append_preview_style(element, "stroke:#1769e0;stroke-opacity:0.9")
        _flatten_nested_svgs_for_qt(root)
        return render_qt_svg_preview(self._serialize(root), self.view_box, max_side)

    @staticmethod
    def _append_preview_style(element: ET.Element, declarations: str) -> None:
        existing = (element.get("style") or "").strip().rstrip(";")
        element.set("style", f"{existing};{declarations}" if existing else declarations)

    @property
    def width_attribute(self) -> str | None:
        return self.root.get("width")

    @property
    def height_attribute(self) -> str | None:
        return self.root.get("height")

    def _serialize(self, root: ET.Element) -> bytes:
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def _ensure_analysis_ids(self, progress: Callable[[float], None] | None = None) -> None:
        used = {e.get("id") for e in self.root.iter() if e.get("id")}
        counter = 0
        processed = 0
        for element in self.root.iter():
            if local_name(element.tag) not in DRAWABLE_TAGS:
                continue
            processed += 1
            if element.get("id"):
                continue
            while True:
                counter += 1
                uid = f"__origin_hybridizer_{counter}"
                if uid not in used:
                    break
            element.set("id", uid)
            used.add(uid)
            self._temp_ids.add(uid)
            if progress is not None and processed % 5000 == 0:
                progress(min(1.0, processed / self._estimated_drawables))
        if progress is not None:
            progress(1.0)

    def _inside_resource(self, element: ET.Element) -> bool:
        current = self.parent_map.get(element)
        while current is not None:
            if local_name(current.tag) in RESOURCE_TAGS:
                return True
            current = self.parent_map.get(current)
        return False

    def _collect_candidates(
        self,
        renderer: QSvgRenderer | None,
        progress: Callable[[float], None] | None = None,
    ) -> list[ElementInfo]:
        result: list[ElementInfo] = []
        processed = 0
        for element in self.root.iter():
            uid = element.get("id")
            if not uid or not _is_filled_primitive(element) or self._inside_resource(element):
                continue
            processed += 1
            if renderer is not None and renderer.elementExists(uid):
                bounds = renderer.boundsOnElement(uid)
                if bounds.isEmpty() or not bounds.isValid():
                    continue
                # boundsOnElement is local for transformed groups in Qt; map it
                # back into the SVG root coordinate system.
                mapped = renderer.transformForElement(uid).mapRect(bounds)
                bbox = Rect(mapped.x(), mapped.y(), mapped.width(), mapped.height()).normalized()
            else:
                # Qt renders nested <svg> documents but does not expose their
                # child IDs through elementExists(). Origin uses that structure
                # for large colormap exports, so calculate primitive bounds here.
                local_bbox = _basic_geometry_bbox(element)
                if local_bbox is None or local_bbox.area <= 0:
                    continue
                bbox = map_rect(self._cumulative_matrix(element), local_bbox).normalized()
            if bbox.area <= 0 or not all(math.isfinite(v) for v in (bbox.x, bbox.y, bbox.width, bbox.height)):
                continue
            parent = self.parent_map.get(element)
            if parent is None:
                continue
            result.append(ElementInfo(uid, element, parent, bbox, local_name(element.tag)))
            if progress is not None and processed % 5000 == 0:
                progress(min(1.0, processed / self._estimated_drawables))
        if progress is not None:
            progress(1.0)
        return result

    def _cumulative_matrix(
        self,
        element: ET.Element,
        parent_map: dict[ET.Element, ET.Element] | None = None,
    ) -> Matrix:
        parent_map = self.parent_map if parent_map is None else parent_map
        path = self._path_to_root(element, parent_map)
        cumulative: Matrix = IDENTITY
        for index, node in enumerate(path):
            cumulative = multiply(cumulative, parse_transform(node.get("transform")))
            if index > 0 and local_name(node.tag) == "svg":
                cumulative = multiply(cumulative, _nested_svg_viewport_matrix(node))
        return cumulative

    def _ancestors(self, element: ET.Element) -> list[ET.Element]:
        result: list[ET.Element] = []
        current = self.parent_map.get(element)
        while current is not None:
            result.append(current)
            current = self.parent_map.get(current)
        return result

    def _is_semantically_protected(self, element: ET.Element) -> bool:
        """Protect named editable artwork from rectangle/density selection."""
        for node in [element, *self._ancestors(element)]:
            if local_name(node.tag) in TEXT_TAGS | RESOURCE_TAGS:
                return True
            name = " ".join(filter(None, (node.get("id"), node.get("class"), node.get("aria-label")))).lower()
            words = set(re.findall(r"[a-z]+", name))
            if words & PROTECTED_NAME_TOKENS:
                return True
        return False

    def classification_summary(self, detection: Detection) -> ClassificationSummary:
        selected = detection.element_ids
        text_count = sum(
            1 for element in self.root.iter()
            if local_name(element.tag) == "text" and not self._inside_resource(element)
        )
        vector_count = sum(
            1 for element in self.root.iter()
            if local_name(element.tag) in DRAWABLE_TAGS
            and element.get("id") not in selected
            and not self._inside_resource(element)
        )
        return ClassificationSummary(
            raster_objects=len(selected),
            editable_text_objects=text_count,
            editable_vector_objects=vector_count,
            protected_filled_objects=len(set(self.info_by_id) - set(selected)),
        )

    def detect_heatmap(
        self,
        minimum_primitives: int = 30,
        progress: ProgressCallback | None = None,
    ) -> Detection:
        _report(progress, 5, "Filtering filled heatmap primitives")
        page = self.view_box
        eligible = [
            info for info in self.infos
            if info.uid not in self.protected_info_ids
            and info.bbox.area < page.area * 0.12
            and info.bbox.width < page.width * 0.7
            and info.bbox.height < page.height * 0.7
        ]
        if len(eligible) < minimum_primitives:
            raise ValueError(
                f"Only {len(eligible)} filled vector primitives were found. "
                "Use Adjust with a rectangle to mark the heatmap manually."
            )

        descendants: dict[ET.Element, list[ElementInfo]] = defaultdict(list)
        depth: dict[ET.Element, int] = {}
        eligible_count = max(1, len(eligible))
        for index, info in enumerate(eligible):
            for level, ancestor in enumerate(self._ancestors(info.element)):
                if local_name(ancestor.tag) in CONTAINER_TAGS:
                    descendants[ancestor].append(info)
                    depth[ancestor] = max(depth.get(ancestor, 0), level)
            if index % 5000 == 0:
                _report(progress, 10 + round(index / eligible_count * 45), "Grouping dense vector regions")

        qualifying: dict[ET.Element, list[ElementInfo]] = {}
        for group, members in descendants.items():
            if len(members) < minimum_primitives:
                continue
            rect = Rect.union_all([m.bbox for m in members])
            coverage = rect.area / page.area
            packing = sum(min(m.bbox.area, rect.area) for m in members) / max(rect.area, 1e-12)
            # Heatmap tiles or filled contours cover a meaningful share of their
            # bounding box. This prevents a dense scatter series or a long run of
            # isolated tick marks from looking like a heatmap based on count alone.
            # A legitimate image/heatmap can occupy the entire SVG page when it
            # was exported without margins. Primitive-size filtering above still
            # excludes a single page background, so full-page dense maps are safe.
            if 0.001 <= coverage <= 1.01 and packing >= 0.08:
                qualifying[group] = members

        # Prefer the deepest qualifying groups. This rejects a page-wide plot
        # layer when it merely contains a smaller, genuinely dense heatmap group.
        frontier: list[tuple[ET.Element, list[ElementInfo]]] = []
        for group, members in qualifying.items():
            has_qualifying_child = any(
                other is not group and group in self._ancestors(other)
                for other in qualifying
            )
            if not has_qualifying_child:
                frontier.append((group, members))

        _report(progress, 65, "Scoring candidate heatmap groups")
        if frontier:
            # Keep substantial groups and merge adjacent rows/tiles. Multiple
            # panels are intentionally allowed; transparent gaps compress well.
            largest = max(len(members) for _, members in frontier)
            chosen_groups = [item for item in frontier if len(item[1]) >= max(minimum_primitives, largest * 0.12)]
            chosen = {
                m.uid
                for _, members in chosen_groups
                for m in self._repeated_geometry(members, minimum_primitives)
            }
        else:
            chosen = self._spatial_fallback(eligible, minimum_primitives)

        if len(chosen) < minimum_primitives:
            chosen = self._spatial_fallback(eligible, minimum_primitives)
        if len(chosen) < minimum_primitives:
            raise ValueError("No dense heatmap cluster was detected. Use Adjust with a rectangle instead.")

        rect = self._tight_region(chosen)
        candidate_ratio = len(chosen) / max(1, len(eligible))
        confidence = min(0.99, 0.62 + 0.32 * min(1.0, len(chosen) / 300.0) + 0.05 * candidate_ratio)
        _report(progress, 100, f"Detected {len(chosen):,} heatmap objects")
        return Detection(rect, frozenset(chosen), "Automatic", confidence)

    @staticmethod
    def _repeated_geometry(members: list[ElementInfo], minimum: int) -> list[ElementInfo]:
        """Drop isolated filled annotation shapes from a dense plot group."""
        tag_counts: dict[str, int] = defaultdict(int)
        for member in members:
            tag_counts[member.tag] += 1
        repeated_tags = {
            tag for tag, count in tag_counts.items()
            if count >= max(4, min(minimum, math.ceil(len(members) * 0.02)))
        }
        filtered = [member for member in members if member.tag in repeated_tags]
        return filtered if filtered else members

    def _spatial_fallback(self, infos: list[ElementInfo], minimum: int) -> set[str]:
        page = self.view_box
        grid_x = grid_y = 28
        bins: dict[tuple[int, int], list[ElementInfo]] = defaultdict(list)
        for info in infos:
            x, y = info.bbox.center
            gx = min(grid_x - 1, max(0, int((x - page.x) / page.width * grid_x)))
            gy = min(grid_y - 1, max(0, int((y - page.y) / page.height * grid_y)))
            bins[(gx, gy)].append(info)
        threshold = max(2, math.ceil(len(infos) / (grid_x * grid_y) * 2.2))
        dense = {key for key, values in bins.items() if len(values) >= threshold}
        components: list[set[tuple[int, int]]] = []
        while dense:
            start = dense.pop()
            component = {start}
            queue = deque([start])
            while queue:
                x, y = queue.popleft()
                for nx in range(x - 1, x + 2):
                    for ny in range(y - 1, y + 2):
                        if (nx, ny) in dense:
                            dense.remove((nx, ny))
                            component.add((nx, ny))
                            queue.append((nx, ny))
            components.append(component)
        if not components:
            return set()
        components.sort(key=lambda c: sum(len(bins[k]) for k in c), reverse=True)
        selected_cells = set().union(*[
            c for c in components
            if sum(len(bins[k]) for k in c) >= minimum
        ])
        return {info.uid for key in selected_cells for info in bins[key]}

    def select_rectangle(self, rectangle: Rect, minimum_group_size: int = 6) -> Detection:
        rectangle = rectangle.normalized().clipped(self.view_box)
        if rectangle.width <= 0 or rectangle.height <= 0:
            raise ValueError("Draw a rectangle around the heatmap area first")
        inside = [
            info for info in self.infos
            if info.uid not in self.protected_info_ids
            and rectangle.contains_point(*info.bbox.center)
            and info.bbox.area <= rectangle.area * 0.25
        ]
        if not inside:
            raise ValueError("No filled SVG primitives were found inside that rectangle")

        group_counts: dict[ET.Element, int] = defaultdict(int)
        ancestors_by_id: dict[str, list[ET.Element]] = {}
        for info in inside:
            ancestors = [a for a in self._ancestors(info.element) if local_name(a.tag) in CONTAINER_TAGS]
            ancestors_by_id[info.uid] = ancestors
            for ancestor in ancestors:
                group_counts[ancestor] += 1
        dense_groups = {group for group, count in group_counts.items() if count >= minimum_group_size}
        # Use the deepest dense groups. A page/plot layer may also meet the count
        # threshold simply because it contains the heatmap; retaining that outer
        # group would pull isolated arrowheads or annotations into the raster.
        frontier_groups = {
            group for group in dense_groups
            if not any(
                other is not group and group in self._ancestors(other)
                for other in dense_groups
            )
        }
        chosen: set[str] = set()
        for group in frontier_groups:
            group_members = [info for info in inside if group in ancestors_by_id[info.uid]]
            chosen.update(info.uid for info in self._repeated_geometry(group_members, minimum_group_size))
        if not chosen:
            chosen = {info.uid for info in inside}
        rect = self._tight_region(chosen)
        return Detection(rect, frozenset(chosen), "Manual rectangle", 1.0)

    def adjust_detection(self, detection: Detection, rectangle: Rect, mode: str) -> Detection:
        """Add raster objects, exclude objects, or replace the current choice."""
        if mode == "replace":
            return self.select_rectangle(rectangle)
        rectangle = rectangle.normalized().clipped(self.view_box)
        if rectangle.area <= 0:
            raise ValueError("Draw a rectangle over the area you want to adjust")
        if mode == "add":
            addition = self.select_rectangle(rectangle)
            chosen = set(detection.element_ids) | set(addition.element_ids)
            method = "Adjusted selection"
        elif mode == "exclude":
            removed = {
                uid for uid in detection.element_ids
                if rectangle.contains_point(*self.info_by_id[uid].bbox.center)
            }
            if not removed:
                raise ValueError("That rectangle does not overlap any raster-selected objects")
            chosen = set(detection.element_ids) - removed
            method = "Adjusted selection"
        else:
            raise ValueError(f"Unknown selection adjustment: {mode}")
        if not chosen:
            raise ValueError("The adjustment would leave no heatmap objects selected")
        return Detection(self._tight_region(chosen), frozenset(chosen), method, 1.0)

    def _tight_region(self, element_ids: set[str] | frozenset[str]) -> Rect:
        rect = Rect.union_all([self.info_by_id[uid].bbox for uid in element_ids])
        pad = max(self.view_box.width, self.view_box.height) * 0.0005
        return rect.expanded(pad).clipped(self.view_box)

    def _selected_only_root(self, selected_ids: set[str] | frozenset[str], region: Rect, pixel_width: int, pixel_height: int) -> ET.Element:
        root = deepcopy(self.root)

        def prune(parent: ET.Element, protected: bool = False) -> bool:
            retained_selected = False
            for child in list(parent):
                tag = local_name(child.tag)
                if protected or tag in RESOURCE_TAGS:
                    continue
                if tag in CONTAINER_TAGS:
                    child_has_selection = prune(child)
                    if not child_has_selection:
                        parent.remove(child)
                    else:
                        retained_selected = True
                elif child.get("id") in selected_ids:
                    retained_selected = True
                else:
                    parent.remove(child)
            return retained_selected

        prune(root)
        root.set("viewBox", f"{region.x:.12g} {region.y:.12g} {region.width:.12g} {region.height:.12g}")
        root.set("width", str(pixel_width))
        root.set("height", str(pixel_height))
        root.set("preserveAspectRatio", "none")
        return root

    def _dense_regular_grid_render_size(
        self,
        selected_ids: set[str] | frozenset[str],
        region: Rect,
    ) -> tuple[int, int, int, int] | None:
        """Return a one-sample-per-cell render size for a dense axis-aligned grid.

        Origin image plots are commonly exported as one polygon per color-grid
        sample. Rendering a 1024-column grid directly into a large PNG preserves
        the narrow polygon strips, which can form moire when PowerPoint scales
        the figure. Reconstructing the regular sample grid first and applying a
        single smooth enlargement matches Origin's bitmap export behavior.
        """
        if len(selected_ids) < 4096:
            return None
        infos = [self.info_by_id[uid] for uid in selected_ids]
        if any(info.tag not in {"polygon", "rect"} for info in infos):
            return None
        x_centers = sorted({round(info.bbox.center[0], 5) for info in infos})
        y_centers = sorted({round(info.bbox.center[1], 5) for info in infos})
        columns = len(x_centers)
        rows = len(y_centers)
        if columns < 2 or rows < 2 or columns * rows != len(infos):
            return None
        # Use the interpolated grid path only for genuinely dense meshes. The
        # earlier 126 x 81 figures retain their established rendering path.
        if max(columns, rows) < 512:
            return None
        step_x = (x_centers[-1] - x_centers[0]) / (columns - 1)
        step_y = (y_centers[-1] - y_centers[0]) / (rows - 1)
        if step_x <= 0 or step_y <= 0:
            return None
        differences_x = [b - a for a, b in zip(x_centers, x_centers[1:])]
        differences_y = [b - a for a, b in zip(y_centers, y_centers[1:])]
        if any(abs(value - step_x) > step_x * 0.5 for value in differences_x):
            return None
        if any(abs(value - step_y) > step_y * 0.5 for value in differences_y):
            return None
        visible_columns = min(columns + 2, max(2, round(region.width / step_x)))
        visible_rows = min(rows + 2, max(2, round(region.height / step_y)))
        return visible_columns, visible_rows, columns, rows

    def _raster_png(
        self,
        selected_ids: set[str] | frozenset[str],
        region: Rect,
        dpi: int,
        progress: ProgressCallback | None = None,
    ) -> tuple[bytes, int, int]:
        page_width_in = length_to_inches(self.root.get("width"), self.view_box.width)
        page_height_in = length_to_inches(self.root.get("height"), self.view_box.height)
        pixel_width = max(1, round(dpi * page_width_in * region.width / self.view_box.width))
        pixel_height = max(1, round(dpi * page_height_in * region.height / self.view_box.height))
        if pixel_width > 24000 or pixel_height > 24000 or pixel_width * pixel_height > 180_000_000:
            raise ValueError(
                f"The selected region would create a {pixel_width} x {pixel_height} image. "
                "Reduce the DPI or draw a tighter rectangle."
            )
        grid_render = self._dense_regular_grid_render_size(selected_ids, region)
        if grid_render is not None:
            render_width, render_height, grid_columns, grid_rows = grid_render
            supersample = 1
        else:
            # Irregular heatmaps retain the high-resolution vector rendering
            # path. Supersampling avoids seams without changing final DPI.
            target_pixels = pixel_width * pixel_height
            supersample = 2 if target_pixels <= 25_000_000 else 1
            render_width = pixel_width * supersample
            render_height = pixel_height * supersample
        _report(progress, 10, "Building the selected-object render copy")
        selected_root = self._selected_only_root(selected_ids, region, render_width, render_height)
        _flatten_nested_svgs_for_qt(selected_root)
        if grid_render is not None:
            _report(progress, 30, f"Reconstructing the {grid_columns:,} × {grid_rows:,} Origin color grid")
        else:
            _report(progress, 30, f"Rendering seam-free heatmap at {render_width:,} × {render_height:,} pixels")
        renderer = QSvgRenderer(QByteArray(self._serialize(selected_root)))
        if not renderer.isValid():
            raise RuntimeError("The selected heatmap objects could not be rendered")
        image = QImage(render_width, render_height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        try:
            renderer.render(painter, QRectF(0, 0, render_width, render_height))
        finally:
            painter.end()
        if grid_render is not None:
            _report(progress, 70, f"Interpolating the color grid to {pixel_width:,} × {pixel_height:,} pixels")
            image = image.scaled(
                pixel_width,
                pixel_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        elif supersample > 1:
            _report(progress, 70, f"Downsampling cleanly to {pixel_width:,} x {pixel_height:,} pixels")
            image = image.scaled(
                pixel_width,
                pixel_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        _report(progress, 80, "Compressing embedded PNG")
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG", 9):
            raise RuntimeError("Could not encode the heatmap PNG")
        _report(progress, 100, "Embedded PNG ready")
        return bytes(buffer.data()), pixel_width, pixel_height

    @staticmethod
    def _clone_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
        return {child: parent for parent in root.iter() for child in parent}

    @staticmethod
    def _path_to_root(element: ET.Element, parent_map: dict[ET.Element, ET.Element]) -> list[ET.Element]:
        path = [element]
        while path[-1] in parent_map:
            path.append(parent_map[path[-1]])
        path.reverse()
        return path

    def _insert_raster(self, root: ET.Element, selected_ids: frozenset[str], png: bytes, region: Rect) -> None:
        id_map = {e.get("id"): e for e in root.iter() if e.get("id")}
        selected = [id_map[uid] for uid in selected_ids if uid in id_map]
        if not selected:
            raise RuntimeError("Selected SVG objects disappeared during export")
        parent_map = self._clone_parent_map(root)
        paths = [self._path_to_root(element, parent_map) for element in selected]
        common_length = min(len(path) for path in paths)
        lca = root
        for index in range(common_length):
            candidate = paths[0][index]
            if all(path[index] is candidate for path in paths):
                lca = candidate
            else:
                break
        if lca in selected or local_name(lca.tag) not in CONTAINER_TAGS:
            lca = parent_map[lca]

        # Insert where the first selected branch originally appeared. Later axes,
        # labels and annotations remain above the embedded bitmap in z-order.
        direct_branches: list[ET.Element] = []
        for path in paths:
            lca_index = path.index(lca)
            direct_branches.append(path[lca_index + 1])
        child_positions = {child: index for index, child in enumerate(list(lca))}
        insert_at = min(child_positions[branch] for branch in direct_branches)

        cumulative = self._cumulative_matrix(lca, parent_map)
        neutralizer = inverse(cumulative)

        image_element = ET.Element(f"{{{SVG_NS}}}image")
        # PowerPoint does not consistently apply an inverse transform on an
        # embedded image inside Origin's nested SVG viewport. For the common
        # axis-aligned scale/translation case, express the image rectangle in
        # the parent group's native coordinates and omit the transform entirely.
        # This maps to the same root rectangle but survives Office import.
        if abs(cumulative[1]) < 1e-12 and abs(cumulative[2]) < 1e-12:
            image_region = map_rect(neutralizer, region)
            image_transform: Matrix | None = None
        else:
            image_region = region
            image_transform = neutralizer
        image_element.set("x", f"{image_region.x:.12g}")
        image_element.set("y", f"{image_region.y:.12g}")
        image_element.set("width", f"{image_region.width:.12g}")
        image_element.set("height", f"{image_region.height:.12g}")
        image_element.set("preserveAspectRatio", "none")
        # Request high-quality resampling when PowerPoint, Illustrator, or
        # another SVG consumer displays the embedded 600-dpi bitmap at a
        # different size.
        image_element.set("image-rendering", "optimizeQuality")
        # Origin exports SVG 1.1. Use its native xlink form for the broadest
        # PowerPoint/Illustrator compatibility while storing the PNG only once.
        image_element.set(f"{{{XLINK_NS}}}href", "data:image/png;base64," + base64.b64encode(png).decode("ascii"))
        image_element.set("data-origin-hybridizer", "heatmap-raster")
        if image_transform is not None and any(abs(a - b) > 1e-12 for a, b in zip(image_transform, IDENTITY)):
            image_element.set("transform", matrix_string(image_transform))
        lca.insert(insert_at, image_element)

        # Remove only the chosen heatmap primitives. Containers, styles, clipping,
        # text and every non-selected vector object remain from the original SVG.
        # Element.remove() performs a linear search through the parent's child
        # list. Calling it once for every heatmap cell therefore becomes
        # quadratic for Origin files that place hundreds of thousands of cells
        # in one group. Rebuild each affected parent's child list once instead.
        # This preserves the exact order of every retained SVG object (and of
        # the image inserted above), while reducing removal to linear time.
        parent_map = self._clone_parent_map(root)
        selected_by_parent: dict[ET.Element, set[ET.Element]] = {}
        for element in selected:
            parent = parent_map.get(element)
            if parent is not None:
                selected_by_parent.setdefault(parent, set()).add(element)
        for parent, removed_children in selected_by_parent.items():
            parent[:] = [child for child in parent if child not in removed_children]

        for element in root.iter():
            if element.get("id") in self._temp_ids:
                element.attrib.pop("id", None)

    def hybrid_bytes(
        self,
        detection: Detection,
        dpi: int = 600,
        progress: ProgressCallback | None = None,
    ) -> tuple[bytes, int, int]:
        if not detection.element_ids:
            raise ValueError("No heatmap objects are selected")
        png, pixel_width, pixel_height = self._raster_png(
            detection.element_ids,
            detection.rect,
            dpi,
            lambda value, message: _report(progress, round(value * 0.82), message),
        )
        _report(progress, 85, "Replacing vector cells with the embedded image")
        output_root = deepcopy(self.root)
        self._insert_raster(output_root, detection.element_ids, png, detection.rect)
        output_root.set("data-origin-hybridizer", "1.3.3")
        result = self._serialize(output_root)
        _report(progress, 94, "Verifying size, live text, and retained vectors")
        self._verify_hybrid_output(result, detection)
        _report(progress, 100, "Hybrid SVG assembled")
        return result, pixel_width, pixel_height

    def _verify_hybrid_output(self, svg_bytes: bytes, detection: Detection) -> None:
        """Fail export if editable artwork or page geometry changed unexpectedly."""
        output = ET.fromstring(svg_bytes)
        for attribute in ("width", "height", "viewBox"):
            if output.get(attribute) != self.root.get(attribute):
                raise RuntimeError(f"Safety check failed: SVG {attribute} changed during export")
        images = [
            element for element in output.iter()
            if local_name(element.tag) == "image"
            and element.get("data-origin-hybridizer") == "heatmap-raster"
        ]
        if len(images) != 1:
            raise RuntimeError("Safety check failed: the hybrid SVG does not contain exactly one heatmap image")
        source_text = [
            ET.tostring(element, encoding="unicode")
            for element in self.root.iter() if local_name(element.tag) == "text"
        ]
        output_text = [
            ET.tostring(element, encoding="unicode")
            for element in output.iter() if local_name(element.tag) == "text"
        ]
        if source_text != output_text:
            raise RuntimeError("Safety check failed: editable SVG text changed during export")
        source_vectors = []
        for element in self.root.iter():
            if local_name(element.tag) not in DRAWABLE_TAGS or element.get("id") in detection.element_ids:
                continue
            attributes = dict(element.attrib)
            if attributes.get("id") in self._temp_ids:
                attributes.pop("id", None)
            source_vectors.append((local_name(element.tag), tuple(sorted(attributes.items())), element.text, element.tail))
        output_vectors = [
            (local_name(element.tag), tuple(sorted(element.attrib.items())), element.text, element.tail)
            for element in output.iter() if local_name(element.tag) in DRAWABLE_TAGS
        ]
        if source_vectors != output_vectors:
            raise RuntimeError(
                "Safety check failed: retained vector coordinates, colors, line widths, or styles changed"
            )
        expected_drawables = sum(
            local_name(element.tag) in DRAWABLE_TAGS for element in self.root.iter()
        ) - detection.count
        output_drawables = sum(
            local_name(element.tag) in DRAWABLE_TAGS for element in output.iter()
        )
        if output_drawables != expected_drawables:
            raise RuntimeError("Safety check failed: an unexpected vector object changed during export")

    def export_hybrid_svg(
        self,
        output_path: str | Path,
        detection: Detection,
        dpi: int = 600,
        progress: ProgressCallback | None = None,
    ) -> ExportStats:
        output_path = Path(output_path)
        svg_bytes, pixel_width, pixel_height = self.hybrid_bytes(
            detection,
            dpi,
            lambda value, message: _report(progress, round(value * 0.95), message),
        )
        _report(progress, 98, "Writing hybrid SVG to disk")
        output_path.write_bytes(svg_bytes)
        _report(progress, 100, "Hybrid SVG written")
        return ExportStats(
            output_path=output_path,
            rasterized_elements=detection.count,
            raster_width=pixel_width,
            raster_height=pixel_height,
            source_bytes=len(self.source_bytes),
            output_bytes=len(svg_bytes),
            region=detection.rect,
            editable_text_objects=self.classification_summary(detection).editable_text_objects,
            editable_vector_objects=self.classification_summary(detection).editable_vector_objects,
            verification_message=(
                "Verified: size, text, and retained vector coordinates/styles match the source SVG"
            ),
        )
