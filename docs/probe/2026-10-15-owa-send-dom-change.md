# OWA compose DOM change — Send button (2026-10-15)

## Symptom

Every run notification failed, both tools:

```
Compose shell not ready: timeout reached after 60000 ms.
Compose shell not ready — retrying navigation once...
Compose shell not ready: timeout reached after 60000 ms.
Could not find the OWA message body editor. Ensure you are logged into Outlook Web.
```

## Evidence

`currentOWA.html` (a captured live compose page) shows the message-body editor,
`Cc`, `To`, and `Subject` are **unchanged**:

- `div[aria-label="Message body"]` — still present, `contenteditable="true"`.
- `div[aria-label="Cc"]`, `div[aria-label="To"]`,
  `input[aria-label="Subject"]` — unchanged.

The **Send** control changed. It is now the Fluent split-button:

```html
<button
  type="button"
  id="splitButton-r5__primaryActionButton"
  aria-label="Send"
  class="fui-Button ... fui-SplitButton__primaryActionButton ...">
```

`Ctrl+Enter` survives only in a **hidden tooltip**, not a `title` attribute:

```html
<div role="tooltip" id="tooltip-r6" ...>Send (Ctrl+Enter)</div>
```

The old locator `button[title*="Ctrl+Enter"]` therefore matched **nothing**.
`_wait_compose_ready` requires the Send control to be visible, so it polled to
the 60 s timeout, retried once, timed out again, and returned `failed` — even
though the operator was fully logged in and the editor was ready.

## Fix

`_SEND_BUTTON` matches both builds (in `store_sync/core/owa_mailer.py` and the
shared `lib/owa_mailer.py`):

```python
_SEND_BUTTON = (
    'button[title*="Ctrl+Enter"], '   # legacy OWA
    'button[aria-label="Send"], '     # 2026 Fluent split-button
    'button[aria-label="Kirim"]'      # 2026 Fluent, Indonesian UI
)
```

Used by `_wait_compose_ready` (readiness gate) and the send loop
(`send_btn = page.locator(_SEND_BUTTON).first`).

## Regression tests

- `tests/core/test_owa_mailer.py::test_editor_ready_when_send_button_only_has_aria_label`
- `tests/core/test_owa_mailer.py::test_editor_ready_with_legacy_title_send_button`
- `stg_automation_suite` `tests/compare/test_owa_compose_ready.py::test_compose_ready_selector_matches_2026_fluent_send_button`
