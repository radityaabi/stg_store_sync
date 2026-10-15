"""Tests for store_sync.core.owa_mailer — the Playwright/OWA transport.

Everything here runs against fakes; no browser is launched and no socket is
opened. The coroutine is driven with ``asyncio.run`` (this repo has no
``pytest-asyncio`` dependency).
"""
from __future__ import annotations

import asyncio

import pytest

from store_sync.core import owa_mailer
from store_sync.core.owa_mailer import send_mail_via_owa_async


class FakeLocator:
    """Minimal stand-in for a Playwright Locator."""

    def __init__(self, *, visible: bool = True, count: int = 1, focusable: bool = True):
        self._visible = visible
        self._count = count
        self._focusable = focusable

    @property
    def first(self):
        return self

    def locator(self, selector):
        return FakeLocator(visible=False, count=0)

    async def is_visible(self):
        return self._visible

    async def count(self):
        return self._count

    async def focus(self):
        if not self._focusable:
            raise RuntimeError("editor refused focus")

    async def click(self, **kw):
        return None

    async def evaluate(self, js):
        return None

    async def wait_for(self, **kw):
        return None

    async def set_input_files(self, files, **kw):
        return None


class FakeKeyboard:
    def __init__(self):
        self.presses: list[str] = []
        self.types: list[str] = []

    async def press(self, key):
        self.presses.append(key)

    async def type(self, text, delay=0):
        self.types.append(text)


class FakePage:
    def __init__(self, *, editor_visible: bool = True):
        self.goto_url: str | None = None
        self.keyboard = FakeKeyboard()
        self._editor_visible = editor_visible

    async def goto(self, url, timeout=0):
        self.goto_url = url

    def locator(self, selector):
        if 'aria-label="Message body"' in selector or "Isi pesan" in selector:
            return FakeLocator(visible=self._editor_visible,
                               count=1 if self._editor_visible else 0)
        if "Ctrl+Enter" in selector:
            return FakeLocator(visible=True)
        return FakeLocator(visible=False, count=0)

    def get_by_text(self, text, exact=False):
        return FakeLocator(visible=False, count=0)

    async def wait_for_timeout(self, ms):
        return None

    async def evaluate(self, js):
        return None


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self._page = page
        self.closed = False

    async def new_page(self):
        return self._page

    async def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, ctx):
        self._ctx = ctx

    async def launch_persistent_context(self, *args, **kwargs):
        return self._ctx


class FakePlaywright:
    """Both the object yielded by ``async with`` and its context manager."""

    def __init__(self, page):
        self.page = page
        self.ctx = FakeContext(page)
        self.chromium = FakeChromium(self.ctx)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeClock:
    """``monotonic`` that jumps 20 s per call, so the wait loops end instantly."""

    def __init__(self):
        self._t = 0.0

    def monotonic(self):
        self._t += 20.0
        return self._t


@pytest.fixture
def fake_pw():
    return FakePlaywright(FakePage(editor_visible=True))


@pytest.fixture
def fake_pw_never_ready():
    return FakePlaywright(FakePage(editor_visible=False))


def test_empty_recipients_skip_without_launching(monkeypatch):
    monkeypatch.setattr(owa_mailer, "_playwright_factory",
                        lambda: pytest.fail("must not launch a browser"))
    out = asyncio.run(send_mail_via_owa_async(to=[], subject="s", html="h"))
    assert out.status == "skipped"


def test_deep_link_carries_to_and_subject(fake_pw, monkeypatch, tmp_path):
    monkeypatch.setattr(owa_mailer, "_playwright_factory", lambda: fake_pw)
    asyncio.run(send_mail_via_owa_async(
        to=["a@x.com", "b@y.com"], subject="Hi there", html="<p>x</p>",
        profile_dir=tmp_path,
    ))
    url = fake_pw.page.goto_url
    assert "outlook.office.com/mail/deeplink/compose" in url
    assert "to=a%40x.com%3Bb%40y.com" in url
    assert "subject=Hi%20there" in url


def test_editor_never_ready_reports_failed(fake_pw_never_ready, monkeypatch, tmp_path):
    monkeypatch.setattr(owa_mailer, "_playwright_factory", lambda: fake_pw_never_ready)
    monkeypatch.setattr(owa_mailer, "time", FakeClock())
    out = asyncio.run(send_mail_via_owa_async(
        to=["a@x.com"], subject="s", html="h", profile_dir=tmp_path,
    ))
    assert out.status == "failed"
    assert "OWA" in out.detail


def test_transport_error_is_contained_as_failed(monkeypatch, tmp_path):
    """A raising Playwright must surface as ``failed``, never as an exception."""
    class BoomPlaywright:
        async def __aenter__(self):
            raise RuntimeError("no msedge")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(owa_mailer, "_playwright_factory", lambda: BoomPlaywright())
    out = asyncio.run(send_mail_via_owa_async(
        to=["a@x.com"], subject="s", html="h", profile_dir=tmp_path,
    ))
    assert out.status == "failed"
    assert "no msedge" in out.detail
