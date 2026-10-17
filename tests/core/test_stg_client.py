"""Tests for store_sync.core.stg_client — REST methods against a mocked transport.

No live calls: a FakeSession records requests and returns canned responses.
"""
from __future__ import annotations

import json
from urllib.parse import urlparse, parse_qs

import pytest
from requests.auth import HTTPBasicAuth
from requests.exceptions import ChunkedEncodingError

from store_sync.core.stg_client import STGClient, STGError


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.content = self.text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise STGError(f"HTTP {self.status_code}: {self.text[:200]}")


class FakeSession:
    """Records calls; returns queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def _next(self):
        if not self._responses:
            raise AssertionError("no more queued responses")
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, params=None, auth=None, headers=None, timeout=None):
        self.calls.append(("GET", url, params, auth, headers, timeout))
        return self._next()

    def post(self, url, params=None, auth=None, headers=None, timeout=None):
        self.calls.append(("POST", url, params, auth, headers, timeout))
        return self._next()


@pytest.fixture
def client_and_session():
    session = FakeSession([])
    c = STGClient(
        server="https://stg.example.com/api/",
        user="user1",
        password="pw1",
        session=session,
        timeout=5,
        max_retries=3,
        backoff=0,
    )
    return c, session


def _q(url):
    return parse_qs(urlparse(url).query)


def _path(url):
    return urlparse(url).path


class TestConstruction:
    def test_strips_trailing_slash_and_creds(self):
        c = STGClient(
            server="https://h/api/", user="  u  ", password="  p  ", session=FakeSession([])
        )
        assert c.base_url == "https://h/api"
        assert c.auth == HTTPBasicAuth("u", "p")

    def test_missing_server_raises(self):
        with pytest.raises(ValueError, match="server"):
            STGClient(server="", user="u", password="p", session=FakeSession([]))


class TestStores:
    def test_get_stores_all(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [{"ID": 1, "Name": "A"}])]
        assert c.get_stores() == [{"ID": 1, "Name": "A"}]
        method, url, params, auth, headers, timeout = s.calls[0]
        assert method == "GET"
        assert _path(url) == "/api/SubjectStores/GetStores"
        assert params in (None, {})

    def test_get_stores_with_ids(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [{"ID": 1}])]
        c.get_stores([1, 2])
        _, url, params, *_ = s.calls[0]
        assert params["storeIDs"] == "1,2"

    def test_get_store(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [{"No": 1}])]
        assert c.get_store(17417) == [{"No": 1}]
        _, url, *_ = s.calls[0]
        assert _path(url) == "/api/SubjectStores/GetStore/17417"

    def test_get_store_usage(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [{"SurveyID": "abc"}])]
        assert c.get_store_usage(13471) == [{"SurveyID": "abc"}]
        _, url, *_ = s.calls[0]
        assert _path(url) == "/api/SubjectStores/GetStoreUsage/13471"

    def test_get_survey_xml(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, None, text="<Survey/>")]
        assert c.get_survey_xml("SURV") == "<Survey/>"
        _, url, params, auth, headers, timeout = s.calls[0]
        assert _path(url) == "/api/Surveys/GetSurveyXML/SURV"
        assert headers["Accept"] == "text/xml"


class TestSurveyInterviewIDs:
    def test_builds_params_and_returns_ids(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, {"subjects": ["11", "22"]})]
        ids = c.survey_interview_ids(
            "SURV",
            date_type="Upload",
            date_start="2026-01-01T00:00:00.0000000",
            date_end="2026-01-02T00:00:00.0000000",
        )
        assert ids == ["11", "22"]
        _, url, params, *_ = s.calls[0]
        assert _path(url) == "/api/SurveyInterviewIDs"
        assert params["surveyIDs"] == "SURV"
        assert params["dateType"] == "Upload"
        assert params["dateStart"] == "2026-01-01T00:00:00.0000000"
        assert params["orderBy"] == "Ascending"

    def test_handles_bare_list(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, ["1", "2", "3"])]
        assert c.survey_interview_ids("S") == ["1", "2", "3"]

    def test_statuses_override_completed(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        c.survey_interview_ids("S", statuses="1,4", completed="true")
        _, url, params, *_ = s.calls[0]
        assert params["statuses"] == "1,4"
        assert "completed" not in params

    def test_completed_used_when_no_statuses(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        c.survey_interview_ids("S", completed="true")
        _, url, params, *_ = s.calls[0]
        assert params["completed"] == "true"

    def test_filtered_and_incomplete_flags(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        c.survey_interview_ids("S", include_incomplete_subjects=False, filtered=False)
        _, url, params, *_ = s.calls[0]
        assert params["includeIncompleteSubjects"] == "false"
        assert params["filtered"] == "false"  # not includeFiltered


class TestSimpleExport:
    def test_chunk_size_defaults_to_100(self, client_and_session):
        c, _ = client_and_session
        assert c.export_chunk == 100

    def test_chunk_size_reads_env_override(self, monkeypatch):
        monkeypatch.setenv("STG_EXPORT_CHUNK", "60")
        c = STGClient(server="https://stg.example.com", user="u", password="p",
                      session=FakeSession([]))
        assert c.export_chunk == 60

    def test_oversized_chunk_is_clamped_to_server_ceiling(self, monkeypatch):
        # The server rejects >100 subject IDs per request with a 200 whose body
        # carries no Subjects, which reads back as zero rows. Clamping keeps a
        # bad setting from turning a pull into a silent no-op.
        monkeypatch.setenv("STG_EXPORT_CHUNK", "500")
        c = STGClient(server="https://stg.example.com", user="u", password="p",
                      session=FakeSession([]))
        assert c.export_chunk == 100

    def test_explicit_chunk_beats_env_override(self, monkeypatch):
        monkeypatch.setenv("STG_EXPORT_CHUNK", "60")
        c = STGClient(server="https://stg.example.com", user="u", password="p",
                      session=FakeSession([]), export_chunk=42)
        assert c.export_chunk == 42

    def test_explicit_oversized_chunk_is_clamped(self):
        c = STGClient(server="https://stg.example.com", user="u", password="p",
                      session=FakeSession([]), export_chunk=1000)
        assert c.export_chunk == 100

    def test_oversized_call_chunk_is_clamped(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        c.simple_export("S", ["1"], chunk=500)
        _, _, params, *_ = s.calls[0]
        assert params["subjectIDs"] == "1"

    def test_workers_default_and_clamp(self, monkeypatch):
        monkeypatch.delenv("STG_EXPORT_WORKERS", raising=False)
        c = STGClient(server="https://stg.example.com", user="u", password="p",
                      session=FakeSession([]))
        assert c.export_workers == 4
        assert STGClient(server="https://stg.example.com", user="u", password="p",
                         session=FakeSession([]), export_workers=99).export_workers == 8
        assert STGClient(server="https://stg.example.com", user="u", password="p",
                         session=FakeSession([]), export_workers=0).export_workers == 1

    def test_malformed_env_chunk_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("STG_EXPORT_CHUNK", "many")
        c = STGClient(server="https://stg.example.com", user="u", password="p",
                      session=FakeSession([]))
        assert c.export_chunk == 100

    def test_single_chunk_returns_subjects(self, client_and_session):
        c, s = client_and_session
        s._responses = [
            FakeResponse(200, [{"SubjectID": "1", "Columns": [{"Var": "x", "Value": 1}]}])
        ]
        out = c.simple_export("SURV", ["1"])
        assert out == [{"SubjectID": "1", "Columns": [{"Var": "x", "Value": 1}]}]
        _, url, params, *_ = s.calls[0]
        assert _path(url) == "/api/SimpleExport"
        assert params["surveyID"] == "SURV"
        assert params["subjectIDs"] == "1"

    def test_unwraps_subjects_object(self, client_and_session):
        c, s = client_and_session
        s._responses = [
            FakeResponse(200, {"Subjects": [{"SubjectID": "9", "Columns": []}]})
        ]
        assert c.simple_export("S", ["9"]) == [{"SubjectID": "9", "Columns": []}]

    def test_variable_names_sent_when_given(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        c.simple_export("S", ["1"], variable_names=["QPM1", "Q32"])
        _, url, params, *_ = s.calls[0]
        assert params["variableNames"] == "QPM1,Q32"

    def test_variable_names_omitted_when_not_given(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        c.simple_export("S", ["1"])
        _, url, params, *_ = s.calls[0]
        assert "variableNames" not in params

    def test_should_stop_raises_cancelled_between_chunks(self, client_and_session):
        from store_sync.core.stg_client import Cancelled

        c, s = client_and_session
        s._responses = [FakeResponse(200, [{"SubjectID": "1", "Columns": []}])]
        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return calls["n"] > 1  # allow first chunk, stop before second

        with pytest.raises(Cancelled):
            c.simple_export("S", ["1", "2"], chunk=1, should_stop=stop)
        # only the first chunk's request went out
        assert len(s.calls) == 1

    def test_on_chunk_reports_progress_per_chunk(self, client_and_session):
        c, s = client_and_session
        s._responses = [
            FakeResponse(200, [{"SubjectID": "1", "Columns": []}]),
            FakeResponse(200, [{"SubjectID": "2", "Columns": []}]),
            FakeResponse(200, [{"SubjectID": "3", "Columns": []}]),
        ]
        seen = []
        c.simple_export("S", ["1", "2", "3"], chunk=1, on_chunk=lambda d, t: seen.append((d, t)))
        assert seen == [(1, 3), (2, 3), (3, 3)]

    def test_on_chunk_optional(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        assert c.simple_export("S", ["1"]) == []


class DictSession:
    """Thread-safe fake: answers per requested id, in any order."""

    def __init__(self, rows_for, delay=0.0, fail_on=None):
        import threading

        self.rows_for = rows_for
        self.delay = delay
        self.fail_on = fail_on or set()
        self.calls = []
        self._lock = threading.Lock()

    def get(self, url, params=None, auth=None, headers=None, timeout=None):
        import time as _time

        ids = params["subjectIDs"].split(",")
        with self._lock:
            self.calls.append(ids)
        if self.delay:
            _time.sleep(self.delay)
        for sid in ids:
            if sid in self.fail_on:
                raise ChunkedEncodingError(f"dropped {sid}")
        return FakeResponse(200, [
            {"SubjectID": sid, "Columns": [{"Var": "x", "Value": sid}]} for sid in ids
        ])

    def post(self, url, params=None, auth=None, headers=None, timeout=None):
        raise AssertionError("no writes expected")

    def close(self):
        pass


class TestParallelExport:
    @staticmethod
    def _client(session, **kw):
        return STGClient(server="https://h", user="u", password="p",
                         session=session, max_retries=1, backoff=0, **kw)

    def test_chunks_never_exceed_the_server_ceiling(self):
        s = DictSession({})
        c = self._client(s, export_chunk=100, export_workers=4)
        c.simple_export("S", [str(i) for i in range(250)])
        assert [len(call) for call in s.calls]
        assert all(len(call) <= 100 for call in s.calls)
        assert sorted(len(call) for call in s.calls) == [50, 100, 100]

    def test_result_keeps_caller_id_order_despite_completion_order(self):
        # Chunks land out of order; the returned list must not.
        s = DictSession({}, delay=0.01)
        c = self._client(s, export_chunk=1, export_workers=4)
        out = c.simple_export("S", ["5", "1", "4", "2", "3"])
        assert [r["SubjectID"] for r in out] == ["5", "1", "4", "2", "3"]

    def test_progress_is_cumulative_and_ends_at_total(self):
        s = DictSession({})
        c = self._client(s, export_chunk=10, export_workers=4)
        seen = []
        c.simple_export("S", [str(i) for i in range(95)], on_chunk=lambda d, t: seen.append(d))
        assert seen == sorted(seen)          # only climbs
        assert seen[-1] == 95

    def test_concurrent_chunks_overlap(self):
        # 4 chunks of 1 id, each sleeping 100ms: serial would be ~400ms.
        import time

        s = DictSession({}, delay=0.1)
        c = self._client(s, export_chunk=1, export_workers=4)
        t = time.perf_counter()
        c.simple_export("S", ["1", "2", "3", "4"])
        assert time.perf_counter() - t < 0.35

    def test_workers_one_stays_serial(self):
        s = DictSession({})
        c = self._client(s, export_chunk=2, export_workers=1)
        out = c.simple_export("S", ["1", "2", "3"])
        assert [r["SubjectID"] for r in out] == ["1", "2", "3"]

    def test_cancelled_before_dispatch(self):
        from store_sync.core.stg_client import Cancelled

        s = DictSession({})
        c = self._client(s, export_chunk=1, export_workers=4)
        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return calls["n"] > 2

        with pytest.raises(Cancelled):
            c.simple_export("S", ["1", "2", "3", "4", "5", "6"], should_stop=stop)
        assert len(s.calls) < 6

    def test_failure_in_one_chunk_propagates(self):
        s = DictSession({}, fail_on={"3"})
        c = self._client(s, export_chunk=1, export_workers=2)
        with pytest.raises(ChunkedEncodingError):
            c.simple_export("S", ["1", "2", "3", "4"])


class TestRetry:
    def test_retries_on_chunked_encoding_error(self, client_and_session):
        c, s = client_and_session
        s._responses = [
            ChunkedEncodingError("premature"),
            FakeResponse(200, [{"SubjectID": "1", "Columns": []}]),
        ]
        out = c.simple_export("S", ["1"])
        assert out == [{"SubjectID": "1", "Columns": []}]
        assert len(s.calls) == 2

    def test_gives_up_after_max_retries(self, client_and_session):
        c, s = client_and_session
        s._responses = [
            ChunkedEncodingError("x"),
            ChunkedEncodingError("x"),
            ChunkedEncodingError("x"),
        ]
        with pytest.raises(ChunkedEncodingError):
            c.simple_export("S", ["1"])
        assert len(s.calls) == 3

    def test_get_stores_does_not_retry_needed_but_works(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        assert c.get_stores() == []


class TestWriteRows:
    def test_add_row(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, {"Done": True})]
        out = c.add_row(17620, {"No": 4, "SbjNum": 104, "I7_1_2": ""})
        assert out == {"Done": True}
        method, url, params, *_ = s.calls[0]
        assert method == "POST"
        assert _path(url) == "/api/SubjectStores/AddRow/17620"
        assert params == {"No": 4, "SbjNum": 104, "I7_1_2": ""}

    def test_update_row(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, {"Done": True})]
        c.update_row(17417, {"uniqueID": "1001", "I7_1_2": "X"})
        method, url, params, *_ = s.calls[0]
        assert method == "POST"
        assert _path(url) == "/api/SubjectStores/UpdateRow/17417"
        assert params == {"uniqueID": "1001", "I7_1_2": "X"}

    def test_no_delete_surface(self, client_and_session):
        c, _ = client_and_session
        assert not hasattr(c, "delete_row")
        assert not hasattr(c, "delete_all_rows")


class TestErrors:
    def test_http_error_raises_stg_error(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(500, None, text="Error: id column data not found")]
        with pytest.raises(STGError, match="id column data not found"):
            c.update_row(17620, {"SbjNum": 1})

    def test_all_methods_use_basic_auth(self, client_and_session):
        c, s = client_and_session
        s._responses = [FakeResponse(200, [])]
        c.get_stores()
        assert s.calls[0][3] == HTTPBasicAuth("user1", "pw1")
