"""Single source of truth for STG Store Sync styling.

Every color, radius, spacing and font-size used by the UI lives here, derived
verbatim from ``DESIGN.md`` at the repo root — that file is authoritative. No
widget may carry its own hex or font-size; they set an ``objectName()`` and this
module's ``STYLESHEET`` styles them. The one sanctioned exception is
:meth:`store_sync.ui.main_window.MainWindow.set_status`, which passes a runtime
status color straight through.

Usage (see ``store_sync.gui.main``)::

    from store_sync.ui import theme
    theme.apply(app)          # installs STYLESHEET app-wide

``STYLESHEET`` is a ``string.Template`` so QSS braces (``{}``) stay literal while
``$token`` references are filled from :data:`TOKENS`.
"""
from __future__ import annotations

from string import Template

# -- DESIGN.md frontmatter, mirrored 1:1 ------------------------------------
# Keep this in lock-step with DESIGN.md; it is the canonical token source.
TOKENS: dict = {
    "colors": {
        "primary": "#1F2937",
        "secondary": "#6B7280",
        "tertiary": "#4F46E5",
        "neutral": "#F8F9FA",
        "surface": "#FFFFFF",
        "border-light": "#E5E7EB",
        "border-medium": "#D1D5DB",
        "border-hover": "#9CA3AF",
        "accent-deep": "#4338CA",
        "accent-darkest": "#3730A3",
        "accent-soft": "#E0E7FF",
        "accent-soft-text": "#3730A3",
        "gradient-start": "#667EEA",
        "gradient-end": "#764BA2",
        "on-primary": "#FFFFFF",
        "on-tertiary": "#FFFFFF",
        "success": "#10B981",
        "success-deep": "#059669",
        "danger": "#EF4444",
        "danger-deep": "#DC2626",
        "warning": "#F59E0B",
        "warning-deep": "#D97706",
        "info": "#6366F1",
        "teal": "#0D9488",
        "teal-deep": "#0F766E",
        "blue": "#2563EB",
        "blue-deep": "#1D4ED8",
        "green": "#16A34A",
        "green-deep": "#15803D",
        "display-value": "#111827",
        "console-bg": "#0F172A",
        "console-text": "#E2E8F0",
        # Values DESIGN.md uses inside component definitions but does not name
        # as standalone palette entries:
        "surface-hover": "#F3F4F6",
        "group-text": "#374151",
        "status-dot": "#86EFAC",
    },
    "typography": {
        "body": {"fontSize": "13px", "fontWeight": 400},
        "display": {"fontSize": "29px", "fontWeight": 700},
        "stat-value": {"fontSize": "24px", "fontWeight": 700},
        "title": {"fontSize": "18px", "fontWeight": 700},
        "heading": {"fontSize": "15px", "fontWeight": 600},
        "label": {"fontSize": "13px", "fontWeight": 600},
        "label-caps": {"fontSize": "12px", "fontWeight": 600, "letterSpacing": "0.02em"},
        "caption": {"fontSize": "10px", "fontWeight": 600, "letterSpacing": "0.02em"},
        "console": {"fontSize": "13px", "fontWeight": 400},
    },
    "rounded": {"sm": "4px", "md": "6px", "lg": "8px", "pill": "10px", "full": "9999px"},
    "spacing": {"xs": "4px", "sm": "8px", "md": "16px", "lg": "24px", "xl": "48px"},
}

_C = TOKENS["colors"]
_TY = TOKENS["typography"]
_R = TOKENS["rounded"]

# Flat substitution table for `string.Template` (ids: [a-z0-9_]).
_T: dict[str, str] = {
    "primary": _C["primary"],
    "secondary": _C["secondary"],
    "tertiary": _C["tertiary"],
    "neutral": _C["neutral"],
    "surface": _C["surface"],
    "border_light": _C["border-light"],
    "border_medium": _C["border-medium"],
    "border_hover": _C["border-hover"],
    "accent_deep": _C["accent-deep"],
    "accent_darkest": _C["accent-darkest"],
    "accent_soft": _C["accent-soft"],
    "accent_soft_text": _C["accent-soft-text"],
    "gradient_start": _C["gradient-start"],
    "gradient_end": _C["gradient-end"],
    "on_primary": _C["on-primary"],
    "success": _C["success"],
    "success_deep": _C["success-deep"],
    "danger": _C["danger"],
    "danger_deep": _C["danger-deep"],
    "warning": _C["warning"],
    "warning_deep": _C["warning-deep"],
    "display_value": _C["display-value"],
    "console_bg": _C["console-bg"],
    "console_text": _C["console-text"],
    "surface_hover": _C["surface-hover"],
    "group_text": _C["group-text"],
    "status_dot": _C["status-dot"],
    # Status / severity pill colors — DESIGN.md `components` entries (pastel bg,
    # saturated text); not part of the frontmatter palette, so named here.
    "status_ok_bg": "#ECFDF5",
    "status_ok_fg": "#047857",
    "status_warn_bg": "#FFF7ED",
    "status_warn_fg": "#C2410C",
    "status_fail_bg": "#FEF2F2",
    "status_fail_fg": "#B91C1C",
    "status_cancelled_bg": "#F3F4F6",
    "status_cancelled_fg": "#4B5563",
    "sev_critical_bg": "#FEF2F2",
    "sev_critical_fg": "#B91C1C",
    "sev_high_bg": "#FEF3C7",
    "sev_high_fg": "#B45309",
    "sev_medium_bg": "#FFF7ED",
    "sev_medium_fg": "#C2410C",
    "sev_low_bg": "#ECFEFF",
    "sev_low_fg": "#0E7490",
    "sev_info_bg": "#F5F3FF",
    "sev_info_fg": "#6D28D9",
    "radius_sm": _R["sm"],
    "radius_md": _R["md"],
    "radius_lg": _R["lg"],
    "radius_pill": _R["pill"],
    "body_size": _TY["body"]["fontSize"],
    "title_size": _TY["title"]["fontSize"],
    "heading_size": _TY["heading"]["fontSize"],
    "display_size": _TY["display"]["fontSize"],
    "stat_value_size": _TY["stat-value"]["fontSize"],
    "label_size": _TY["label"]["fontSize"],
    "label_caps_size": _TY["label-caps"]["fontSize"],
    "caption_size": _TY["caption"]["fontSize"],
    "console_size": _TY["console"]["fontSize"],
}

# -- shared QSS -------------------------------------------------------------
# ID selectors everywhere the DESIGN.md names one (#GradientHeader,
# #btn_primary, #ConsoleLog, #StatCard, …) so a dark GTK/KDE palette cannot
# bleed through.
_QSS = """\
/* ==== base ========================================================== */
QWidget {
    font-family: "Segoe UI", "Roboto", sans-serif;
    font-size: $body_size;
    color: $primary;
}
QMainWindow, QDialog { background: $neutral; }
QLabel { background: transparent; }

/* ==== gradient header ============================================== */
QFrame#GradientHeader {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 $gradient_start, stop:1 $gradient_end);
    border-radius: $radius_lg;
}
QLabel#HeaderTitle {
    color: $on_primary;
    font-size: $title_size;
    font-weight: 700;
}
QLabel#HeaderBreadcrumb {
    color: $console_text;
    font-size: $body_size;
    font-weight: 500;
}
QLabel#HeaderStatus {
    color: $status_dot;
    font-size: $label_caps_size;
    font-weight: 600;
}

/* ==== buttons ====================================================== */
QPushButton {
    background: $surface;
    color: $primary;
    border: 1px solid $border_light;
    border-radius: $radius_md;
    padding: 10px 20px;
    font-size: $label_size;
    font-weight: 600;
}
QPushButton:hover { background: $surface_hover; }
QPushButton:pressed { background: $border_light; }
QPushButton:disabled { background: $surface_hover; color: $border_hover; }

QPushButton#btn_primary {
    background: $tertiary;
    color: $on_primary;
    border: none;
}
QPushButton#btn_primary:hover { background: $accent_deep; }
QPushButton#btn_primary:pressed { background: $accent_darkest; }
QPushButton#btn_primary:disabled { background: $border_hover; color: $on_primary; }

QPushButton#btn_success { background: $success; color: $on_primary; border: none; }
QPushButton#btn_success:hover { background: $success_deep; }
QPushButton#btn_success:disabled { background: $border_hover; color: $on_primary; }

QPushButton#btn_danger { background: $danger; color: $on_primary; border: none; }
QPushButton#btn_danger:hover { background: $danger_deep; }
QPushButton#btn_danger:disabled { background: $border_hover; color: $on_primary; }

QPushButton#btn_warning { background: $warning; color: $on_primary; border: none; }
QPushButton#btn_warning:hover { background: $warning_deep; }
QPushButton#btn_warning:disabled { background: $border_hover; color: $on_primary; }

/* ==== text inputs / spin / combo ================================== */
QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox {
    background: $surface;
    color: $primary;
    border: 1px solid $border_light;
    border-radius: $radius_md;
    padding: 8px;
    selection-background-color: $accent_soft;
    selection-color: $accent_soft_text;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QAbstractSpinBox:focus, QComboBox:focus {
    border: 2px solid $tertiary;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: $surface;
    color: $display_value;
    border: 1px solid $border_light;
    selection-background-color: $accent_soft;
    selection-color: $accent_soft_text;
}

/* ==== checkboxes ================================================== */
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border_medium;
    border-radius: $radius_sm;
    background: $surface;
}
QCheckBox::indicator:checked {
    background: $tertiary;
    border: 1px solid $tertiary;
}

/* ==== group boxes ================================================= */
QGroupBox {
    background: $surface;
    color: $group_text;
    border: 1px solid $border_light;
    border-radius: $radius_lg;
    margin-top: 12px;
    padding: 24px 16px;
    font-size: $label_size;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
}

/* ==== tabs ======================================================== */
QTabWidget::pane {
    background: $surface;
    border: 1px solid $border_light;
    border-radius: $radius_md;
}
QTabBar::tab {
    background: $surface_hover;
    color: $secondary;
    padding: 8px 16px;
    margin-right: 2px;
    border-top-left-radius: $radius_md;
    border-top-right-radius: $radius_md;
}
QTabBar::tab:selected {
    background: $surface;
    color: $tertiary;
    font-weight: 700;
}

/* ==== tables ====================================================== */
QTableView {
    background: $surface;
    alternate-background-color: $neutral;
    border: 1px solid $border_light;
    border-radius: $radius_md;
    gridline-color: $border_light;
    selection-background-color: $accent_soft;
    selection-color: $accent_soft_text;
}
QHeaderView::section {
    background: $neutral;
    color: $secondary;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid $border_light;
    font-weight: 600;
}

/* ==== stat cards ================================================== */
QFrame#StatCard {
    background: $surface;
    border: 1px solid $border_light;
    border-radius: $radius_lg;
}
QLabel#StatValue {
    color: $display_value;
    font-size: $stat_value_size;
    font-weight: 700;
}
QLabel#StatCaption {
    color: $secondary;
    font-size: $caption_size;
    font-weight: 600;
}

/* ==== queue strip ================================================= */
QFrame#QueueStrip {
    background: $surface;
    border: 1px solid $border_light;
    border-radius: $radius_md;
}
QLabel#QueueCaption {
    color: $secondary;
    font-size: $label_caps_size;
    font-weight: 600;
}
QLabel#QueueLabel {
    color: $secondary;
    font-size: $label_caps_size;
    font-weight: 600;
}

/* ==== action rail ================================================= */
QFrame#ActionRail {
    background: $neutral;
    border: none;
    border-top: 1px solid $border_light;
    border-radius: 0;
    padding: 12px 16px;
}

/* ==== run summary ================================================= */
QFrame#RunSummary {
    background: $surface;
    border: 1px solid $border_light;
    border-radius: $radius_lg;
}
QFrame#RunSummary QLabel { color: $display_value; font-weight: 600; }

/* ==== run progress panel (live timeline; see ui/job_detail.py) ==== */
QFrame#ConsolePanel {
    background: $surface;
    border: 1px solid $border_light;
    border-top: 2px solid $primary;
    border-radius: $radius_lg;
}
QLabel#ProgressLabel {
    color: $secondary;
    font-size: $caption_size;
    font-weight: 600;
}

/* ==== schedule preview ============================================ */
QLabel#SchedulePreview {
    color: $secondary;
    font-size: $caption_size;
    font-weight: 600;
}

/* ==== empty state ================================================= */
QLabel#EmptyState {
    color: $secondary;
    font-size: $body_size;
    padding: 24px;
}

/* ==== console log ================================================= */
QTextEdit#ConsoleLog {
    background: $console_bg;
    color: $console_text;
    font-family: "Consolas", "Cascadia Code", "JetBrains Mono", monospace;
    font-size: $console_size;
    border: 1px solid $border_light;
    border-radius: $radius_md;
    padding: 8px;
}

/* ==== status pills ================================================ */
QLabel#status_ok { background: $status_ok_bg; color: $status_ok_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }
QLabel#status_warn { background: $status_warn_bg; color: $status_warn_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }
QLabel#status_fail { background: $status_fail_bg; color: $status_fail_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }
QLabel#status_cancelled { background: $status_cancelled_bg; color: $status_cancelled_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }

/* ==== toasts (see ui/toast.py) ==================================== */
QLabel#ToastOk { background: $status_ok_bg; color: $status_ok_fg;
    border: 1px solid $success; border-radius: $radius_md; padding: 8px 16px;
    font-size: $label_size; font-weight: 600; }
QLabel#ToastWarn { background: $status_warn_bg; color: $status_warn_fg;
    border: 1px solid $warning; border-radius: $radius_md; padding: 8px 16px;
    font-size: $label_size; font-weight: 600; }
QLabel#ToastFail { background: $status_fail_bg; color: $status_fail_fg;
    border: 1px solid $danger; border-radius: $radius_md; padding: 8px 16px;
    font-size: $label_size; font-weight: 600; }

/* ==== severity badges ============================================= */
QLabel#sev_critical { background: $sev_critical_bg; color: $sev_critical_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }
QLabel#sev_high { background: $sev_high_bg; color: $sev_high_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }
QLabel#sev_medium { background: $sev_medium_bg; color: $sev_medium_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }
QLabel#sev_low { background: $sev_low_bg; color: $sev_low_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }
QLabel#sev_info { background: $sev_info_bg; color: $sev_info_fg;
    border-radius: $radius_pill; padding: 2px 10px; font-size: $caption_size; font-weight: 600; }

/* ==== menus (force light; avoid dark-mode bleed) ================== */
QMenuBar, QMenu {
    background: $surface;
    color: $display_value;
}
QMenu::item:selected { background: $accent_soft; color: $accent_soft_text; }
QToolTip { background: $surface; color: $display_value; border: 1px solid $border_light; }
"""

STYLESHEET: str = Template(_QSS).substitute(_T)

# -- runtime header-dot colors ----------------------------------------------
# `MainWindow.set_status(status, color)` recolors the header status dot at
# runtime; these are the colors the controller passes. `ok` is DESIGN.md's
# `status-dot` (#86EFAC). `warn` (#fbbf24) and `fail` (#f87171) are the two
# runtime-only amber/red dots — DESIGN.md names status-* *pills* but no tokens
# for these dot shades, so they are recorded here (values unchanged from the
# previous literals) rather than invented into the DESIGN.md palette.
RUNTIME_STATUS: dict[str, str] = {
    "ok": _C["status-dot"],
    "warn": "#fbbf24",
    "fail": "#f87171",
}


def apply(app) -> None:
    """Install :data:`STYLESHEET` on the running ``QApplication``."""
    app.setStyleSheet(STYLESHEET)


def style_caps(label, *, px: int | None = None, weight: int | None = None) -> None:
    """Apply the ``caption``/``label-caps`` letter-spacing to a label.

    Qt Style Sheets have no ``letter-spacing`` property, so the tokens'
    ``letterSpacing: 0.02em`` intent is honoured via ``QFont`` here instead of
    dead CSS. ``QFont`` letter spacing survives QSS polish (verified). Defaults
    come from the ``caption`` token; pass ``px=12, weight=600`` for
    ``label-caps`` sizing.
    """
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QLabel

    caps = _TY["caption"]
    font = QFont(label.font() if isinstance(label, QLabel) else None)
    font.setPixelSize(px if px is not None else int(caps["fontSize"].rstrip("px")))
    font.setWeight(
        QFont.Weight(weight if weight is not None else caps["fontWeight"])
    )
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 102)
    label.setFont(font)
