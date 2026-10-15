"""Playwright-driven Outlook Web Access (OWA) email transport.

Ported from the sibling ``stg_automation_suite`` repo
(``src/lib/owa_mailer.py``) — reference only, never imported (repo rule). The
only change beyond naming is that this module knows nothing about that repo: its
logger is local, its browser profile lives under this tool's own state directory,
and the account-picker email comes from ``STG_OWA_EMAIL``.

Why OWA and not SMTP: OWA reuses the operator's existing signed-in browser
session, so there is no relay, no app password, and no IT ticket. The cost is
that the browser is deliberately **not** headless — OWA's own automation
handling makes a headless send unreliable — so a send raises a browser window.

:func:`send_mail_via_owa_async` never raises: every failure is returned as an
:class:`OwaOutcome` with ``status="failed"``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time
import traceback
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

__all__ = ["OwaOutcome", "send_mail_via_owa_async"]

logger = logging.getLogger(__name__)

_PROFILE_DIRNAME = "playwright_profile_owa"

#: The OWA Send control. Historically a ``button`` carrying
#: ``title="Send (Ctrl+Enter)"``; since the 2026 Fluent split-button rewrite it
#: is ``button[aria-label="Send"]`` (the "Ctrl+Enter" text now survives only in
#: a hidden ``role="tooltip"`` node, not a ``title`` attribute). Both are
#: matched so the wait survives either OWA build — a Send button the locator
#: cannot see is the difference between a send and a 60 s "Compose shell not
#: ready" timeout.
_SEND_BUTTON = (
    'button[title*="Ctrl+Enter"], '
    'button[aria-label="Send"], '
    'button[aria-label="Kirim"]'
)


def _default_profile_dir() -> Path:
    """The persistent browser profile, overridable via ``STG_OWA_PROFILE_DIR``."""
    override = os.environ.get("STG_OWA_PROFILE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".stg_store_sync" / _PROFILE_DIRNAME


def _playwright_factory():
    """Import Playwright lazily, so importing this module never needs it.

    Tests replace this with a fake; production calls the real ``async_playwright``.
    """
    from playwright.async_api import async_playwright

    return async_playwright()


@dataclass(frozen=True)
class OwaOutcome:
    """What happened when an OWA send was attempted.

    ``status`` is one of ``sent`` · ``skipped`` · ``failed``.
    """

    status: str
    detail: str = ""


async def _handle_account_picker(page) -> bool:
    """If OWA shows an account picker (Microsoft AAD tile), click the target account.

    Reads the target email from ``STG_OWA_EMAIL``. Returns True if an account
    tile was clicked.
    """
    target_email = os.environ.get("STG_OWA_EMAIL", "").strip()

    locators = []
    if target_email:
        email_lower = target_email.lower()
        locators.extend([
            page.locator(f'div[role="button"][data-test-id="{email_lower}"]'),
            page.locator(f'div[aria-label*="{target_email}"]'),
        ])
        if hasattr(page, "get_by_text"):
            locators.append(page.get_by_text(target_email, exact=False))

    locators.extend([
        page.locator('div[role="button"][data-test-id]'),
        page.locator('div.tile[role="listitem"]'),
    ])

    for loc in locators:
        try:
            count = await loc.count()
            if count > 0 and await loc.first.is_visible():
                logger.info("Account picker tile detected — attempting to click...")
                try:
                    await loc.first.click(force=True, timeout=2000)
                except Exception:
                    try:
                        await loc.first.focus()
                        await page.keyboard.press("Enter")
                    except Exception:
                        await loc.first.evaluate("el => el.click()")
                await page.wait_for_timeout(3000)
                return True
        except Exception:
            pass
    return False


async def _wait_compose_ready(page, editor_locator, timeout: int = 60000) -> bool:
    """Wait until the OWA compose shell is actually usable, not just present.

    Multi-stage:
      1. Polling loop checking for editor visibility.
      2. If login page / account tile is visible, auto-clicks target account.
      3. Verifies Send button visible & editor accepts focus.

    Returns ``True`` once every stage passes within ``timeout``,
    ``False`` on any stage timing out.
    """
    start_time = time.monotonic()
    send_locator = page.locator(_SEND_BUTTON)

    while (time.monotonic() - start_time) * 1000 < timeout:
        try:
            if await editor_locator.first.is_visible() and await send_locator.first.is_visible():
                try:
                    await editor_locator.first.focus()
                    return True
                except Exception:
                    pass
        except Exception:
            pass

        if await _handle_account_picker(page):
            logger.info("Clicked AAD account tile, waiting for redirect...")
            await page.wait_for_timeout(3000)
            continue

        await page.wait_for_timeout(1000)

    logger.warning("Compose shell not ready: timeout reached after %d ms.", timeout)
    return False


async def _wait_attachment_settled(
    page, first_filename: str, timeout: int = 8000, min_wait: int = 1500,
) -> None:
    """Wait for an attachment's upload progress indicator to clear."""
    await page.wait_for_timeout(min_wait)
    card = page.locator(f'text="{first_filename}"').first
    try:
        progress = card.locator(
            'xpath=ancestor::*[self::div][1]//*[@role="progressbar"]'
        )
        remaining = max(0, timeout - min_wait)
        await progress.first.wait_for(state="hidden", timeout=remaining)
    except Exception:
        pass


async def _copy_html_to_clipboard(page, html_content: str):
    """Write HTML to the clipboard so it can be pasted with rich formatting."""
    escaped_html = html_content.replace('`', '\\`').replace('$', '\\$')

    await page.evaluate(f"""
        async () => {{
            const html = `{escaped_html}`;
            try {{
                const blob = new Blob([html], {{ type: 'text/html' }});
                const item = new ClipboardItem({{ 'text/html': blob }});
                await navigator.clipboard.write([item]);
            }} catch(e) {{
                const div = document.createElement('div');
                div.innerHTML = html;
                div.contentEditable = true;
                document.body.appendChild(div);

                const range = document.createRange();
                range.selectNodeContents(div);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);

                document.execCommand('copy');
                document.body.removeChild(div);
            }}
        }}
    """)


async def send_mail_via_owa_async(
    to: Sequence[str],
    cc: Sequence[str] = (),
    subject: str = "",
    html: str = "",
    attachments: Iterable[tuple[str, bytes]] = (),
    *,
    profile_dir: Path | str | None = None,
) -> OwaOutcome:
    """Zero-touch email delivery via Outlook Web Access (OWA) using Playwright.

    Args:
        to: primary recipients.
        cc: carbon-copy recipients.
        subject: subject line.
        html: the message body, HTML.
        attachments: ``(filename, content)`` pairs.
        profile_dir: persistent browser profile directory. Defaults to
            :func:`_default_profile_dir`.
    """
    recipients = [addr for addr in to if addr]
    if not recipients:
        return OwaOutcome("skipped", "No valid 'to' recipient provided.")

    copies = [addr for addr in cc if addr]

    user_data_dir = Path(profile_dir) if profile_dir else _default_profile_dir()

    base_url = "https://outlook.office.com/mail/deeplink/compose"
    params = {
        "to": ";".join(recipients),
        "subject": subject,
    }
    url = f"{base_url}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"

    logger.info("Launching Playwright to send email via OWA. URL length: %d", len(url))

    os.makedirs(user_data_dir, exist_ok=True)

    try:
        async with _playwright_factory() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=False,
                channel="msedge",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
                permissions=["clipboard-read", "clipboard-write"],
            )

            page = context.pages[0] if context.pages else await context.new_page()

            logger.info("Navigating to OWA compose deep link...")
            await page.goto(url, timeout=90000)

            logger.info("Waiting for OWA compose shell to be ready...")
            editor_locator = page.locator(
                'div[aria-label="Message body"], div[aria-label="Isi pesan"]'
            )

            ready = await _wait_compose_ready(page, editor_locator, timeout=60000)
            if not ready:
                logger.warning("Compose shell not ready — retrying navigation once...")
                try:
                    await page.goto(url, timeout=90000)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Retry navigation failed: %s", exc)
                ready = await _wait_compose_ready(page, editor_locator, timeout=60000)

            if not ready:
                logger.error(
                    "Could not find the OWA message body editor. "
                    "Ensure you are logged into Outlook Web."
                )
                await context.close()
                return OwaOutcome(
                    "failed",
                    "Timeout waiting for OWA editor. "
                    "Please login to Outlook Web in the Playwright window.",
                )

            if copies:
                logger.info("Injecting CC...")
                cc_text = "; ".join(copies) + ";"
                try:
                    cc_input = page.locator(
                        'div[aria-label="Cc"], input[aria-label="Cc"]'
                    )
                    if await cc_input.count() > 0:
                        await cc_input.first.focus()
                        await page.keyboard.type(cc_text, delay=50)
                        await page.wait_for_timeout(1000)
                        await page.keyboard.press("Tab")
                        await page.wait_for_timeout(1000)
                    else:
                        logger.warning("CC input not found in DOM.")
                except Exception as e:
                    logger.warning("Could not inject CC automatically: %s", e)

            logger.info("Injecting HTML body...")
            await _copy_html_to_clipboard(page, html)
            await editor_locator.first.focus()

            modifier = "Meta" if sys.platform == "darwin" else "Control"
            await page.keyboard.press(f"{modifier}+v")
            await page.wait_for_timeout(2000)

            if attachments:
                logger.info("Attaching files...")
                temp_dir = tempfile.mkdtemp()
                file_paths = []
                attach_list = list(attachments)
                for filename, content in attach_list:
                    temp_path = os.path.join(temp_dir, filename)
                    with open(temp_path, "wb") as f:
                        f.write(content)
                    file_paths.append(temp_path)

                file_inputs = page.locator('input[type="file"][multiple]')
                count = await file_inputs.count()
                attached = False

                if count == 0:
                    file_inputs = page.locator('input[type="file"]')
                    count = await file_inputs.count()

                for i in range(count):
                    try:
                        await file_inputs.nth(i).set_input_files(
                            file_paths, timeout=2000
                        )
                        logger.info(
                            "Sent file to input index %d. Waiting to see if it registers...",
                            i,
                        )
                        try:
                            first_filename = attach_list[0][0]
                            card = page.locator(f'text="{first_filename}"')
                            await card.first.wait_for(state="visible", timeout=3000)
                            logger.info(
                                "Attachment %s appeared in UI! Input index %d is correct.",
                                first_filename,
                                i,
                            )
                            attached = True
                            break
                        except Exception:
                            logger.info(
                                "Input %d failed to show attachment in UI. Trying next...",
                                i,
                            )
                    except Exception:
                        pass

                if attached:
                    logger.info("Waiting for attachment upload to settle...")
                    await _wait_attachment_settled(page, attach_list[0][0])

            logger.info("Initiating Send sequence...")
            max_send_attempts = 3
            sent_successfully = False

            for attempt in range(max_send_attempts):
                try:
                    for dismiss_text in ["Dismiss", "Tutup", "Abaikan"]:
                        dismiss_btn = page.get_by_text(dismiss_text, exact=True)
                        if (
                            await dismiss_btn.count() > 0
                            and await dismiss_btn.first.is_visible()
                        ):
                            logger.info("Dismissing banner using '%s'...", dismiss_text)
                            await dismiss_btn.first.click(timeout=1000)
                            await page.wait_for_timeout(1000)

                    try:
                        await editor_locator.first.focus()
                    except Exception:
                        pass

                    send_btn = page.locator(_SEND_BUTTON).first
                    if await send_btn.is_visible():
                        try:
                            await send_btn.click(force=True, timeout=1500)
                            logger.info(
                                "Send attempt %d: Clicked Send button (force=True)",
                                attempt + 1,
                            )
                        except Exception:
                            await send_btn.evaluate("el => el.click()")
                            logger.info(
                                "Send attempt %d: Clicked Send button (JS evaluate)",
                                attempt + 1,
                            )

                    await page.wait_for_timeout(500)
                    try:
                        await editor_locator.first.focus()
                    except Exception:
                        pass
                    await page.keyboard.press(f"{modifier}+Enter")
                    logger.info(
                        "Send attempt %d: Triggered Ctrl+Enter with editor focus",
                        attempt + 1,
                    )

                    await page.wait_for_timeout(2000)

                    # 5. Wait for window to close, editor to disappear, or completion screen
                    await page.wait_for_timeout(3000)

                    editor_visible = await editor_locator.first.is_visible()
                    close_msg = page.get_by_text("close this window", exact=False)
                    inbox_btn = page.get_by_text("Go to Inbox", exact=False)
                    completion_screen = (
                        (await close_msg.count() > 0 and await close_msg.first.is_visible())
                        or (await inbox_btn.count() > 0 and await inbox_btn.first.is_visible())
                    )

                    if not editor_visible or completion_screen:
                        logger.info(
                            "Send confirmed (editor_visible=%s, completion_screen=%s). Closing window!",
                            editor_visible,
                            completion_screen,
                        )
                        sent_successfully = True
                        break

                except Exception as e:
                    if (
                        "TargetClosedError" in str(type(e))
                        or "has been closed" in str(e)
                    ):
                        logger.info("OWA window closed automatically. Send successful!")
                        sent_successfully = True
                        break
                    else:
                        logger.warning(
                            "Error during send attempt %d: %s", attempt + 1, e
                        )

            if not sent_successfully:
                logger.error(
                    "Failed to send email after multiple attempts (stuck in draft)."
                )
            else:
                try:
                    await page.evaluate("window.close()")
                except Exception:
                    pass

            try:
                await context.close()
            except Exception:
                pass

            if sent_successfully:
                return OwaOutcome("sent", "Email sent successfully via OWA automation.")
            else:
                return OwaOutcome(
                    "failed", "Email got stuck in draft. Check Playwright logs."
                )

    except Exception as e:
        logger.error("OWA Mailer failed: %s", e)
        logger.error(traceback.format_exc())
        return OwaOutcome("failed", f"Playwright automation error: {e}")
