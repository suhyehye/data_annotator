"""Data Annotator - point-style inspection annotation tool.

Build a standalone exe with PyInstaller (single file, icon embedded):

    pip install pyinstaller pyqt5
    pyinstaller --onefile --windowed --name DataAnnotator ^
        --icon icon.ico --add-data "icon.ico;." main.py

Usage:
    - Open Folder: loads every image under the folder (recurses into subfolders).
                   Relative paths (e.g. "blue_cuboid_potentiometer/foo.jpg") are
                   used as keys, matching the annotations.json schema.
    - Open Image:  single-image session.
    - Load Annotations: merge an existing annotations.json into the session so
                        prior work is shown immediately.
    - Save: writes annotations.json to the dataset root (or beside the image).

Annotation model matches the reference annotations.json:
    {
        "<rel_path>": {
            "<attribute> <class>": {
                "class": "...",
                "attribute": "...",
                "points": [[x, y], ...],
                "type": "inspection"
            }
        }
    }
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore, QtGui, QtWidgets


def resource_path(name: str) -> str:
    """Resolve a bundled resource path for both dev runs and PyInstaller onefile.

    PyInstaller extracts ``--add-data`` files under ``sys._MEIPASS``; in dev we
    fall back to the script directory.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


# ----------------------------- configuration ------------------------------- #

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}

# Default class -> attribute schema, derived from the reference dataset.
DEFAULT_SCHEMA: dict[str, list[str]] = {
    "blue cuboid potentiometer": ["normal", "cracked", "broken", "bent lead"],
    "bolt":                      ["normal", "contaminated", "curved", "scratched"],
    "capacitor":                 ["normal", "peeled", "ruptured", "truncated", "without terminals"],
    "cylindrical capacitor":     ["normal", "gouged", "scratched"],
    "fuse":                      ["normal", "broken", "crushed", "damaged terminal"],
    "gear":                      ["normal", "broken", "worn"],
    "nut":                       ["normal", "contaminated"],
    "plastic cap":               ["normal", "broken", "cracked", "peeled"],
    "plastic part":              ["normal", "bent head"],
    "plastic tube":              ["normal", "shrunk", "torn"],
    "switch connector":          ["normal", "bent lead", "crushed"],
    "washer":                    ["normal", "contaminated", "curved", "scratched"],
}

# Stable color per attribute so they're visually distinct at a glance.
ATTR_COLORS: dict[str, str] = {
    "normal":            "#22c55e",
    "cracked":           "#ef4444",
    "broken":            "#dc2626",
    "bent lead":         "#f97316",
    "bent head":         "#fb923c",
    "contaminated":      "#a855f7",
    "curved":            "#06b6d4",
    "scratched":         "#facc15",
    "peeled":            "#ec4899",
    "ruptured":          "#991b1b",
    "truncated":         "#d97706",
    "without terminals": "#0ea5e9",
    "gouged":            "#7c3aed",
    "crushed":           "#84cc16",
    "damaged terminal":  "#0891b2",
    "worn":              "#9333ea",
    "shrunk":            "#e11d48",
    "torn":              "#be185d",
}

_FALLBACK_COLORS = [
    "#f43f5e", "#10b981", "#3b82f6", "#eab308", "#8b5cf6",
    "#14b8a6", "#f59e0b", "#ec4899", "#06b6d4", "#84cc16",
]


def _stable_hash(s: str) -> int:
    """FNV-1a hash — stable across runs (unlike the salted built-in hash)."""
    h = 2166136261
    for ch in s:
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def base_color_for(attribute: str) -> str:
    if attribute in ATTR_COLORS:
        return ATTR_COLORS[attribute]
    idx = _stable_hash(attribute) % len(_FALLBACK_COLORS)
    return _FALLBACK_COLORS[idx]


def color_for(cls: str, attribute: str = "") -> str:
    """Color for a (class, attribute) pair.

    The attribute sets the base hue (so e.g. 'normal' stays green-ish
    everywhere), while the class applies a deterministic hue/brightness shift
    so the *same* attribute on a *different* class is still visually distinct.
    """
    base = QtGui.QColor(base_color_for(attribute))
    if not cls:
        return base.name()
    h, s, v, a = base.getHsv()
    seed = _stable_hash(cls)
    h = (h + ((seed % 11) - 5) * 22) % 360                 # ±110° hue shift
    s = max(80, min(255, s + (((seed // 11) % 5) - 2) * 30))
    v = max(120, min(255, v + (((seed // 55) % 5) - 2) * 26))
    return QtGui.QColor.fromHsv(int(h), int(s), int(v), a).name()


def entry_key(cls: str, attr: str) -> str:
    return f"{attr} {cls}"


# ------------------------------- viewer ----------------------------------- #

class PointMarker(QtWidgets.QGraphicsObject):
    """Draggable annotation dot. Drawn at constant screen size."""

    moved = QtCore.pyqtSignal(object, QtCore.QPointF)
    right_clicked = QtCore.pyqtSignal(object)
    left_clicked = QtCore.pyqtSignal(object)
    drag_finished = QtCore.pyqtSignal(object, QtCore.QPointF)

    RADIUS = 9

    def __init__(self, color: str, label: str, parent=None):
        super().__init__(parent)
        self._color = QtGui.QColor(color)
        self._label = label
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsScenePositionChanges, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(10)
        self.entry_key: Optional[str] = None
        self.point_index: int = -1
        self._hover = False
        self._label_visible = True
        self._drag_start_pos: Optional[QtCore.QPointF] = None

        self._font = QtGui.QFont()
        self._font.setPointSize(9)
        self._font.setBold(True)
        fm = QtGui.QFontMetricsF(self._font)
        self._label_rect = fm.boundingRect(self._label)

    def set_label(self, label: str):
        self._label = label
        fm = QtGui.QFontMetricsF(self._font)
        self._label_rect = fm.boundingRect(self._label)
        self.prepareGeometryChange()
        self.update()

    def set_color(self, color: str):
        self._color = QtGui.QColor(color)
        self.update()

    def set_label_visible(self, visible: bool):
        if visible == self._label_visible:
            return
        self._label_visible = visible
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QtCore.QRectF:
        r = self.RADIUS
        if not self._label_visible:
            m = r + 3
            return QtCore.QRectF(-m, -m, m * 2, m * 2)
        label_w = self._label_rect.width() + 12
        label_h = self._label_rect.height() + 6
        w = r * 2 + 8 + label_w
        h = max(r * 2, label_h) + 6
        return QtCore.QRectF(-r - 3, -h / 2, w, h)

    def paint(self, painter: QtGui.QPainter, option, widget=None):
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        r = self.RADIUS
        # selection/hover ring
        if self._hover or self.isSelected():
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 3))
            painter.drawEllipse(QtCore.QPointF(0, 0), r + 3, r + 3)
        # dot
        painter.setBrush(QtGui.QBrush(self._color))
        painter.setPen(QtGui.QPen(QtGui.QColor("white"), 2))
        painter.drawEllipse(QtCore.QPointF(0, 0), r, r)
        # crosshair
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 220), 1.2))
        painter.drawLine(QtCore.QPointF(-r + 3, 0), QtCore.QPointF(r - 3, 0))
        painter.drawLine(QtCore.QPointF(0, -r + 3), QtCore.QPointF(0, r - 3))
        # label pill
        if not self._label_visible:
            return
        painter.setFont(self._font)
        lw = self._label_rect.width() + 12
        lh = self._label_rect.height() + 6
        lx = r + 6
        ly = -lh / 2
        bg = QtGui.QColor(self._color)
        bg.setAlpha(225)
        painter.setBrush(bg)
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawRoundedRect(QtCore.QRectF(lx, ly, lw, lh), 4, 4)
        painter.setPen(QtGui.QPen(QtGui.QColor("white")))
        painter.drawText(QtCore.QRectF(lx, ly, lw, lh),
                         QtCore.Qt.AlignCenter, self._label)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemScenePositionHasChanged:
            self.moved.emit(self, self.scenePos())
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self._hover = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hover = False
        self.update()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            self.right_clicked.emit(self)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_pos = self.pos()
            self.left_clicked.emit(self)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._drag_start_pos is not None:
            if self.pos() != self._drag_start_pos:
                self.drag_finished.emit(self, self._drag_start_pos)
            self._drag_start_pos = None
        super().mouseReleaseEvent(event)


class ImageViewer(QtWidgets.QGraphicsView):
    """Scroll-zoom + drag-pan canvas. Emits image_clicked for empty clicks."""

    image_clicked = QtCore.pyqtSignal(QtCore.QPointF)
    zoom_changed = QtCore.pyqtSignal(float)
    cycle_attr = QtCore.pyqtSignal(int)  # -1 = prev attribute, +1 = next

    def __init__(self):
        super().__init__()
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QtGui.QPainter.Antialiasing
                            | QtGui.QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setBackgroundBrush(QtGui.QBrush(QtGui.QColor("#1e1e23")))
        self.setMouseTracking(True)
        self._pixmap_item: Optional[QtWidgets.QGraphicsPixmapItem] = None
        self._panning = False
        self._pan_start = QtCore.QPoint()
        self._space_held = False

    # public API ---------------------------------------------------------- #
    def annotation_scene(self) -> QtWidgets.QGraphicsScene:
        return self._scene

    def has_image(self) -> bool:
        return self._pixmap_item is not None

    def image_rect(self) -> QtCore.QRectF:
        return self._pixmap_item.boundingRect() if self._pixmap_item else QtCore.QRectF()

    def clear_scene(self):
        self._scene.clear()
        self._pixmap_item = None

    def set_image(self, path: str) -> bool:
        self.clear_scene()
        reader = QtGui.QImageReader(path)
        reader.setAutoTransform(True)
        img = reader.read()
        if img.isNull():
            return False
        pixmap = QtGui.QPixmap.fromImage(img)
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setZValue(-1)
        self.setSceneRect(QtCore.QRectF(pixmap.rect()))
        self.fit_view()
        return True

    def fit_view(self):
        if not self._pixmap_item:
            return
        self.resetTransform()
        self.fitInView(self._pixmap_item, QtCore.Qt.KeepAspectRatio)
        self.zoom_changed.emit(self._current_scale())

    # internal ------------------------------------------------------------ #
    def _current_scale(self) -> float:
        return self.transform().m11()

    def wheelEvent(self, event: QtGui.QWheelEvent):
        if not self._pixmap_item:
            return
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        new_scale = self._current_scale() * factor
        if new_scale < 0.02 or new_scale > 80:
            return
        self.scale(factor, factor)
        self.zoom_changed.emit(self._current_scale())

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        pan_mod = bool(event.modifiers() & QtCore.Qt.ControlModifier) or self._space_held
        if event.button() == QtCore.Qt.MiddleButton or (
            event.button() == QtCore.Qt.LeftButton and pan_mod
        ):
            self._panning = True
            self._pan_start = event.pos()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.LeftButton and self._pixmap_item:
            scene_pos = self.mapToScene(event.pos())
            item = self.scene().itemAt(scene_pos, self.transform())
            if item is None or item is self._pixmap_item:
                rect = self._pixmap_item.boundingRect()
                if rect.contains(scene_pos):
                    self.image_clicked.emit(scene_pos)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if self._panning:
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - delta.x())
            v.setValue(v.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if self._panning and event.button() in (QtCore.Qt.MiddleButton, QtCore.Qt.LeftButton):
            self._panning = False
            self.viewport().setCursor(
                QtCore.Qt.OpenHandCursor if self._space_held else QtCore.Qt.ArrowCursor
            )
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = True
            self.viewport().setCursor(QtCore.Qt.OpenHandCursor)
            return
        if event.key() in (QtCore.Qt.Key_Plus, QtCore.Qt.Key_Equal):
            self.scale(1.2, 1.2); self.zoom_changed.emit(self._current_scale()); return
        if event.key() == QtCore.Qt.Key_Minus:
            self.scale(1/1.2, 1/1.2); self.zoom_changed.emit(self._current_scale()); return
        if event.key() == QtCore.Qt.Key_1:
            self.cycle_attr.emit(-1); return
        if event.key() == QtCore.Qt.Key_2:
            self.cycle_attr.emit(1); return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QtGui.QKeyEvent):
        if event.key() == QtCore.Qt.Key_Space and not event.isAutoRepeat():
            self._space_held = False
            if not self._panning:
                self.viewport().setCursor(QtCore.Qt.ArrowCursor)
            return
        super().keyReleaseEvent(event)


# --------------------------- main window ---------------------------------- #

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Data Annotator")
        self.resize(1600, 950)

        # state
        self.root_dir: Optional[Path] = None
        self.images: list[tuple[str, Path]] = []   # (rel_key, abs_path)
        self.current_index: int = -1
        self.annotations: dict = {}
        self.schema: dict[str, list[str]] = {k: list(v) for k, v in DEFAULT_SCHEMA.items()}
        self.prompt_overrides: dict[tuple[str, str], str] = {}
        self.markers: list[PointMarker] = []
        self._dirty = False
        self._suppress_marker_signals = False
        self._suppress_prompt_edit = False
        self._undo_stack: list[tuple] = []
        self._clipboard: dict = {}
        self._hidden_entries: set[str] = set()  # entry_keys hidden on current image
        self._labels_hidden = False  # F1: hide marker text labels on the canvas
        self._annotations_path: Optional[Path] = None  # remembered save target

        self._build_ui()
        self._build_actions()
        self._refresh_class_combo()
        self._update_status()

    # ------------------------------ UI ----------------------------------- #
    def _build_ui(self):
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #2a2a30; color: #eee; }
            QGroupBox {
                border: 1px solid #44444c; border-radius: 6px;
                margin-top: 14px; padding: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 6px;
                background: #2a2a30; font-weight: bold;
            }
            QLabel#legendName {
                font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
                font-weight: normal; color: #eee;
            }
            QPushButton {
                background: #3a3a44; border: 1px solid #4a4a55;
                border-radius: 4px; padding: 6px 10px; min-height: 22px;
            }
            QPushButton:hover { background: #4a4a55; }
            QPushButton:pressed { background: #5a5a65; }
            QPushButton:disabled { color: #777; background: #2f2f36; }
            QListWidget, QComboBox, QLineEdit {
                background: #1f1f24; border: 1px solid #44444c;
                border-radius: 4px; padding: 4px;
                selection-background-color: #3b82f6;
            }
            QListWidget::item { padding: 4px 6px; }
            QListWidget::item:selected { background: #3b82f6; }
            QToolBar { background: #232328; spacing: 4px; padding: 4px; border: 0; }
            QStatusBar { background: #232328; }
            QLabel#hint { color: #aaa; font-size: 11px; }
            QLabel#legendSwatch { border-radius: 2px; }
        """)

        # toolbar
        tb = QtWidgets.QToolBar("Main")
        tb.setIconSize(QtCore.QSize(18, 18))
        tb.setMovable(False)
        self.addToolBar(tb)
        self._tb = tb

        # central splitter
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QHBoxLayout(central)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter)

        # left: image list ------------------------------------------------- #
        left = QtWidgets.QWidget()
        left_l = QtWidgets.QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        title = QtWidgets.QLabel("Images")
        title.setStyleSheet("font-weight:bold;padding:4px;")
        left_l.addWidget(title)
        self.image_filter = QtWidgets.QLineEdit()
        self.image_filter.setPlaceholderText("Filter…")
        self.image_filter.textChanged.connect(self._apply_image_filter)
        left_l.addWidget(self.image_filter)
        self.image_list = QtWidgets.QListWidget()
        self.image_list.currentRowChanged.connect(self._on_image_row_changed)
        left_l.addWidget(self.image_list, 1)
        self.image_count_label = QtWidgets.QLabel("0 images")
        self.image_count_label.setObjectName("hint")
        left_l.addWidget(self.image_count_label)
        splitter.addWidget(left)

        # center: viewer --------------------------------------------------- #
        self.viewer = ImageViewer()
        self.viewer.image_clicked.connect(self._on_canvas_click)
        self.viewer.zoom_changed.connect(self._on_zoom_changed)
        self.viewer.cycle_attr.connect(self._cycle_attribute)
        splitter.addWidget(self.viewer)

        # right: panels ---------------------------------------------------- #
        right = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        right_l.setSpacing(8)

        # class/attribute selector
        sel_box = QtWidgets.QGroupBox("Active label")
        sel_l = QtWidgets.QGridLayout(sel_box)
        sel_l.addWidget(QtWidgets.QLabel("Class"), 0, 0)
        self.class_combo = QtWidgets.QComboBox()
        self.class_combo.setEditable(True)
        self.class_combo.lineEdit().setPlaceholderText("Select or type a class")
        self.class_combo.currentTextChanged.connect(self._on_class_changed)
        sel_l.addWidget(self.class_combo, 0, 1)
        sel_l.addWidget(QtWidgets.QLabel("Attribute"), 1, 0)
        self.attr_combo = QtWidgets.QComboBox()
        self.attr_combo.setEditable(True)
        self.attr_combo.lineEdit().setPlaceholderText("Select or type an attribute")
        self.attr_combo.currentTextChanged.connect(self._on_attr_changed)
        sel_l.addWidget(self.attr_combo, 1, 1)
        self.color_swatch = QtWidgets.QLabel()
        self.color_swatch.setFixedHeight(8)
        self.color_swatch.setStyleSheet("background:#22c55e;border-radius:3px;")
        sel_l.addWidget(self.color_swatch, 2, 0, 1, 2)
        sel_l.addWidget(QtWidgets.QLabel("Prompt"), 3, 0)
        prompt_row = QtWidgets.QHBoxLayout()
        prompt_row.setContentsMargins(0, 0, 0, 0)
        self.prompt_edit = QtWidgets.QLineEdit()
        self.prompt_edit.setPlaceholderText("e.g. plastic part with bent lead")
        self.prompt_edit.setStyleSheet(
            "background:#1f1f24;border:1px solid #44444c;border-radius:4px;"
            "padding:6px 8px;font-weight:bold;font-size:13px;color:#ffffff;"
        )
        self.prompt_edit.textEdited.connect(self._on_prompt_edited)
        prompt_row.addWidget(self.prompt_edit, 1)
        self.prompt_reset_btn = QtWidgets.QPushButton("↺")
        self.prompt_reset_btn.setToolTip("Reset to '{attribute} {class}'")
        self.prompt_reset_btn.setFixedWidth(32)
        self.prompt_reset_btn.clicked.connect(self._reset_prompt)
        prompt_row.addWidget(self.prompt_reset_btn)
        prompt_wrap = QtWidgets.QWidget()
        prompt_wrap.setLayout(prompt_row)
        sel_l.addWidget(prompt_wrap, 3, 1)
        hint = QtWidgets.QLabel(
            "Left-click image to add a point.\n"
            "Right-click a point (or select + Delete) to remove.\n"
            "Drag a point to move it.\n"
            "Scroll = zoom · Middle-drag or Ctrl+drag or Space+drag = pan.\n"
            "1 / 2 = prev / next attribute of current class.\n"
            "F1 = hide / show marker labels · F2 = hide / show all annotations.\n"
            "D = prev image · F = next image · Ctrl+S = save."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        sel_l.addWidget(hint, 4, 0, 1, 2)
        right_l.addWidget(sel_box)

        # annotations for current image
        ann_box = QtWidgets.QGroupBox("Annotations on this image")
        ann_l = QtWidgets.QVBoxLayout(ann_box)
        self.entry_list = QtWidgets.QListWidget()
        self.entry_list.itemSelectionChanged.connect(self._on_entry_selection)
        self.entry_list.itemChanged.connect(self._on_entry_check_changed)
        ann_l.addWidget(self.entry_list, 1)
        vis_btns = QtWidgets.QHBoxLayout()
        self.btn_show_all = QtWidgets.QPushButton("Show all")
        self.btn_show_all.clicked.connect(lambda: self._set_all_entries_visible(True))
        vis_btns.addWidget(self.btn_show_all)
        self.btn_hide_all = QtWidgets.QPushButton("Hide all")
        self.btn_hide_all.clicked.connect(lambda: self._set_all_entries_visible(False))
        vis_btns.addWidget(self.btn_hide_all)
        ann_l.addLayout(vis_btns)
        btns = QtWidgets.QHBoxLayout()
        self.btn_remove_entry = QtWidgets.QPushButton("Remove entry")
        self.btn_remove_entry.clicked.connect(self._remove_selected_entry)
        btns.addWidget(self.btn_remove_entry)
        self.btn_clear_image = QtWidgets.QPushButton("Clear image")
        self.btn_clear_image.clicked.connect(self._clear_current_image)
        btns.addWidget(self.btn_clear_image)
        ann_l.addLayout(btns)
        right_l.addWidget(ann_box, 1)

        # legend (one row per class · attribute, since color now depends on both)
        leg_box = QtWidgets.QGroupBox("Color legend (class · attribute)")
        leg_outer = QtWidgets.QVBoxLayout(leg_box)
        leg_outer.setContentsMargins(0, 0, 0, 0)
        leg_scroll = QtWidgets.QScrollArea()
        leg_scroll.setWidgetResizable(True)
        leg_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        leg_scroll.setMaximumHeight(220)
        leg_inner = QtWidgets.QWidget()
        self.legend_layout = QtWidgets.QVBoxLayout(leg_inner)
        self.legend_layout.setSpacing(4)
        self.legend_layout.setContentsMargins(2, 2, 2, 2)
        leg_scroll.setWidget(leg_inner)
        leg_outer.addWidget(leg_scroll)
        self._build_legend()
        right_l.addWidget(leg_box)

        splitter.addWidget(right)
        splitter.setSizes([280, 940, 360])

        # status bar
        self.status = self.statusBar()
        self.zoom_lbl = QtWidgets.QLabel("zoom: 100%")
        self.status.addPermanentWidget(self.zoom_lbl)

    def _build_legend(self):
        pairs = []
        for cls in sorted(self.schema):
            for a in self.schema[cls]:
                pairs.append((cls, a))
        for cls, a in pairs:
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            sw = QtWidgets.QLabel()
            sw.setObjectName("legendSwatch")
            sw.setFixedSize(14, 14)
            sw.setStyleSheet(f"background:{color_for(cls, a)};border-radius:3px;")
            row.addWidget(sw, 0, QtCore.Qt.AlignVCenter)
            lbl = QtWidgets.QLabel(f"{a} · {cls}")
            lbl.setObjectName("legendName")
            lbl.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
            lbl.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                              QtWidgets.QSizePolicy.Preferred)
            row.addWidget(lbl, 1, QtCore.Qt.AlignVCenter)
            container = QtWidgets.QWidget()
            container.setLayout(row)
            # Force a row taller than the font's natural height so the text
            # never gets clipped to the swatch's 14 px at fractional DPI scales.
            container.setMinimumHeight(22)
            self.legend_layout.addWidget(container)
        self.legend_layout.addStretch()

    def _rebuild_legend(self):
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._build_legend()

    def _build_actions(self):
        act_open_folder = QtWidgets.QAction("Open Folder…", self)
        act_open_folder.setShortcut("Ctrl+O")
        act_open_folder.triggered.connect(self.open_folder)
        self._tb.addAction(act_open_folder)

        act_open_image = QtWidgets.QAction("Open Image…", self)
        act_open_image.setShortcut("Ctrl+Shift+O")
        act_open_image.triggered.connect(self.open_image)
        self._tb.addAction(act_open_image)

        self._tb.addSeparator()

        act_load = QtWidgets.QAction("Load Annotations…", self)
        act_load.setShortcut("Ctrl+L")
        act_load.triggered.connect(self.load_annotations)
        self._tb.addAction(act_load)

        act_save = QtWidgets.QAction("Save", self)
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self.save_annotations)
        self._tb.addAction(act_save)

        act_save_as = QtWidgets.QAction("Save As…", self)
        act_save_as.setShortcut("Ctrl+Shift+S")
        act_save_as.triggered.connect(lambda: self.save_annotations(as_=True))
        self._tb.addAction(act_save_as)

        self._tb.addSeparator()

        act_prev = QtWidgets.QAction("◀ Prev", self)
        act_prev.setShortcut(QtCore.Qt.Key_D)
        act_prev.triggered.connect(self.prev_image)
        self._tb.addAction(act_prev)

        act_next = QtWidgets.QAction("Next ▶", self)
        act_next.setShortcut(QtCore.Qt.Key_F)
        act_next.triggered.connect(self.next_image)
        self._tb.addAction(act_next)

        act_fit = QtWidgets.QAction("Fit", self)
        act_fit.triggered.connect(self.viewer.fit_view)
        self._tb.addAction(act_fit)

        # delete shortcut on canvas / list
        del_sc = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Delete), self)
        del_sc.activated.connect(self._delete_selected_markers)

        # F1 = hide / show the marker text labels on the canvas
        labels_sc = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_F1), self)
        labels_sc.activated.connect(self._toggle_labels)

        # F2 = hide / show all annotations on the current image
        hide_sc = QtWidgets.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_F2), self)
        hide_sc.activated.connect(self._toggle_hide_all)

        undo_sc = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+Z"), self)
        undo_sc.activated.connect(self._undo)

        copy_sc = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+C"), self)
        copy_sc.activated.connect(self._copy_selected)

        paste_sc = QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+V"), self)
        paste_sc.activated.connect(self._paste)

    # --------------------------- file ops -------------------------------- #
    def _gather_images(self, root: Path) -> list[tuple[str, Path]]:
        result: list[tuple[str, Path]] = []
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                rel = p.relative_to(root).as_posix()
                result.append((rel, p))
        return result

    def open_folder(self):
        if not self._maybe_prompt_save():
            return
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Open dataset folder")
        if not d:
            return
        root = Path(d)
        imgs = self._gather_images(root)
        if not imgs:
            QtWidgets.QMessageBox.warning(self, "No images",
                                          "No supported images were found in that folder.")
            return
        self.root_dir = root
        self.images = imgs
        self.annotations = {}
        self._annotations_path = None
        self._undo_stack.clear()
        self._refresh_image_list()
        self.image_list.setCurrentRow(0)
        self.status.showMessage(f"Loaded {len(imgs)} image(s) from {root}", 4000)

    def open_image(self):
        if not self._maybe_prompt_save():
            return
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTS))
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open image", "", f"Images ({exts})"
        )
        if not f:
            return
        p = Path(f)
        self.root_dir = p.parent
        self.images = [(p.name, p)]
        self.annotations = {}
        self._annotations_path = None
        self._undo_stack.clear()
        self._refresh_image_list()
        self.image_list.setCurrentRow(0)

    def load_annotations(self):
        if not self.root_dir:
            QtWidgets.QMessageBox.information(
                self, "Open images first",
                "Open a folder or image before loading annotations."
            )
            return
        f, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load annotations.json", str(self.root_dir), "JSON (*.json)"
        )
        if not f:
            return
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e))
            return
        if not isinstance(data, dict):
            QtWidgets.QMessageBox.critical(self, "Load failed",
                                           "Annotations file must be an object keyed by image path.")
            return
        # normalize entries
        loaded_schema: dict[str, list[str]] = {}
        normalized: dict = {}
        for img_key, entries in data.items():
            if not isinstance(entries, dict):
                continue
            cleaned: dict = {}
            for ek, ent in entries.items():
                if not isinstance(ent, dict):
                    continue
                cls = ent.get("class", "")
                attr = ent.get("attribute", "")
                pts = ent.get("points", [])
                typ = ent.get("type", "inspection")
                if not cls or not attr:
                    continue
                cleaned_points = [
                    [float(x), float(y)]
                    for pt in pts if isinstance(pt, (list, tuple)) and len(pt) >= 2
                    for x, y in [pt[:2]]
                ]
                # If the JSON key isn't the default '{attr} {class}',
                # treat it as a user-defined prompt override.
                default_key = entry_key(cls, attr)
                if ek != default_key:
                    self.prompt_overrides[(cls, attr)] = ek
                cleaned[ek] = {
                    "class": cls,
                    "attribute": attr,
                    "points": cleaned_points,
                    "type": typ,
                }
                loaded_schema.setdefault(cls, [])
                if attr not in loaded_schema[cls]:
                    loaded_schema[cls].append(attr)
            if cleaned:
                normalized[img_key] = cleaned
        self.annotations = normalized
        self.schema = loaded_schema
        self._annotations_path = Path(f)  # save back here by default
        self._undo_stack.clear()
        self._refresh_class_combo()
        self._rebuild_legend()
        self._refresh_image_list()
        if self.current_index >= 0:
            self._reload_markers()
            self._refresh_entry_list()
        self._dirty = False
        self.status.showMessage(f"Loaded {len(self.annotations)} annotated image(s) from {f}", 4000)

    def save_annotations(self, as_: bool = False):
        if not self.root_dir:
            QtWidgets.QMessageBox.information(self, "Nothing to save",
                                              "Open a folder or image first.")
            return
        # Plain Save reuses the remembered target (the file loaded via Load
        # Annotations, or the one last chosen in Save As). Only prompt when
        # there is no remembered target yet, or for an explicit Save As.
        if as_ or self._annotations_path is None:
            default = self._annotations_path or (self.root_dir / "annotations.json")
            f, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save annotations.json", str(default), "JSON (*.json)"
            )
            if not f:
                return
            target = f
            self._annotations_path = Path(target)
        else:
            target = str(self._annotations_path)
        cleaned = {k: v for k, v in self.annotations.items() if v}
        try:
            with open(target, "w", encoding="utf-8") as fp:
                json.dump(cleaned, fp, indent=4, ensure_ascii=False)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))
            return
        self._dirty = False
        self.status.showMessage(
            f"저장되었습니다  ·  {len(cleaned)} annotated image(s) → {target}", 4000)
        QtWidgets.QMessageBox.information(
            self, "저장 완료",
            f"저장되었습니다.\n\n{len(cleaned)}개 이미지의 annotation\n→ {target}")

    def _maybe_prompt_save(self) -> bool:
        if not self._dirty:
            return True
        r = QtWidgets.QMessageBox.question(
            self, "Unsaved changes",
            "You have unsaved annotations. Save before continuing?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel,
        )
        if r == QtWidgets.QMessageBox.Save:
            self.save_annotations()
            return not self._dirty
        if r == QtWidgets.QMessageBox.Cancel:
            return False
        return True

    def closeEvent(self, event: QtGui.QCloseEvent):
        if self._maybe_prompt_save():
            event.accept()
        else:
            event.ignore()

    # --------------------------- list / nav ------------------------------ #
    def _refresh_image_list(self):
        self.image_list.blockSignals(True)
        self.image_list.clear()
        for rel, _abs in self.images:
            n = sum(len(e["points"]) for e in self.annotations.get(rel, {}).values())
            label = f"{rel}    [{n}]" if n else rel
            it = QtWidgets.QListWidgetItem(label)
            it.setData(QtCore.Qt.UserRole, rel)
            if n:
                it.setForeground(QtGui.QBrush(QtGui.QColor("#a7f3d0")))
            self.image_list.addItem(it)
        self.image_list.blockSignals(False)
        self.image_count_label.setText(f"{len(self.images)} image(s)")
        self._apply_image_filter(self.image_filter.text())

    def _apply_image_filter(self, text: str):
        text = text.strip().lower()
        for i in range(self.image_list.count()):
            it = self.image_list.item(i)
            visible = text in it.text().lower() if text else True
            it.setHidden(not visible)

    def _on_image_row_changed(self, row: int):
        if row < 0 or row >= self.image_list.count():
            return
        it = self.image_list.item(row)
        rel = it.data(QtCore.Qt.UserRole)
        # find absolute path by rel
        try:
            idx = next(i for i, (r, _) in enumerate(self.images) if r == rel)
        except StopIteration:
            return
        self.current_index = idx
        rel, abs_path = self.images[idx]
        ok = self.viewer.set_image(str(abs_path))
        if not ok:
            QtWidgets.QMessageBox.warning(self, "Cannot load image",
                                          f"Failed to read {abs_path}")
            return
        self.markers = []
        self._hidden_entries.clear()
        self._reload_markers()
        self._refresh_entry_list()
        self._update_status()

    def next_image(self):
        if not self.images:
            return
        n = self.image_list.count()
        if n == 0: return
        cur = self.image_list.currentRow()
        for off in range(1, n + 1):
            i = (cur + off) % n
            if not self.image_list.item(i).isHidden():
                self.image_list.setCurrentRow(i)
                return

    def prev_image(self):
        if not self.images:
            return
        n = self.image_list.count()
        if n == 0: return
        cur = self.image_list.currentRow()
        for off in range(1, n + 1):
            i = (cur - off) % n
            if not self.image_list.item(i).isHidden():
                self.image_list.setCurrentRow(i)
                return

    # --------------------------- selector -------------------------------- #
    def _refresh_class_combo(self):
        cur = self.class_combo.currentText()
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        self.class_combo.addItems(sorted(self.schema.keys()))
        if cur and cur in self.schema:
            self.class_combo.setCurrentText(cur)
        else:
            if self.class_combo.count():
                self.class_combo.setCurrentIndex(0)
        self.class_combo.blockSignals(False)
        self._refresh_attr_combo()

    def _refresh_attr_combo(self):
        cls = self.class_combo.currentText().strip()
        cur = self.attr_combo.currentText()
        self.attr_combo.blockSignals(True)
        self.attr_combo.clear()
        attrs = self.schema.get(cls, [])
        self.attr_combo.addItems(attrs)
        if cur and cur in attrs:
            self.attr_combo.setCurrentText(cur)
        elif attrs:
            self.attr_combo.setCurrentIndex(0)
        self.attr_combo.blockSignals(False)
        self._update_color_swatch()

    def _on_class_changed(self, _text: str):
        self._refresh_attr_combo()
        self._update_color_swatch()

    def _on_attr_changed(self, _text: str):
        self._update_color_swatch()

    def _cycle_attribute(self, direction: int):
        """Step the active attribute backward (1 key) / forward (2 key)
        through the current class's attribute list, wrapping around."""
        cls = self.class_combo.currentText().strip()
        attrs = self.schema.get(cls, [])
        if not attrs:
            return
        cur = self.attr_combo.currentText().strip()
        try:
            i = attrs.index(cur)
        except ValueError:
            i = 0
            direction = 0
        i = (i + direction) % len(attrs)
        self.attr_combo.setCurrentText(attrs[i])
        self.status.showMessage(f"Attribute: {attrs[i]}  ({cls})", 1500)

    def _prompt_for(self, cls: str, attr: str) -> str:
        """Return the prompt/key for a (class, attribute) pair.

        Honors any user override set via the Prompt edit; otherwise falls back
        to the default ``"{attribute} {class}"`` form.
        """
        if not cls or not attr:
            return ""
        return self.prompt_overrides.get((cls, attr), entry_key(cls, attr))

    def _update_color_swatch(self):
        cls = self.class_combo.currentText().strip()
        a = self.attr_combo.currentText().strip()
        c = color_for(cls, a) if a else "#666"
        self.color_swatch.setStyleSheet(f"background:{c};border-radius:3px;")
        # update prompt edit without firing textEdited
        self._suppress_prompt_edit = True
        if cls and a:
            self.prompt_edit.setText(self._prompt_for(cls, a))
            self.prompt_edit.setStyleSheet(
                f"background:{c};border:1px solid #44444c;border-radius:4px;"
                "padding:6px 8px;font-weight:bold;font-size:13px;color:#ffffff;"
            )
            self.prompt_edit.setEnabled(True)
            self.prompt_reset_btn.setEnabled(True)
        else:
            self.prompt_edit.setText("")
            self.prompt_edit.setStyleSheet(
                "background:#1f1f24;border:1px solid #44444c;border-radius:4px;"
                "padding:6px 8px;font-weight:bold;font-size:13px;color:#888;"
            )
            self.prompt_edit.setEnabled(False)
            self.prompt_reset_btn.setEnabled(False)
        self._suppress_prompt_edit = False

    def _on_prompt_edited(self, text: str):
        if self._suppress_prompt_edit:
            return
        cls = self.class_combo.currentText().strip()
        attr = self.attr_combo.currentText().strip()
        if not cls or not attr:
            return
        new_prompt = text.strip()
        default = entry_key(cls, attr)
        old_prompt = self._prompt_for(cls, attr)
        if not new_prompt or new_prompt == default:
            self.prompt_overrides.pop((cls, attr), None)
            effective = default
        else:
            self.prompt_overrides[(cls, attr)] = new_prompt
            effective = new_prompt
        if effective != old_prompt:
            self._rename_entries_for(cls, attr, old_prompt, effective)

    def _reset_prompt(self):
        cls = self.class_combo.currentText().strip()
        attr = self.attr_combo.currentText().strip()
        if not cls or not attr:
            return
        old_prompt = self._prompt_for(cls, attr)
        self.prompt_overrides.pop((cls, attr), None)
        default = entry_key(cls, attr)
        self._suppress_prompt_edit = True
        self.prompt_edit.setText(default)
        self._suppress_prompt_edit = False
        if old_prompt != default:
            self._rename_entries_for(cls, attr, old_prompt, default)

    def _rename_entries_for(self, cls: str, attr: str,
                            old_key: str, new_key: str):
        """Rename every annotation entry on (cls, attr) from old_key to new_key.

        Updates the in-memory annotations dict, the marker labels, and the
        entry list.
        """
        if old_key == new_key:
            return
        changed_any = False
        for rel, entries in self.annotations.items():
            match = None
            for ek, ent in entries.items():
                if ent["class"] == cls and ent["attribute"] == attr:
                    match = ek
                    break
            if match is None:
                continue
            ent = entries.pop(match)
            # merge if new_key already exists (shouldn't normally happen)
            if new_key in entries:
                entries[new_key]["points"].extend(ent["points"])
            else:
                entries[new_key] = ent
            changed_any = True
        # update current image's markers + lists
        for m in self.markers:
            if m.entry_key == old_key:
                m.entry_key = new_key
                m.set_label(new_key)
        if changed_any:
            self._dirty = True
        self._refresh_entry_list()

    # --------------------------- undo / copy-paste ------------------------ #
    def _push_undo_snapshot(self, rel: str):
        entries = self.annotations.get(rel, {})
        self._undo_stack.append(("snapshot", rel, copy.deepcopy(entries)))
        if len(self._undo_stack) > 200:
            self._undo_stack = self._undo_stack[-200:]

    def _on_marker_drag_finished(self, marker: PointMarker, old_pos: QtCore.QPointF):
        rel = self.current_rel()
        if not rel:
            return
        entry = self.annotations.get(rel, {}).get(marker.entry_key)
        if not entry or marker.point_index >= len(entry["points"]):
            return
        old_x, old_y = round(old_pos.x(), 2), round(old_pos.y(), 2)
        self._undo_stack.append(("move", rel, marker.entry_key,
                                 marker.point_index, old_x, old_y))
        if len(self._undo_stack) > 200:
            self._undo_stack = self._undo_stack[-200:]

    def _undo(self):
        if not self._undo_stack:
            return
        record = self._undo_stack.pop()
        current_rel = self.current_rel()

        if record[0] == "snapshot":
            _, rel, snapshot = record
            if snapshot:
                self.annotations[rel] = snapshot
            else:
                self.annotations.pop(rel, None)
            if rel == current_rel:
                self._reload_markers()
                self._refresh_entry_list()
            self._refresh_image_list_row(rel)

        elif record[0] == "move":
            _, rel, ek, idx, old_x, old_y = record
            entries = self.annotations.get(rel, {})
            entry = entries.get(ek)
            if entry and idx < len(entry["points"]):
                entry["points"][idx] = [old_x, old_y]
            if rel == current_rel:
                for m in self.markers:
                    if m.entry_key == ek and m.point_index == idx:
                        self._suppress_marker_signals = True
                        m.setPos(old_x, old_y)
                        self._suppress_marker_signals = False
                        break

        self._dirty = True

    def _on_marker_left_clicked(self, marker: PointMarker):
        rel = self.current_rel()
        if not rel:
            return
        entry = self.annotations.get(rel, {}).get(marker.entry_key)
        if not entry:
            return
        self.class_combo.setCurrentText(entry["class"])
        self.attr_combo.setCurrentText(entry["attribute"])

    def _copy_selected(self):
        cls = self.class_combo.currentText().strip()
        attr = self.attr_combo.currentText().strip()
        if cls and attr:
            self._clipboard = {"cls": cls, "attr": attr}
            self.status.showMessage(f"Copied label: {attr} {cls}", 2000)

    def _paste(self):
        if not self._clipboard:
            return
        self.class_combo.setCurrentText(self._clipboard["cls"])
        self.attr_combo.setCurrentText(self._clipboard["attr"])
        self.status.showMessage(
            f"Pasted label: {self._clipboard['attr']} {self._clipboard['cls']}", 2000)

    # --------------------------- annotation ops -------------------------- #
    def current_rel(self) -> Optional[str]:
        if self.current_index < 0 or self.current_index >= len(self.images):
            return None
        return self.images[self.current_index][0]

    def _on_canvas_click(self, pos: QtCore.QPointF):
        rel = self.current_rel()
        if not rel:
            return
        cls = self.class_combo.currentText().strip()
        attr = self.attr_combo.currentText().strip()
        if not cls or not attr:
            QtWidgets.QMessageBox.information(
                self, "Pick a label",
                "Select a class and attribute before placing a point."
            )
            return
        # register class/attribute into schema for future use
        self.schema.setdefault(cls, [])
        if attr not in self.schema[cls]:
            self.schema[cls].append(attr)
            self._refresh_class_combo()
            self.class_combo.setCurrentText(cls)
            self.attr_combo.setCurrentText(attr)

        self._push_undo_snapshot(rel)
        ek = self._prompt_for(cls, attr)
        # If this entry was hidden (e.g. after "hide all"), reveal it so the
        # point the user is placing is actually visible.
        self._hidden_entries.discard(ek)
        img_entries = self.annotations.setdefault(rel, {})
        # locate any existing entry for this (cls, attr), regardless of key
        existing_key = None
        for k, ent in img_entries.items():
            if ent["class"] == cls and ent["attribute"] == attr:
                existing_key = k; break
        if existing_key is not None and existing_key != ek:
            img_entries[ek] = img_entries.pop(existing_key)
            for m in self.markers:
                if m.entry_key == existing_key:
                    m.entry_key = ek
                    m.set_label(ek)
        entry = img_entries.get(ek)
        if entry is None:
            entry = {"class": cls, "attribute": attr, "points": [], "type": "inspection"}
            img_entries[ek] = entry
        entry["points"].append([round(pos.x(), 2), round(pos.y(), 2)])
        idx = len(entry["points"]) - 1
        self._add_marker(ek, idx, pos.x(), pos.y(), cls, attr)
        self._apply_entry_visibility()  # reveal any existing markers of this entry
        self._dirty = True
        self._refresh_entry_list()
        self._refresh_image_list_row(rel)

    def _add_marker(self, ek: str, point_index: int, x: float, y: float,
                    cls: str, attr: str):
        m = PointMarker(color_for(cls, attr), ek)
        m.entry_key = ek
        m.point_index = point_index
        m.setPos(x, y)
        m.moved.connect(self._on_marker_moved)
        m.right_clicked.connect(self._on_marker_right_clicked)
        m.left_clicked.connect(self._on_marker_left_clicked)
        m.drag_finished.connect(self._on_marker_drag_finished)
        m.setToolTip(f"{attr} · {cls}")
        m.set_label_visible(not self._labels_hidden)
        if ek in self._hidden_entries:
            m.setVisible(False)
        self.viewer.annotation_scene().addItem(m)
        self.markers.append(m)

    def _on_marker_moved(self, marker: PointMarker, scene_pos: QtCore.QPointF):
        if self._suppress_marker_signals:
            return
        rel = self.current_rel()
        if not rel:
            return
        entry = self.annotations.get(rel, {}).get(marker.entry_key)
        if not entry:
            return
        # clamp to image
        rect = self.viewer.image_rect()
        x = max(0.0, min(rect.width(),  scene_pos.x()))
        y = max(0.0, min(rect.height(), scene_pos.y()))
        if (x != scene_pos.x()) or (y != scene_pos.y()):
            self._suppress_marker_signals = True
            marker.setPos(x, y)
            self._suppress_marker_signals = False
        if 0 <= marker.point_index < len(entry["points"]):
            entry["points"][marker.point_index] = [round(x, 2), round(y, 2)]
            self._dirty = True

    def _on_marker_right_clicked(self, marker: PointMarker):
        self._remove_marker(marker)

    def _delete_selected_markers(self):
        selected = [m for m in self.markers if m.isSelected()]
        if not selected and self.entry_list.hasFocus():
            self._remove_selected_entry()
            return
        if selected:
            rel = self.current_rel()
            if rel:
                self._push_undo_snapshot(rel)
            for m in selected:
                self._remove_marker(m, push_undo=False)

    def _remove_marker(self, marker: PointMarker, push_undo: bool = True):
        rel = self.current_rel()
        if not rel:
            return
        if push_undo:
            self._push_undo_snapshot(rel)
        entries = self.annotations.get(rel, {})
        entry = entries.get(marker.entry_key)
        if entry and 0 <= marker.point_index < len(entry["points"]):
            del entry["points"][marker.point_index]
            if not entry["points"]:
                entries.pop(marker.entry_key, None)
        self._dirty = True
        # remove marker and re-index remaining markers of same entry_key
        ek = marker.entry_key
        self.viewer.annotation_scene().removeItem(marker)
        self.markers.remove(marker)
        # recompute point_index for remaining markers of same entry
        same = [m for m in self.markers if m.entry_key == ek]
        same.sort(key=lambda m: m.point_index)
        for new_i, m in enumerate(same):
            m.point_index = new_i
        self._refresh_entry_list()
        self._refresh_image_list_row(rel)

    def _reload_markers(self):
        # remove existing markers
        for m in self.markers:
            self.viewer.annotation_scene().removeItem(m)
        self.markers = []
        rel = self.current_rel()
        if not rel:
            return
        entries = self.annotations.get(rel, {})
        for ek, ent in entries.items():
            cls = ent["class"]; attr = ent["attribute"]
            for i, (x, y) in enumerate(ent["points"]):
                self._add_marker(ek, i, float(x), float(y), cls, attr)

    def _refresh_entry_list(self):
        self.entry_list.blockSignals(True)
        self.entry_list.clear()
        rel = self.current_rel()
        if rel:
            entries = self.annotations.get(rel, {})
            for ek, ent in entries.items():
                n = len(ent["points"])
                it = QtWidgets.QListWidgetItem(f"{ek}  ({n})")
                it.setData(QtCore.Qt.UserRole, ek)
                color = QtGui.QColor(color_for(ent["class"], ent["attribute"]))
                pix = QtGui.QPixmap(12, 12); pix.fill(color)
                it.setIcon(QtGui.QIcon(pix))
                # checkbox toggles visibility of this entry's markers
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(
                    QtCore.Qt.Unchecked if ek in self._hidden_entries
                    else QtCore.Qt.Checked
                )
                self.entry_list.addItem(it)
        self.entry_list.blockSignals(False)

    def _on_entry_check_changed(self, item: QtWidgets.QListWidgetItem):
        ek = item.data(QtCore.Qt.UserRole)
        if ek is None:
            return
        if item.checkState() == QtCore.Qt.Checked:
            self._hidden_entries.discard(ek)
        else:
            self._hidden_entries.add(ek)
        self._apply_entry_visibility()

    def _apply_entry_visibility(self):
        for m in self.markers:
            m.setVisible(m.entry_key not in self._hidden_entries)

    def _set_all_entries_visible(self, visible: bool):
        rel = self.current_rel()
        if not rel:
            return
        if visible:
            self._hidden_entries.clear()
        else:
            self._hidden_entries = set(self.annotations.get(rel, {}).keys())
        self._apply_entry_visibility()
        self._refresh_entry_list()

    def _toggle_labels(self):
        """F1: hide / show the text label pills on every marker (dots stay)."""
        self._labels_hidden = not self._labels_hidden
        for m in self.markers:
            m.set_label_visible(not self._labels_hidden)
        self.status.showMessage(
            "Labels hidden" if self._labels_hidden else "Labels shown", 1500)

    def _toggle_hide_all(self):
        """F2: hide every annotation on the current image, or show
        them all again if anything is currently hidden. Non-destructive."""
        rel = self.current_rel()
        if not rel:
            return
        entries = self.annotations.get(rel, {})
        if not entries:
            return
        any_visible = any(ek not in self._hidden_entries for ek in entries)
        self._set_all_entries_visible(not any_visible)
        self.status.showMessage(
            "Hid all annotations on this image" if any_visible
            else "Showing all annotations on this image", 1500)

    def _on_entry_selection(self):
        items = self.entry_list.selectedItems()
        sel_keys = {it.data(QtCore.Qt.UserRole) for it in items}
        for m in self.markers:
            m.setSelected(m.entry_key in sel_keys)

    def _remove_selected_entry(self):
        items = self.entry_list.selectedItems()
        rel = self.current_rel()
        if not items or not rel:
            return
        self._push_undo_snapshot(rel)
        keys = [it.data(QtCore.Qt.UserRole) for it in items]
        for k in keys:
            self.annotations.get(rel, {}).pop(k, None)
        # drop markers
        for m in list(self.markers):
            if m.entry_key in keys:
                self.viewer.annotation_scene().removeItem(m)
                self.markers.remove(m)
        self._dirty = True
        self._refresh_entry_list()
        self._refresh_image_list_row(rel)

    def _clear_current_image(self):
        rel = self.current_rel()
        if not rel:
            return
        if rel not in self.annotations or not self.annotations[rel]:
            return
        r = QtWidgets.QMessageBox.question(
            self, "Clear annotations",
            f"Remove every annotation on {rel}?"
        )
        if r != QtWidgets.QMessageBox.Yes:
            return
        self._push_undo_snapshot(rel)
        self.annotations.pop(rel, None)
        for m in self.markers:
            self.viewer.annotation_scene().removeItem(m)
        self.markers = []
        self._hidden_entries.clear()
        self._dirty = True
        self._refresh_entry_list()
        self._refresh_image_list_row(rel)

    def _refresh_image_list_row(self, rel: str):
        for i in range(self.image_list.count()):
            it = self.image_list.item(i)
            if it.data(QtCore.Qt.UserRole) == rel:
                n = sum(len(e["points"]) for e in self.annotations.get(rel, {}).values())
                it.setText(f"{rel}    [{n}]" if n else rel)
                if n:
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#a7f3d0")))
                else:
                    it.setForeground(QtGui.QBrush(QtGui.QColor("#eeeeee")))
                break

    # --------------------------- status ---------------------------------- #
    def _on_zoom_changed(self, scale: float):
        self.zoom_lbl.setText(f"zoom: {int(scale * 100)}%")

    def _update_status(self):
        rel = self.current_rel()
        if rel:
            n = sum(len(e["points"]) for e in self.annotations.get(rel, {}).values())
            self.status.showMessage(f"{rel}  ·  {n} point(s) on this image")
        else:
            self.status.showMessage("Ready. Open a folder or image to begin.")


# -------------------------------- entry ----------------------------------- #

def main():
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    # On fractional Windows DPI (125/150 %) the default Round policy can clip
    # text rows to the nearest swatch-sized integer. PassThrough keeps the real
    # device scale and stops the legend labels from being cut in half.
    try:
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass  # Qt < 5.14
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("Data Annotator")
    icon_path = resource_path("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
