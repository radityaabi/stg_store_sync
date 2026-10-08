"""STG REST client: Subject Stores + survey pull.

Thin, transport-injectable wrapper over the SurveyToGo REST API. Every method
returns parsed JSON. Retries apply to GETs that STG is known to drop mid-stream
(`/SimpleExport`). Write semantics (Fase 0):
  - `AddRow` / `UpdateRow` send column values as query params; every column must
    be present (omitted columns are reset to their default).
  - `DeleteRow` takes the id/tag value as a URL path segment.
  - `DeleteRow` returns `{Done: true}` even when nothing matched — callers must
    verify by re-reading the store.
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Sequence
from urllib.parse import quote

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


class STGError(RuntimeError):
    """A non-2xx response from the STG API (or a malformed one)."""


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
        export_chunk: int = 100,
    ):
        if not server:
            raise ValueError("server is required")
        self.base_url = server.rstrip("/")
        self.auth = HTTPBasicAuth((user or "").strip(), (password or "").strip())
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.backoff = backoff
        self.export_chunk = max(1, int(export_chunk))
        self.headers = {"Accept": "application/json"}

    # -- transport ---------------------------------------------------------

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
                r = self.session.get(
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
        r = self.session.post(
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
        r = self.session.get(
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
    ) -> list[dict]:
        """Pull subjects via /SimpleExport, chunked and retried per chunk.

        Returns the flat list of subject objects (`{SubjectID, Columns:[...]}`).
        """
        ids = [str(s) for s in subject_ids]
        if not ids:
            return []
        size = max(1, int(chunk if chunk is not None else self.export_chunk))
        out: list[dict] = []
        for i in range(0, len(ids), size):
            params: dict[str, Any] = {
                "surveyID": survey_id,
                "subjectIDs": ",".join(ids[i : i + size]),
            }
            if variable_names:
                params["variableNames"] = ",".join(str(v) for v in variable_names)
            if include_nulls is not None:
                params["includeNulls"] = str(include_nulls).lower()
            data = self._get("/SimpleExport", params, timeout=EXPORT_TIMEOUT, retry=True)
            out.extend(self._as_subjects(data))
        return out

    # -- writes ------------------------------------------------------------

    def add_row(self, store_id: int | str, values: dict[str, Any]) -> JSON:
        return self._post(f"/SubjectStores/AddRow/{store_id}", values)

    def update_row(self, store_id: int | str, values: dict[str, Any]) -> JSON:
        return self._post(f"/SubjectStores/UpdateRow/{store_id}", values)

    def delete_row(self, store_id: int | str, tag: Any) -> JSON:
        return self._post(f"/SubjectStores/DeleteRow/{store_id}/{quote(str(tag), safe='')}")

    def delete_all_rows(self, store_id: int | str) -> JSON:
        return self._post(f"/SubjectStores/DeleteAllRows/{store_id}")

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
