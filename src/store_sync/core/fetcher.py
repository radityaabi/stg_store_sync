"""Fetcher: pull interviews, flatten EAV, derive the pull term, project columns.

Flow (spec §2.8 / §7.2 / probe F7–F10):

1. Build the survey index (`answer_var → question-level base name`) from the
   survey XML.
2. Derive pull terms from the configured source vars. A var is resolvable if it
   is a question base name or an answer of one; loop/derived vars are not.
3. Pull via `SimpleExport` with a `variableNames` filter when **every** needed
   var is resolvable — otherwise pull everything. The filter is only an
   optimisation.
4. **Verify** the response actually contains every needed var; if any is
   missing, fall back to a full export.
5. Flatten EAV (`{Var, Value}` per subject) to `{Var: Value}`; a var absent from
   a subject simply is not in the dict (projection turns that into `""`).
6. Optionally project to the configured `source_var → store_col` columns.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable

from store_sync.core.binding import project
from store_sync.core.config import ColumnBinding


@dataclass
class PullResult:
    records: list[dict] = field(default_factory=list)
    projected: list[dict] = field(default_factory=list)
    pull_terms: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    fell_back: bool = False


# --- survey index -----------------------------------------------------------


def parse_survey_index(xml: str | bytes) -> dict[str, str]:
    """Map every source var to its question-level base name.

    Questions are `SQ` blocks (`QuestionID` + `VariableName`); answers are `STo`
    blocks referencing a `QuestionID`. Grid/matrix answers use `SAN` blocks
    instead (a real survey emits tens of thousands), so both are read. An
    "other, specify" text var (`OtherSpecVarName`, e.g. `Q2_2_TEXT`) is
    exportable and maps to its owning question's base name. Returns
    `{var: base}` for answer vars, other-specify vars, and the question base
    names themselves (`Q32 → Q32`).
    """
    if isinstance(xml, bytes):
        xml = xml.decode("utf-8", errors="replace")
    q_var: dict[str, str] = {}
    answer_pairs: list[tuple[str, str]] = []
    other_pairs: list[tuple[str, str]] = []

    stack: list[dict] = []
    for event, elem in ET.iterparse(_iter(xml), events=("start", "end")):
        if event == "start":
            if elem.tag in ("SQ", "STo", "SAN"):
                stack.append({"tag": elem.tag, "qid": None, "var": None, "other": None})
        else:
            if stack:
                if elem.tag == "QuestionID":
                    stack[-1]["qid"] = (elem.text or "").strip()
                elif elem.tag == "VariableName":
                    stack[-1]["var"] = (elem.text or "").strip()
                elif elem.tag == "OtherSpecVarName":
                    stack[-1]["other"] = (elem.text or "").strip()
            if elem.tag in ("SQ", "STo", "SAN") and stack:
                ctx = stack.pop()
                if ctx["tag"] == "SQ":
                    if ctx["qid"] and ctx["var"]:
                        q_var[ctx["qid"]] = ctx["var"]
                    if ctx["other"] and ctx["var"]:
                        other_pairs.append((ctx["other"], ctx["var"]))
                elif ctx["qid"] and ctx["var"]:
                    answer_pairs.append((ctx["qid"], ctx["var"]))
                    if ctx["other"]:
                        other_pairs.append((ctx["other"], ctx["var"]))

    index: dict[str, str] = {base: base for base in set(q_var.values())}
    for qid, av in answer_pairs:
        base = q_var.get(qid)
        if base:
            index[av] = base
        else:
            index.setdefault(av, av)
    for other, owner in other_pairs:
        index.setdefault(other, index.get(owner, owner))
    return index


def _iter(xml: str) -> Iterable:
    """Yield the XML as a byte stream for iterparse (keeps memory bounded)."""
    import io

    return io.BytesIO(xml.encode("utf-8"))


# --- derivation -------------------------------------------------------------


def derive_pull_terms(
    source_vars: Iterable[str],
    index: dict[str, str],
    *,
    match_var: str | None = None,
) -> tuple[list[str], list[str]]:
    """Return `(terms, unresolved)` for the requested source vars.

    `terms` is the ordered, de-duplicated set of base names; `unresolved` lists
    the vars with no safe base name.
    """
    terms: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        if term and term not in seen:
            seen.add(term)
            terms.append(term)

    if match_var:
        add(index.get(match_var, match_var))

    for var in source_vars:
        base = index.get(var)
        if base:
            add(base)
        else:
            unresolved.append(var)

    return terms, unresolved


def pair_columns(
    source_vars: Iterable[str], index: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Per-column pull terms from a cached index; unresolved vars reported.

    `pull_term` is `index[var]` when known, else `""` — never a guessed term.
    """
    terms: dict[str, str] = {}
    unresolved: list[str] = []
    for var in source_vars:
        base = index.get(var, "")
        terms[var] = base
        if not base:
            unresolved.append(var)
    return terms, unresolved


# --- flatten ----------------------------------------------------------------


def flatten_subject(subject: dict) -> dict:
    """EAV subject → flat `{Var: Value}`."""
    out: dict = {}
    for col in subject.get("Columns", []):
        var = col.get("Var")
        if var is not None:
            out[var] = col.get("Value")
    return out


# --- pull -------------------------------------------------------------------


def _base_names(records: list[dict], index: dict[str, str]) -> set[str]:
    """Union of question-level base names across every returned record."""
    bases: set[str] = set()
    for rec in records:
        for var in rec:
            bases.add(index.get(var, var))
    return bases


def pull(
    client,
    survey_id: str,
    subject_ids: list[str],
    *,
    source_vars: list[str],
    match_var: str | None = None,
    survey_index: dict[str, str] | None = None,
    columns: list[ColumnBinding] | None = None,
    **export_kwargs,
) -> PullResult:
    """Pull, flatten, optionally project. See module docstring for the steps."""
    index = survey_index or {}
    res = PullResult()

    terms, unresolved = derive_pull_terms(source_vars, index, match_var=match_var)
    res.pull_terms = terms
    res.unresolved = unresolved

    # A filter is only safe when every needed var has a base-name term.
    use_filter = bool(source_vars) and not unresolved
    if not use_filter and source_vars:
        # Could not build a safe filter → full export (correct, just heavier).
        res.fell_back = True

    subjects = client.simple_export(
        survey_id,
        subject_ids,
        variable_names=(terms if use_filter else None),
        **export_kwargs,
    )
    records = [flatten_subject(s) for s in subjects]

    # Verify the filter kept every needed term's family. Presence is checked at
    # the base-name level over the whole pull — a var absent from some subjects
    # is normal skip logic (probe F7), not a filter failure.
    if use_filter:
        needed_bases = set(terms)
        if not needed_bases.issubset(_base_names(records, index)):
            subjects = client.simple_export(
                survey_id, subject_ids, variable_names=None, **export_kwargs
            )
            records = [flatten_subject(s) for s in subjects]
            res.fell_back = True

    res.records = records
    if columns:
        res.projected = [project(columns, rec) for rec in records]
    return res
