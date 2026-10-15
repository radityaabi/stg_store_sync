"""Tests for store_sync.core.notify — the run-notification domain.

No network, no browser: the transport seam ``notify._send`` is monkeypatched.
"""
from __future__ import annotations

import pytest

from store_sync.core import notify
from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
from store_sync.core.owa_mailer import OwaOutcome
from store_sync.core.planner import Action, Plan
from store_sync.core.state import State
from store_sync.core.sync import SyncResult


def a_job(pic=""):
    return JobConfig("link1", StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig(), pic=pic)


def a_result(adds=1, dry_run=False, status="ok", values=None):
    acts = [Action("add", f"k{i}", payload={"SbjNum": f"k{i}", "I7_1_2": "v"})
            for i in range(adds)]
    if values:  # one action carrying hostile column values
        acts = [Action("add", "kx", payload={"SbjNum": "kx", **values})]
    return SyncResult(plan=Plan(actions=acts), dry_run=dry_run, status=status,
                      counts={"add": len(acts), "update": 0, "same": 0, "conflict": 0},
                      run_id=1)


@pytest.fixture
def state():
    s = State(":memory:")
    s.start_run(job="link1", dry_run=False)
    return s


def test_should_notify_only_when_live_and_adds():
    assert notify.should_notify(a_result(adds=1))
    assert not notify.should_notify(a_result(adds=0))
    assert not notify.should_notify(a_result(adds=1, dry_run=True))


def test_subject_names_job_and_add_count():
    s = notify.subject_line(a_job(), a_result(adds=3))
    assert s.startswith("[StoreSync] link1 — 3 row(s) added")


def test_render_escapes_store_values():
    html = notify.render_report(
        a_job(), a_result(values={"I7_1_2": "<script>x</script>"}))
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_render_caps_the_add_list():
    html = notify.render_report(a_job(), a_result(adds=60), max_rows=50)
    assert "and 10 more" in html


def test_notify_sends_when_adds_and_records_sent(monkeypatch, state):
    calls = []
    monkeypatch.setattr(notify, "_send",
                        lambda **kw: calls.append(kw) or OwaOutcome("sent", ""))
    out = notify.notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1))
    assert out.status == "sent" and calls and calls[0]["to"] == ["a@x.com"]
    assert state.notification_for_run(1)["status"] == "sent"


def test_notify_skips_zero_adds_and_no_pic(monkeypatch, state):
    monkeypatch.setattr(notify, "_send", lambda **kw: pytest.fail("must not send"))
    assert notify.notify_run_report(
        a_job(pic="a@x.com"), state, a_result(adds=0)).status == "skipped"
    assert notify.notify_run_report(
        a_job(pic=""), state, a_result(adds=1)).status == "skipped"


def test_notify_dry_run_writes_html_and_does_not_send(monkeypatch, state, tmp_path):
    monkeypatch.setattr(notify, "_send", lambda **kw: pytest.fail("must not send"))
    out = notify.notify_run_report(
        a_job(pic="a@x.com"), state, a_result(adds=1, dry_run=True), out_dir=tmp_path)
    assert out.status == "dry_run" and (tmp_path / "run_1.html").exists()


def test_notify_transport_exception_is_recorded_failed_not_raised(monkeypatch, state):
    def boom(**kw):
        raise RuntimeError("no playwright")

    monkeypatch.setattr(notify, "_send", boom)
    out = notify.notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1))
    assert out.status == "failed" and "no playwright" in out.reason
    assert state.notification_for_run(1)["status"] == "failed"


def test_notify_skips_a_run_already_sent_unless_forced(monkeypatch, state):
    monkeypatch.setattr(notify, "_send", lambda **kw: OwaOutcome("sent", ""))
    notify.notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1))
    assert notify.notify_run_report(
        a_job(pic="a@x.com"), state, a_result(adds=1)).status == "skipped"
    assert notify.notify_run_report(
        a_job(pic="a@x.com"), state, a_result(adds=1), force=True).status == "sent"


def test_notify_skip_reason_names_non_address_entries(monkeypatch, state):
    monkeypatch.setattr(notify, "_send", lambda **kw: pytest.fail("must not send"))
    out = notify.notify_run_report(a_job(pic="Budi"), state, a_result(adds=1))
    assert out.status == "skipped" and "Budi" in out.reason
