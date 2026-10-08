"""Tests for the shared theme (offscreen Qt)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from store_sync.ui import theme  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_stylesheet_is_nonempty_string():
    assert isinstance(theme.STYLESHEET, str)
    assert theme.STYLESHEET.strip()


def test_stylesheet_has_key_selectors():
    for selector in (
        "#GradientHeader",
        "#btn_primary",
        "#btn_danger",
        "#ConsoleLog",
        "#StatCard",
        "#ActionRail",
    ):
        assert selector in theme.STYLESHEET, selector


def test_stylesheet_has_gradient_endpoints():
    assert "#667EEA" in theme.STYLESHEET
    assert "#764BA2" in theme.STYLESHEET


def test_tokens_mirror_design_md():
    assert theme.TOKENS["colors"]["gradient-start"] == "#667EEA"
    assert theme.TOKENS["colors"]["gradient-end"] == "#764BA2"
    assert theme.TOKENS["typography"]["stat-value"]["fontSize"] == "24px"
    assert theme.TOKENS["rounded"]["lg"] == "8px"


def test_apply_sets_app_stylesheet():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet("")
    theme.apply(app)
    assert app.styleSheet()
