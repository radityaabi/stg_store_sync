"""Multi-PIC helpers — parse / format the `;`-separated PIC list stored on a job.

The list is stored in the registry's ``jobs.pic`` column as a single
``TEXT`` value using ``;`` as the separator (e.g. ``"alice@x.com; bob@y.com"``).
These helpers are the single source of truth for going between the stored string
and a Python list, so every surface (the job-detail form, the registry, the
mailer) agrees on the canonical form.

Parse contract:
    * Split on ``;``.
    * Trim whitespace around each entry.
    * Drop empty entries.
    * Dedupe case-insensitively, preserving first-seen order.

Format contract:
    * Join with ``"; "`` (semicolon + space) — the canonical stored form.
    * Empty list → empty string.

The entries are **email addresses**: this column is the recipient list for a
job's run notifications. Classification is deliberately soft — a PIC column may
hold people's names from before that decision, and such a job must keep saving
and keep displaying. So nothing here rejects a non-address:
:func:`split_pic_emails` hands the caller both groups and lets the UI warn while
the mailer quietly mails only the addresses.
"""
from __future__ import annotations

import re

__all__ = [
    "parse_pics",
    "format_pics",
    "is_valid_email",
    "split_pic_emails",
    "pic_email_warning",
]

# Conservative on purpose. This decides who gets mailed, so the cost of
# accepting a malformed address (a silent bounce nobody sees) is higher than
# the cost of rejecting an exotic-but-legal one (a visible warning in the
# dialog, and the person corrects it). A dotted domain is required:
# "citra@nielseniq" is a typo far more often than it is an intranet host.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$")


def parse_pics(raw: str | None) -> list[str]:
    """Parse a stored PIC string into a clean, deduped list.

    >>> parse_pics("alice; bob ;;alice")
    ['alice', 'bob']
    >>> parse_pics("")
    []
    >>> parse_pics(None)
    []
    """
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for token in raw.split(";"):
        name = token.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def format_pics(pics: list[str]) -> str:
    """Render a PIC list into the canonical stored form.

    >>> format_pics(["alice", "bob"])
    'alice; bob'
    >>> format_pics([])
    ''
    """
    # Preserve caller's input order but dedupe case-insensitively so the
    # stored form is stable regardless of how a caller built the list.
    seen: set[str] = set()
    clean: list[str] = []
    for name in pics:
        s = (name or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(s)
    return "; ".join(clean)


def is_valid_email(value: str | None) -> bool:
    """Is this entry an address we are willing to mail a report to?

    >>> is_valid_email("citra@nielseniq.com")
    True
    >>> is_valid_email("Citra")
    False
    """
    if not value:
        return False
    return bool(_EMAIL_RE.match(value.strip()))


def split_pic_emails(raw: str | None) -> tuple[list[str], list[str]]:
    """Split a stored PIC string into ``(addresses, everything else)``.

    Built on :func:`parse_pics`, so trimming, empty-entry handling and the
    case-insensitive dedupe are inherited — which matters here because
    ``Citra@`` and ``citra@`` are one mailbox and must not receive two copies.

    Non-addresses are *returned*, never discarded silently — the caller decides
    whether that is a warning to show (the job-detail form) or simply nobody to
    mail (the mailer).

    >>> split_pic_emails("citra@nielseniq.com; Budi")
    (['citra@nielseniq.com'], ['Budi'])
    """
    valid: list[str] = []
    other: list[str] = []
    for entry in parse_pics(raw):
        (valid if is_valid_email(entry) else other).append(entry)
    return valid, other


def pic_email_warning(raw: str | None) -> str:
    """Describe which entries will not receive reports; ``""`` when all will.

    Non-fatal by design, and shared so the job-detail form and the mailer say
    the same thing. Saving is never blocked: a job whose PIC column holds names
    predates the decision to mail reports there, and making it unsaveable would
    punish the wrong person.

    >>> pic_email_warning("citra@nielseniq.com; Budi")
    '1 entry is not an email address and will not receive reports: Budi'
    """
    _, other = split_pic_emails(raw)
    if not other:
        return ""
    count = "1 entry is" if len(other) == 1 else f"{len(other)} entries are"
    return (
        f"{count} not an email address and will not receive reports: "
        + ", ".join(other)
    )
