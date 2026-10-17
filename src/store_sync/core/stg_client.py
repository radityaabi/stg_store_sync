"""STG REST client: Subject Stores + survey pull.

Thin, transport-injectable wrapper over the SurveyToGo REST API. Every method
returns parsed JSON. Retries apply to GETs that STG is known to drop mid-stream
(`/SimpleExport`). Write semantics (Fase 0):
  - `AddRow` / `UpdateRow` send column values as query params; every column must
    be present (omitted columns are reset to their default).
  - The tool never deletes: there is no `DeleteRow` here (spec §6).
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any, Callable, Iterable, Sequence

import requests
from requests.auth import HTTPBasicAuth
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError as ReqConnectionError,
)
from urllib3.exceptions import ProtocolError

JSON = Any

# Transient errors that justify a retry (STG drops chunked bodies mid-transfer).
_RETRYABLE = (ChunkedEncodingError, ProtocolError, ReqConnectionError)

DEFAULT_TIMEOUT = 120
EXPORT_TIMEOUT = 300
# 100 is the server's hard ceiling, not a preference: `/SimpleExport` rejects a
# request carrying more than 100 subject IDs with `200 {"Error": ...,
# "Description": "Incorrect Input: Too many Subject IDs"}`. Any larger value is
# clamped (see `_chunk_setting`) so a bad env var can never silently shrink a
# pull — the rejection parses as "no rows", which would look like a clean run.
MAX_EXPORT_CHUNK = 100
DEFAULT_EXPORT_CHUNK = MAX_EXPORT_CHUNK
EXPORT_CHUNK_ENV = "STG_EXPORT_CHUNK"
# Chunks are independent requests, so the pull is latency-bound: one round-trip
# per 100 ids, ~16 for a 1500-interview survey. A small pool overlaps those
# round-trips without stressing the server (measured: 3.1x at 4 workers, 3.2x at
# 8, degrading by 12).
DEFAULT_EXPORT_WORKERS = 4
EXPORT_WORKERS_ENV = "STG_EXPORT_WORKERS"
MAX_EXPORT_WORKERS = 8


class STGError(RuntimeError):
    """A non-2xx response from the STG API (or a malformed one)."""


class Cancelled(Exception):
    """A cooperative stop was requested (raised between export chunks)."""


class STGClient:
    def __init__(
        self,
        *,
        server: str,
        user: str,
        password: str,
        session: Any | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        backoff: float = 5.0,
        export_chunk: int | None = None,
        export_workers: int | None = None,
    ):
        if not server:
            raise ValueError("server is required")
        self.base_url = server.rstrip("/")
        self.auth = HTTPBasicAuth((user or "").strip(), (password or "").strip())
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.backoff = backoff
        self.export_chunk = max(1, int(self._chunk_setting(export_chunk)))
        self.export_workers = max(1, int(self._workers_setting(export_workers)))
        self.headers = {"Accept": "application/json"}
        # A `requests.Session` is not thread-safe, so each pull worker gets its
        # own. An injected session (tests, callers with a transport) is shared
        # as-is: they own its lifecycle.
        self._injected_session = session is not None
        self._local = threading.local()
        self._worker_sessions: list[Any] = []
        self._sessions_lock = threading.Lock()

    # -- transport ---------------------------------------------------------

    @staticmethod
    def _chunk_setting(explicit: int | None) -> int:
        """Export chunk size: explicit arg → `STG_EXPORT_CHUNK` → 100, clamped.

        The server rejects more than `MAX_EXPORT_CHUNK` subject IDs per request
        and reports it as a 200 whose body has no `Subjects`, so an oversized
        chunk reads back as zero rows. Clamping is a correctness guard: no
        setting may exceed what the server accepts.

        A malformed env value is ignored rather than fatal (a typo must not
        break every run); an explicit arg always wins.
        """
        value = explicit
        if value is None:
            raw = os.environ.get(EXPORT_CHUNK_ENV, "").strip()
            if raw.isdigit() and int(raw) > 0:
                value = int(raw)
        if value is None:
            return DEFAULT_EXPORT_CHUNK
        return min(int(value), MAX_EXPORT_CHUNK)

    @staticmethod
    def _workers_setting(explicit: int | None) -> int:
        """Concurrent export workers: explicit arg → env → 4, clamped to 1..8."""
        value = explicit
        if value is None:
            raw = os.environ.get(EXPORT_WORKERS_ENV, "").strip()
            if raw.isdigit() and int(raw) > 0:
                value = int(raw)
        if value is None:
            return DEFAULT_EXPORT_WORKERS
        return max(1, min(int(value), MAX_EXPORT_WORKERS))

    def _session(self) -> Any:
        """Session for the calling thread (own session per worker)."""
        if self._injected_session:
            return self.session
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
            with self._sessions_lock:
                self._worker_sessions.append(session)
        return session

    def close(self) -> None:
        """Close the shared session and every per-thread worker session."""
        with self._sessions_lock:
            extras, self._worker_sessions = self._worker_sessions, []
        for session in extras:
            try:
                session.close()
            except Exception:  # noqa: BLE001 - closing must never raise
                pass
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
        retry: bool = False,
    ) -> JSON:
        url = self._url(path)
        last_exc: Exception | None = None
        attempts = self.max_retries if retry else 1
        for attempt in range(1, attempts + 1):
            try:
                r = self._session().get(
                    url,
                    params=params,
                    auth=self.auth,
                    headers=self.headers,
                    timeout=timeout if timeout is not None else self.timeout,
                )
                self._check(r)
                return r.json()
            except _RETRYABLE as e:  # pragma: no cover - timing-dependent
                last_exc = e
                if attempt < attempts:
                    time.sleep(self.backoff * attempt)
        assert last_exc is not None
        raise last_exc

    def _post(self, path: str, params: dict[str, Any] | None = None) -> JSON:
        r = self._session().post(
            self._url(path),
            params=params,
            auth=self.auth,
            headers=self.headers,
            timeout=self.timeout,
        )
        self._check(r)
        return r.json()

    @staticmethod
    def _check(r: Any) -> None:
        if r.status_code >= 400:
            raise STGError(f"HTTP {r.status_code}: {r.text[:300]}")
        if r.status_code == 200 and not r.text:
            return

    # -- stores ------------------------------------------------------------

    def get_stores(self, ids: Iterable[int | str] | None = None) -> list[dict]:
        params: dict[str, Any] = {}
        if ids is not None:
            params["storeIDs"] = ",".join(str(i) for i in ids)
        data = self._get("/SubjectStores/GetStores", params)
        return self._as_list(data)

    def get_store(self, store_id: int | str) -> list[dict]:
        data = self._get(f"/SubjectStores/GetStore/{store_id}")
        return self._as_list(data)

    def get_store_usage(self, store_id: int | str) -> list[dict]:
        data = self._get(f"/SubjectStores/GetStoreUsage/{store_id}")
        return self._as_list(data)

    def get_survey_xml(self, survey_id: str, *, timeout: int = 180) -> str:
        """Return the survey definition XML (used to derive pull terms)."""
        r = self._session().get(
            self._url(f"/Surveys/GetSurveyXML/{survey_id}"),
            params=None,
            auth=self.auth,
            headers={"Accept": "text/xml"},
            timeout=timeout,
        )
        if r.status_code >= 400:
            raise STGError(f"HTTP {r.status_code}: {r.text[:300]}")
        return r.text

    # -- survey pull -------------------------------------------------------

    def survey_interview_ids(
        self,
        survey_id: str,
        *,
        date_type: str = "Submit",
        date_start: str | None = None,
        date_end: str | None = None,
        statuses: str | None = None,
        completed: str | None = None,
        include_incomplete_subjects: bool | None = None,
        filtered: bool | None = None,
        order_by: str = "Ascending",
    ) -> list[str]:
        params: dict[str, Any] = {
            "surveyIDs": survey_id,
            "dateType": date_type,
            "orderBy": order_by,
        }
        if date_start is not None:
            params["dateStart"] = date_start
        if date_end is not None:
            params["dateEnd"] = date_end
        if statuses:
            params["statuses"] = statuses
        elif completed is not None:
            params["completed"] = completed
        if include_incomplete_subjects is not None:
            params["includeIncompleteSubjects"] = str(include_incomplete_subjects).lower()
        if filtered is not None:
            params["filtered"] = str(filtered).lower()
        data = self._get("/SurveyInterviewIDs", params, retry=True)
        return self._as_ids(data)

    def simple_export(
        self,
        survey_id: str,
        subject_ids: Sequence[str],
        *,
        variable_names: Sequence[str] | None = None,
        include_nulls: bool | None = None,
        chunk: int | None = None,
        should_stop: Callable[[], bool] | None = None,
        on_chunk: Callable[[int, int], None] | None = None,
        workers: int | None = None,
    ) -> list[dict]:
        """Pull subjects via /SimpleExport, chunked, retried, and concurrent.

        Returns the flat list of subject objects (`{SubjectID, Columns:[...]}`),
        in the caller's id order regardless of which chunk landed first.

        Chunks are independent requests, so they run on a small thread pool
        (`export_workers`, default 4): the pull is latency-bound, and one
        round-trip per 100 ids means 16 sequential waits for a 1500-interview
        survey. Retries stay per chunk.

        When `should_stop()` returns true, raise `Cancelled` before dispatching
        the next chunk (cooperative mid-pull cancellation).

        `on_chunk(done_ids, total_ids)` is called after each chunk lands, so a
        multi-minute pull can report live progress instead of looking hung; the
        counter is cumulative, so it only ever climbs.
        """
        ids = [str(s) for s in subject_ids]
        if not ids:
            return []
        size = max(1, min(int(chunk if chunk is not None else self.export_chunk),
                          MAX_EXPORT_CHUNK))
        spans = [(i, min(i + size, len(ids))) for i in range(0, len(ids), size)]
        n_workers = max(1, min(int(workers if workers is not None else self.export_workers),
                               len(spans)))

        def fetch(span: tuple[int, int]) -> list[dict]:
            start, end = span
            params: dict[str, Any] = {
                "surveyID": survey_id,
                "subjectIDs": ",".join(ids[start:end]),
            }
            if variable_names:
                params["variableNames"] = ",".join(str(v) for v in variable_names)
            if include_nulls is not None:
                params["includeNulls"] = str(include_nulls).lower()
            data = self._get("/SimpleExport", params, timeout=EXPORT_TIMEOUT, retry=True)
            return self._as_subjects(data)

        if n_workers == 1:
            out: list[dict] = []
            for span in spans:
                if should_stop and should_stop():
                    raise Cancelled()
                out.extend(fetch(span))
                if on_chunk is not None:
                    on_chunk(span[1], len(ids))
            return out

        results: dict[int, list[dict]] = {}
        done = 0
        pending = iter(enumerate(spans))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            in_flight = {}
            exhausted = False
            while True:
                while not exhausted and len(in_flight) < n_workers:
                    if should_stop and should_stop():
                        for fut in in_flight:
                            fut.cancel()
                        raise Cancelled()
                    try:
                        ix, span = next(pending)
                    except StopIteration:
                        exhausted = True
                        break
                    in_flight[pool.submit(fetch, span)] = (ix, span)
                if not in_flight:
                    break
                finished, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
                for fut in finished:
                    ix, span = in_flight.pop(fut)
                    try:
                        results[ix] = fut.result()
                    except BaseException:
                        for other in in_flight:
                            other.cancel()
                        raise
                    done += span[1] - span[0]
                    if on_chunk is not None:
                        on_chunk(done, len(ids))

        out = []
        for ix in range(len(spans)):
            out.extend(results.get(ix) or [])
        return out

    # -- writes ------------------------------------------------------------

    def add_row(self, store_id: int | str, values: dict[str, Any]) -> JSON:
        return self._post(f"/SubjectStores/AddRow/{store_id}", values)

    def update_row(self, store_id: int | str, values: dict[str, Any]) -> JSON:
        return self._post(f"/SubjectStores/UpdateRow/{store_id}", values)

    # -- shape helpers -----------------------------------------------------

    @staticmethod
    def _as_list(data: JSON) -> list[dict]:
        if data is None:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("Stores", "stores", "Subjects", "subjects", "Data", "data"):
                if isinstance(data.get(k), list):
                    return data[k]
            for v in data.values():
                if isinstance(v, list):
                    return v
        return []

    @staticmethod
    def _as_ids(data: JSON) -> list[str]:
        if isinstance(data, list):
            return [str(s) for s in data]
        if isinstance(data, dict):
            for k in ("subjects", "Subjects"):
                if isinstance(data.get(k), list):
                    return [str(s) for s in data[k]]
            for v in data.values():
                if isinstance(v, list):
                    return [str(s) for s in v]
        return []

    @staticmethod
    def _as_subjects(data: JSON) -> list[dict]:
        if data is None:
            return []
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict)]
        if isinstance(data, dict):
            for k in ("Subjects", "subjects"):
                if isinstance(data.get(k), list):
                    return [s for s in data[k] if isinstance(s, dict)]
            for v in data.values():
                if isinstance(v, list):
                    return [s for s in v if isinstance(s, dict)]
        return []
