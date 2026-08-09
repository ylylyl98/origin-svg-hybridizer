from __future__ import annotations

from pathlib import Path
import math
import sys
import tempfile
import time

from PySide6.QtCore import QByteArray, QObject, QPointF, QRectF, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QDragEnterEvent, QDropEvent, QFont, QIcon, QImage, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .exporters import (
    export_emf_with_powerpoint,
    export_pdf_with_qt,
    export_with_inkscape,
    find_inkscape,
    powerpoint_available,
)
from .geometry import Rect
from .svg_document import (
    Detection,
    ExportStats,
    SvgDocument,
    qt_preview_bytes,
    render_qt_svg_preview,
)


APP_STYLE = """
QMainWindow, QWidget { background: #f4f6f8; color: #1f2933; }
QGroupBox {
    background: white; border: 1px solid #d9e0e7; border-radius: 10px;
    margin-top: 13px; padding: 14px 12px 12px 12px; font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QPushButton {
    background: #edf1f5; border: 1px solid #cbd5df; border-radius: 7px;
    padding: 8px 12px; font-weight: 600;
}
QPushButton:hover { background: #e3eaf1; }
QPushButton:pressed { background: #d7e0e8; }
QPushButton#primaryButton { background: #1769e0; color: white; border-color: #1769e0; }
QPushButton#primaryButton:hover { background: #0f5dcc; }
QPushButton:disabled { background: #e8ebee; color: #8a949e; border-color: #d9dee3; }
QSpinBox { background: white; border: 1px solid #cbd5df; border-radius: 6px; padding: 5px; }
QLabel#muted { color: #637381; }
QLabel#fileName { font-weight: 650; color: #172b4d; }
QFrame#summaryCard { background: #eef5ff; border: 1px solid #ccdef9; border-radius: 8px; }
QStatusBar { background: white; border-top: 1px solid #d9e0e7; }
"""


def application_icon_path() -> Path:
    """Return the icon path in both source and PyInstaller builds."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return bundle_root / "packaging" / "origin-svg-hybridizer.ico"


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)


class BackgroundTask(QRunnable):
    def __init__(self, function) -> None:
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(self.signals.progress.emit)
        except Exception as exc:
            self.signals.failed.emit(str(exc))
        else:
            self.signals.succeeded.emit(result)


class SvgCanvas(QWidget):
    rectangle_drawn = Signal(object)
    file_dropped = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(620, 500)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.renderer: QSvgRenderer | None = None
        self.preview_image: QImage | None = None
        self.preview_key: tuple[int, int, float] | None = None
        self.view_box: Rect | None = None
        self.overlay: Rect | None = None
        self.overlay_label = ""
        self.manual_mode = False
        self.drag_start: QPointF | None = None
        self.drag_current: QPointF | None = None

    def set_svg(self, svg_bytes: bytes, view_box: Rect) -> None:
        renderer = QSvgRenderer(QByteArray(svg_bytes))
        if not renderer.isValid():
            raise ValueError("The preview renderer could not open this SVG")
        self.renderer = renderer
        self.preview_image = None
        self.preview_key = None
        self.view_box = view_box
        self.update()

    def set_preview_image(self, image: QImage, view_box: Rect) -> None:
        self.renderer = None
        self.preview_image = image
        self.preview_key = (-1, -1, -1.0)
        self.view_box = view_box
        self.update()

    def set_overlay(self, rect: Rect | None, label: str = "") -> None:
        self.overlay = rect
        self.overlay_label = label
        self.update()

    def set_manual_mode(self, enabled: bool) -> None:
        self.manual_mode = enabled
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.drag_start = None
        self.drag_current = None
        self.update()

    def _page_rect(self) -> QRectF:
        margin = 26.0
        available = QRectF(margin, margin, max(1.0, self.width() - 2 * margin), max(1.0, self.height() - 2 * margin))
        if not self.view_box:
            return available
        scale = min(available.width() / self.view_box.width, available.height() / self.view_box.height)
        width, height = self.view_box.width * scale, self.view_box.height * scale
        return QRectF(
            available.center().x() - width / 2,
            available.center().y() - height / 2,
            width,
            height,
        )

    def _widget_to_svg(self, point: QPointF) -> QPointF:
        assert self.view_box is not None
        page = self._page_rect()
        x = self.view_box.x + (point.x() - page.x()) / page.width() * self.view_box.width
        y = self.view_box.y + (point.y() - page.y()) / page.height() * self.view_box.height
        return QPointF(x, y)

    def _svg_to_widget(self, rect: Rect) -> QRectF:
        assert self.view_box is not None
        page = self._page_rect()
        x = page.x() + (rect.x - self.view_box.x) / self.view_box.width * page.width()
        y = page.y() + (rect.y - self.view_box.y) / self.view_box.height * page.height()
        width = rect.width / self.view_box.width * page.width()
        height = rect.height / self.view_box.height * page.height()
        return QRectF(x, y, width, height)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#e8edf2"))
        if (not self.renderer and self.preview_image is None) or not self.view_box:
            painter.setPen(QColor("#52616f"))
            font = QFont(self.font())
            font.setPointSize(13)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Drop an Origin SVG here\n\nor click Open SVG")
            return

        page = self._page_rect()
        painter.fillRect(page.adjusted(-2, 3, 4, 6), QColor(0, 0, 0, 28))
        painter.fillRect(page, Qt.GlobalColor.white)
        # Origin frequently wraps the plot in a nested SVG with a user-space
        # mask. Qt applies that mask incorrectly whenever the destination painter
        # has a device offset, making the figure look completely blank. Render to
        # an origin-aligned off-screen image, cache it, then place that image on
        # the canvas. This bitmap is preview-only; export still uses the original
        # SVG objects plus the selected high-resolution heatmap raster.
        pixel_ratio = self.devicePixelRatioF()
        preview_width = max(1, math.ceil(page.width() * pixel_ratio))
        preview_height = max(1, math.ceil(page.height() * pixel_ratio))
        preview_key = (preview_width, preview_height, round(pixel_ratio, 3))
        if self.renderer is not None and (self.preview_image is None or self.preview_key != preview_key):
            preview = QImage(preview_width, preview_height, QImage.Format.Format_ARGB32_Premultiplied)
            preview.fill(QColor(0, 0, 0, 0))
            preview_painter = QPainter(preview)
            preview_painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self.renderer.render(preview_painter, QRectF(0, 0, preview_width, preview_height))
            preview_painter.end()
            self.preview_image = preview
            self.preview_key = preview_key
        if self.preview_image is not None:
            painter.drawImage(page, self.preview_image)

        display_rect: QRectF | None = None
        label = self.overlay_label
        if self.drag_start is not None and self.drag_current is not None:
            display_rect = QRectF(self.drag_start, self.drag_current).normalized()
            label = "Manual selection"
        elif self.overlay:
            display_rect = self._svg_to_widget(self.overlay)
        if display_rect:
            # Orange always means "will become the embedded raster". Editable
            # vectors and live text are blue/green in the optional review view.
            color = QColor("#f08c2e")
            painter.fillRect(display_rect, QColor(color.red(), color.green(), color.blue(), 32))
            pen = QPen(color, 2.0, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawRect(display_rect)
            if label:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                metrics = painter.fontMetrics()
                text_rect = QRectF(metrics.boundingRect(label)).adjusted(-6, -3, 6, 3)
                text_rect.moveBottomLeft(display_rect.topLeft() + QPointF(0, -4))
                painter.drawRoundedRect(text_rect, 4, 4)
                painter.setPen(Qt.GlobalColor.white)
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.manual_mode and (self.renderer or self.preview_image is not None) and event.button() == Qt.MouseButton.LeftButton:
            if self._page_rect().contains(event.position()):
                self.drag_start = event.position()
                self.drag_current = event.position()
                self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self.drag_start is not None:
            page = self._page_rect()
            x = min(max(event.position().x(), page.left()), page.right())
            y = min(max(event.position().y(), page.top()), page.bottom())
            self.drag_current = QPointF(x, y)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self.drag_start is None or self.drag_current is None or not self.view_box:
            return
        start = self._widget_to_svg(self.drag_start)
        end = self._widget_to_svg(self.drag_current)
        self.drag_start = None
        self.drag_current = None
        rect = Rect(start.x(), start.y(), end.x() - start.x(), end.y() - start.y()).normalized()
        if rect.width > self.view_box.width * 0.003 and rect.height > self.view_box.height * 0.003:
            self.rectangle_drawn.emit(rect)
        self.update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        if any(path.suffix.lower() == ".svg" for path in paths):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".svg":
                self.file_dropped.emit(str(path))
                event.acceptProposedAction()
                return


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.document: SvgDocument | None = None
        self.detection: Detection | None = None
        self.last_hybrid_path: Path | None = None
        self.original_preview: QImage | None = None
        self.review_preview: QImage | None = None
        self.thread_pool = QThreadPool.globalInstance()
        self.active_task: BackgroundTask | None = None
        self.setWindowTitle("Origin SVG Hybridizer")
        icon_path = application_icon_path()
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1180, 760)
        self.setMinimumSize(960, 650)
        self.setAcceptDrops(True)

        self.canvas = SvgCanvas()
        self.canvas.file_dropped.connect(self.load_svg)
        self.canvas.rectangle_drawn.connect(self.apply_manual_rectangle)
        self.setCentralWidget(self._build_content())
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready - open or drop an Origin SVG")
        self._build_menu()
        self._set_document_controls(False)

    def _build_content(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)
        layout.addWidget(self.canvas, 1)

        panel = QWidget()
        panel.setFixedWidth(325)
        sidebar = QVBoxLayout(panel)
        sidebar.setContentsMargins(0, 0, 0, 0)
        sidebar.setSpacing(10)

        title = QLabel("Origin SVG Hybridizer")
        title_font = QFont(title.font())
        title_font.setPointSize(17)
        title_font.setBold(True)
        title.setFont(title_font)
        subtitle = QLabel("Compact heatmaps. Editable labels and vectors.")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        sidebar.addWidget(title)
        sidebar.addWidget(subtitle)

        file_group = QGroupBox("1  Open figure")
        file_layout = QVBoxLayout(file_group)
        self.open_button = QPushButton("Open SVG...")
        self.open_button.clicked.connect(self.open_dialog)
        self.file_name = QLabel("No file selected")
        self.file_name.setObjectName("fileName")
        self.file_name.setWordWrap(True)
        file_layout.addWidget(self.open_button)
        file_layout.addWidget(self.file_name)
        sidebar.addWidget(file_group)

        select_group = QGroupBox("2  Select the heatmap")
        select_layout = QVBoxLayout(select_group)
        self.auto_button = QPushButton("Auto-detect dense region")
        self.auto_button.clicked.connect(self.auto_detect)
        self.manual_checkbox = QCheckBox("Adjust with a rectangle")
        self.manual_checkbox.toggled.connect(self.canvas.set_manual_mode)
        self.manual_checkbox.toggled.connect(self._manual_toggled)
        self.adjust_mode = QComboBox()
        self.adjust_mode.addItem("Replace raster selection", "replace")
        self.adjust_mode.addItem("Add area to raster", "add")
        self.adjust_mode.addItem("Keep area editable", "exclude")
        self.adjust_mode.setToolTip("Choose what the next drawn rectangle should do")
        self.selection_summary = QLabel("No selection")
        self.selection_summary.setObjectName("muted")
        self.selection_summary.setWordWrap(True)
        select_layout.addWidget(self.auto_button)
        select_layout.addWidget(self.manual_checkbox)
        select_layout.addWidget(self.adjust_mode)
        select_layout.addWidget(self.selection_summary)

        self.review_checkbox = QCheckBox("Show editability review colors")
        self.review_checkbox.setChecked(True)
        self.review_checkbox.toggled.connect(self._refresh_canvas_preview)
        review_key = QLabel(
            '<span style="color:#f08c2e"><b>Orange</b></span> raster area &nbsp; '
            '<span style="color:#1769e0"><b>Blue</b></span> vector &nbsp; '
            '<span style="color:#169c52"><b>Green</b></span> live text'
        )
        review_key.setObjectName("muted")
        review_key.setWordWrap(True)
        select_layout.addWidget(self.review_checkbox)
        select_layout.addWidget(review_key)
        sidebar.addWidget(select_group)

        settings_group = QGroupBox("3  Output quality")
        settings_layout = QVBoxLayout(settings_group)
        dpi_row = QHBoxLayout()
        dpi_label = QLabel("Raster DPI")
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 2400)
        self.dpi_spin.setSingleStep(50)
        self.dpi_spin.setValue(600)
        self.dpi_spin.setSuffix(" dpi")
        dpi_row.addWidget(dpi_label)
        dpi_row.addStretch()
        dpi_row.addWidget(self.dpi_spin)
        settings_layout.addLayout(dpi_row)
        quality_note = QLabel("600 dpi is recommended for publication figures. Only the selected heatmap is rasterized.")
        quality_note.setObjectName("muted")
        quality_note.setWordWrap(True)
        settings_layout.addWidget(quality_note)
        sidebar.addWidget(settings_group)

        export_group = QGroupBox("4  Export")
        export_layout = QVBoxLayout(export_group)
        self.svg_button = QPushButton("Export hybrid SVG...")
        self.svg_button.setObjectName("primaryButton")
        self.svg_button.clicked.connect(self.export_svg)
        export_layout.addWidget(self.svg_button)
        secondary = QHBoxLayout()
        self.emf_button = QPushButton("EMF...")
        self.emf_button.setToolTip("Optional PowerPoint export; uses Inkscape or desktop PowerPoint")
        self.emf_button.clicked.connect(lambda: self.export_secondary("emf"))
        self.pdf_button = QPushButton("PDF...")
        self.pdf_button.setToolTip("Optional PDF export")
        self.pdf_button.clicked.connect(lambda: self.export_secondary("pdf"))
        secondary.addWidget(self.emf_button)
        secondary.addWidget(self.pdf_button)
        export_layout.addLayout(secondary)
        if find_inkscape():
            emf_note = "EMF: Inkscape detected"
        elif powerpoint_available():
            emf_note = "EMF: PowerPoint converter available"
        else:
            emf_note = "EMF needs Inkscape or desktop PowerPoint"
        self.inkscape_note = QLabel(emf_note)
        self.inkscape_note.setObjectName("muted")
        export_layout.addWidget(self.inkscape_note)
        sidebar.addWidget(export_group)

        self.result_card = QFrame()
        self.result_card.setObjectName("summaryCard")
        result_layout = QVBoxLayout(self.result_card)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        result_layout.addWidget(self.result_label)
        self.result_card.hide()
        sidebar.addWidget(self.result_card)
        sidebar.addStretch()
        layout.addWidget(panel)
        return root

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("Open SVG...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_dialog)
        export_action = QAction("Export hybrid SVG...", self)
        export_action.setShortcut("Ctrl+S")
        export_action.triggered.connect(self.export_svg)
        quit_action = QAction("Exit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(open_action)
        file_menu.addAction(export_action)
        file_menu.addSeparator()
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def _set_document_controls(self, enabled: bool) -> None:
        for widget in (
            self.auto_button, self.manual_checkbox, self.adjust_mode, self.review_checkbox,
            self.svg_button, self.emf_button, self.pdf_button,
        ):
            widget.setEnabled(enabled)

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open Origin SVG", "", "SVG figures (*.svg)")
        if path:
            self.load_svg(path)

    def load_svg(self, path: str) -> None:
        source_path = Path(path)

        def work(report):
            document = SvgDocument(
                path,
                lambda value, message: report(round(value * 0.62), message),
            )
            detection = None
            detection_error = None
            try:
                detection = document.detect_heatmap(
                    progress=lambda value, message: report(62 + round(value * 0.18), message)
                )
            except Exception as exc:
                detection_error = str(exc)
            report(82, "Rendering bounded-size preview")
            preview = document.render_preview()
            review = None
            if detection is not None:
                report(91, "Building the editability review")
                review = document.render_review_preview(detection)
            report(100, "SVG ready")
            return document, detection, detection_error, preview, review

        def loaded(result) -> None:
            document, detection, detection_error, preview, review = result
            self.document = document
            self.detection = None
            self.last_hybrid_path = None
            self.original_preview = preview
            self.review_preview = review
            self.canvas.set_preview_image(preview, document.view_box)
            self.canvas.set_overlay(None)
            self.file_name.setText(source_path.name)
            self.result_card.hide()
            self._set_document_controls(True)
            if detection is not None:
                self._set_detection(detection, review)
                self.manual_checkbox.setChecked(False)
                self.statusBar().showMessage(
                    f"Loaded {source_path.name} - auto-detected {detection.count:,} heatmap objects"
                )
            else:
                self.selection_summary.setText(
                    "Automatic detection needs help. Select Adjust with a rectangle, then drag around the heatmap."
                )
                self.manual_checkbox.setChecked(True)
                self.statusBar().showMessage(
                    f"Loaded {source_path.name} - {len(document.infos):,} filled objects; draw the heatmap rectangle"
                )
                if detection_error:
                    self.selection_summary.setToolTip(detection_error)

        def load_failed(message: str) -> None:
            self.file_name.setText("Could not load SVG")
            self.statusBar().showMessage("SVG loading failed")
            QMessageBox.critical(self, "Could not open SVG", message)

        size_mb = source_path.stat().st_size / 1024 / 1024 if source_path.is_file() else 0
        self.file_name.setText(f"Loading {source_path.name}...")
        self.statusBar().showMessage(f"Reading and indexing {size_mb:.1f} MB SVG in the background...")
        self._start_background(
            "Loading and analyzing the SVG...",
            work,
            loaded,
            "Could not open SVG",
            load_failed,
        )

    def auto_detect(self, silent: bool = False) -> None:
        if not self.document:
            return
        document = self.document

        def work(report):
            detection = document.detect_heatmap(
                progress=lambda value, message: report(round(value * 0.72), message)
            )
            report(78, "Building the editability review")
            review = document.render_review_preview(detection)
            report(100, "Automatic selection ready")
            return detection, review

        def detected(result) -> None:
            detection, review = result
            self._set_detection(detection, review)
            self.manual_checkbox.setChecked(False)
            self.statusBar().showMessage(
                f"Auto-detected {detection.count:,} dense heatmap objects - review the orange raster outline"
            )

        def failed(message: str) -> None:
            self.detection = None
            self.canvas.set_overlay(None)
            self.selection_summary.setText("Automatic detection needs help. Select Adjust with a rectangle, then drag around the heatmap.")
            self.manual_checkbox.setChecked(True)
            if not silent:
                QMessageBox.information(self, "Draw the heatmap area", message)

        self._start_background(
            "Detecting dense heatmap regions...",
            work,
            detected,
            "Detection failed",
            failed,
        )

    def _manual_toggled(self, enabled: bool) -> None:
        if enabled:
            action = self.adjust_mode.currentText().lower()
            self.statusBar().showMessage(f"Drag a rectangle to {action}")

    def apply_manual_rectangle(self, rect: Rect) -> None:
        if not self.document:
            return
        document = self.document
        mode = self.adjust_mode.currentData()
        current = self.detection

        def work(report):
            report(10, "Finding eligible filled objects inside the rectangle")
            if mode == "replace" or current is None:
                detection = document.select_rectangle(rect)
            else:
                detection = document.adjust_detection(current, rect, mode)
            report(70, "Building the editability review")
            review = document.render_review_preview(detection)
            report(100, "Selection adjustment ready")
            return detection, review

        def selected(result) -> None:
            detection, review = result
            self._set_detection(detection, review)
            self.statusBar().showMessage(f"Manual selection contains {detection.count:,} heatmap objects")

        def failed(message: str) -> None:
            QMessageBox.warning(self, "Nothing selected", message)

        self._start_background(
            "Selecting heatmap objects inside the rectangle...",
            work,
            selected,
            "Nothing selected",
            failed,
        )

    def _set_detection(self, detection: Detection, review_preview: QImage | None = None) -> None:
        self.detection = detection
        if review_preview is not None:
            self.review_preview = review_preview
        self.canvas.set_overlay(detection.rect, f"{detection.method}: {detection.count:,} objects")
        confidence = f" ({detection.confidence:.0%} confidence)" if detection.method == "Automatic" else ""
        summary = self.document.classification_summary(detection) if self.document else None
        detail = ""
        if summary:
            detail = (
                f"\nKept editable: {summary.editable_text_objects:,} text objects and "
                f"{summary.editable_vector_objects:,} vector objects."
            )
        self.selection_summary.setText(
            f"{detection.method}{confidence}\n{detection.count:,} dense objects will become one embedded PNG.{detail}"
        )
        self._refresh_canvas_preview()

    def _refresh_canvas_preview(self) -> None:
        if not self.document:
            return
        image = self.review_preview if self.review_checkbox.isChecked() and self.review_preview is not None else self.original_preview
        if image is not None:
            self.canvas.set_preview_image(image, self.document.view_box)

    def _require_ready(self) -> bool:
        if not self.document:
            QMessageBox.information(self, "Open an SVG", "Open or drop an Origin SVG first.")
            return False
        if not self.detection:
            QMessageBox.information(self, "Select the heatmap", "Auto-detect the heatmap or draw a rectangle around it first.")
            return False
        return True

    def _progress(self, message: str) -> QProgressDialog:
        dialog = QProgressDialog(message, "", 0, 100, self)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.setLabelText(message + "\nLarge Origin files may take a little while; the app will remain responsive.")
        dialog.setValue(0)
        dialog.show()
        QApplication.processEvents()
        return dialog

    def _set_busy(self, busy: bool) -> None:
        self.open_button.setEnabled(not busy)
        self._set_document_controls(not busy and self.document is not None)

    def _start_background(
        self,
        message: str,
        function,
        on_success,
        error_title: str,
        on_error=None,
    ) -> None:
        if self.active_task is not None:
            self.statusBar().showMessage("Please wait for the current operation to finish")
            return
        progress = self._progress(message)
        task = BackgroundTask(function)
        self.active_task = task
        self._set_busy(True)
        started_at = time.monotonic()
        progress_state = {"value": 0, "message": message}
        elapsed_timer = QTimer(self)
        elapsed_timer.setInterval(500)

        def refresh_label() -> None:
            elapsed = time.monotonic() - started_at
            progress.setLabelText(
                f"{progress_state['message']}\n"
                f"{progress_state['value']}% complete · elapsed {elapsed:.1f} seconds"
            )

        def update_progress(value: int, stage_message: str) -> None:
            progress_state["value"] = max(progress_state["value"], value)
            progress_state["message"] = stage_message
            progress.setValue(progress_state["value"])
            refresh_label()

        elapsed_timer.timeout.connect(refresh_label)
        elapsed_timer.start()

        def succeeded(result) -> None:
            elapsed_timer.stop()
            progress.setValue(100)
            progress.close()
            self.active_task = None
            try:
                on_success(result)
            except Exception as exc:
                QMessageBox.critical(self, error_title, str(exc))
            self._set_busy(False)

        def failed(error_message: str) -> None:
            elapsed_timer.stop()
            progress.close()
            self.active_task = None
            if on_error is not None:
                on_error(error_message)
            else:
                QMessageBox.critical(self, error_title, error_message)
            self._set_busy(False)

        task.signals.succeeded.connect(succeeded)
        task.signals.failed.connect(failed)
        task.signals.progress.connect(update_progress)
        self.thread_pool.start(task)

    def export_svg(self) -> None:
        if not self._require_ready():
            return
        assert self.document is not None and self.detection is not None
        suggested = self.document.path.with_name(self.document.path.stem + "_hybrid.svg")
        path, _ = QFileDialog.getSaveFileName(self, "Export hybrid SVG", str(suggested), "SVG figure (*.svg)")
        if not path:
            return
        if not path.lower().endswith(".svg"):
            path += ".svg"
        document, detection, dpi = self.document, self.detection, self.dpi_spin.value()

        def work(report):
            stats = document.export_hybrid_svg(
                path,
                detection,
                dpi,
                lambda value, message: report(round(value * 0.88), message),
            )
            report(90, "Generating compact result preview")
            preview_bytes = qt_preview_bytes(Path(path).read_bytes())
            preview = render_qt_svg_preview(preview_bytes, document.view_box)
            report(100, "Hybrid SVG saved")
            return stats, preview

        def exported(result) -> None:
            stats, preview = result
            self.last_hybrid_path = Path(path)
            self.original_preview = preview
            self.review_checkbox.setChecked(False)
            self.canvas.set_preview_image(preview, document.view_box)
            self.canvas.set_overlay(None)
            self._show_export_result(stats)
            self.statusBar().showMessage(f"Hybrid SVG saved: {path}")

        self._start_background("Rasterizing only the selected heatmap...", work, exported, "Export failed")

    def _show_export_result(self, stats: ExportStats) -> None:
        ratio = stats.output_bytes / stats.source_bytes if stats.source_bytes else 0
        self.result_label.setText(
            f"Saved {stats.output_path.name}\n"
            f"Heatmap: {stats.raster_width:,} x {stats.raster_height:,} px\n"
            f"Rasterized: {stats.rasterized_elements:,} objects\n"
            f"Editable: {stats.editable_text_objects:,} text + {stats.editable_vector_objects:,} vectors\n"
            f"Safety check: passed\n"
            f"Output size: {stats.output_bytes / 1024 / 1024:.2f} MB ({ratio:.0%} of source)"
        )
        self.result_card.show()

    def export_secondary(self, export_type: str) -> None:
        if not self._require_ready():
            return
        assert self.document is not None and self.detection is not None
        suggested = self.document.path.with_name(self.document.path.stem + f"_hybrid.{export_type}")
        label = "Enhanced Metafile (*.emf)" if export_type == "emf" else "PDF figure (*.pdf)"
        path, _ = QFileDialog.getSaveFileName(self, f"Export {export_type.upper()}", str(suggested), label)
        if not path:
            return
        if not path.lower().endswith(f".{export_type}"):
            path += f".{export_type}"
        document, detection, dpi = self.document, self.detection, self.dpi_spin.value()
        output_path = Path(path)

        def work(report):
            svg_bytes, _, _ = document.hybrid_bytes(
                detection,
                dpi,
                lambda value, message: report(round(value * 0.72), message),
            )
            output_path = Path(path)
            with tempfile.TemporaryDirectory(prefix="origin-hybridizer-") as temp_dir:
                temp_svg = Path(temp_dir) / "hybrid.svg"
                temp_svg.write_bytes(svg_bytes)
                if export_type == "emf":
                    report(78, "Converting hybrid SVG to EMF")
                    if find_inkscape():
                        export_with_inkscape(temp_svg, output_path, "emf")
                    else:
                        export_emf_with_powerpoint(temp_svg, output_path)
                elif find_inkscape():
                    report(78, "Converting hybrid SVG to PDF with Inkscape")
                    export_with_inkscape(temp_svg, output_path, "pdf")
                else:
                    report(78, "Rendering vector PDF")
                    export_pdf_with_qt(
                        svg_bytes,
                        output_path,
                        document.view_box,
                        document.width_attribute,
                        document.height_attribute,
                    )
            report(100, f"{export_type.upper()} saved")
            return output_path

        def exported_secondary(saved_path: Path) -> None:
            self.statusBar().showMessage(f"{export_type.upper()} saved: {path}")
            QMessageBox.information(
                self,
                f"{export_type.upper()} saved",
                f"Created {saved_path.name}. Keep the hybrid SVG as the editable master figure.",
            )

        self._start_background(
            f"Creating optional {export_type.upper()} output...",
            work,
            exported_secondary,
            f"Could not export {export_type.upper()}",
        )

    def show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Origin SVG Hybridizer",
            "Origin SVG Hybridizer 1.3.2\n\n"
            "Replaces only dense heatmap primitives with an embedded high-resolution PNG. "
            "Text, axes, ticks, arrows and annotations stay as the original SVG objects.",
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Origin SVG Hybridizer")
    app.setOrganizationName("OpenAI")
    icon_path = application_icon_path()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    if len(sys.argv) > 1 and Path(sys.argv[1]).suffix.lower() == ".svg":
        window.load_svg(sys.argv[1])
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
