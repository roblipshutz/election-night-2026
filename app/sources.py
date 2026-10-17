"""Fetch and parse official results.

Two sources:
  * State  - California Secretary of State JSON API (api.sos.ca.gov/returns/...)
  * County - Santa Clara County's results site, hosted by Clarity Elections
             (results.enr.clarityelections.com/CA/Santa_Clara/...)

Plus an optional hand-typed override file (data/manual_override.csv) that wins
over anything fetched, for when a website is down or changes format.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests

HEADERS = {"User-Agent": "PaloAltoElectionNight/1.0 (Channing House community display)"}
TIMEOUT = 20


def _num(text) -> int | None:
    """'1,234' -> 1234. Returns None for blanks."""
    if text is None:
        return None
    s = str(text).replace(",", "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9 ]+", " ", str(text).upper()).strip()


@dataclass
class ContestResult:
    """Raw votes for one contest as reported by a source."""
    title: str                                     # the source's own contest name
    votes: dict[str, int | None] = field(default_factory=dict)  # choice name -> votes


@dataclass
class SourceStatus:
    ok: bool
    as_of: str | None = None       # ISO time of the source's latest report
    label: str = ""                # human text, e.g. "23.8% of precincts reporting"
    error: str | None = None


# --------------------------------------------------------------------------
# State: California Secretary of State
# --------------------------------------------------------------------------

def _parse_sos_time(text: str | None) -> str | None:
    """'November 3, 2026, 9:14 p.m.' -> ISO string (Pacific, naive)."""
    if not text:
        return None
    t = text.replace("a.m.", "AM").replace("p.m.", "PM").replace("noon", "12:00 PM")
    for fmt in ("%B %d, %Y, %I:%M %p", "%B %d, %Y, %I %p"):
        try:
            return datetime.strptime(t, fmt).isoformat()
        except ValueError:
            continue
    return None


def fetch_state(api_base: str, endpoints: list[str]) -> tuple[dict[str, ContestResult], dict, SourceStatus]:
    """Returns (candidate contests by endpoint, propositions by number, status)."""
    candidates: dict[str, ContestResult] = {}
    props: dict[str, ContestResult] = {}
    status = SourceStatus(ok=True)
    try:
        for ep in endpoints:
            r = requests.get(f"{api_base}/{ep}", headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            race = data[0] if isinstance(data, list) else data
            # For list answers (county + statewide), prefer the statewide entry.
            if isinstance(data, list):
                race = next((d for d in data if "Statewide" in d.get("raceTitle", "")), data[0])
            cr = ContestResult(title=race.get("raceTitle", ep))
            for c in race.get("candidates", []):
                cr.votes[c.get("Name", "")] = _num(c.get("Votes"))
            candidates[ep] = cr
            status.as_of = _parse_sos_time(race.get("ReportingTime")) or status.as_of
            status.label = race.get("Reporting", "")

        r = requests.get(f"{api_base}/ballot-measures", params={"f": "json"}, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        for m in r.json():
            num = str(m.get("Number", "")).lstrip("0")
            props[num] = ContestResult(
                title=f"Prop {num}: {m.get('Name', '')}",
                votes={"Yes": _num(m.get("yesVotes")), "No": _num(m.get("noVotes"))},
            )
    except Exception as e:  # network, HTTP or format problem
        status = SourceStatus(ok=False, error=f"State results: {e}")
    return candidates, props, status


# --------------------------------------------------------------------------
# County: Clarity Elections (Santa Clara County)
# --------------------------------------------------------------------------

CLARITY_RE = re.compile(r"(https?://results\.enr\.clarityelections\.com/[A-Z]{2}/[^/]+)/(\d+)(?:/(?:web\.)?(\d+))?", re.I)


def _clarity_parts(url: str):
    m = CLARITY_RE.search(url or "")
    if not m:
        raise ValueError(
            "config.json county.clarity_url is not a Clarity results link yet. "
            "Paste the county's results page address there (see README)."
        )
    return m.group(1), m.group(2), m.group(3)


def _clarity_version(base: str, eid: str, fallback: str | None) -> str:
    try:
        r = requests.get(f"{base}/{eid}/current_ver.txt", headers=HEADERS, timeout=TIMEOUT)
        if r.ok and r.text.strip().isdigit():
            return r.text.strip()
    except requests.RequestException:
        pass
    if fallback:
        return fallback
    raise ValueError("Could not find the county results version number.")


def _parse_clarity_summary_csv(text: str) -> dict[str, ContestResult]:
    """Clarity's summary.csv: one row per (contest, choice)."""
    out: dict[str, ContestResult] = {}
    reader = csv.DictReader(io.StringIO(text))
    cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}
    c_contest = cols.get("contest name")
    c_choice = cols.get("choice name")
    c_votes = cols.get("total votes")
    if not (c_contest and c_choice and c_votes):
        raise ValueError(f"Unexpected county CSV columns: {reader.fieldnames}")
    for row in reader:
        title = row[c_contest].strip()
        cr = out.setdefault(title, ContestResult(title=title))
        cr.votes[row[c_choice].strip()] = _num(row[c_votes])
    return out


def _parse_clarity_summary_json(data) -> dict[str, ContestResult]:
    """Clarity's json/en/summary.json: a list of contests with parallel arrays.
    Key names are short (C = contest, CH = choices, V = votes); handled loosely."""
    out: dict[str, ContestResult] = {}
    for c in data if isinstance(data, list) else data.get("Contests", []):
        title = c.get("C") or c.get("ContestName") or ""
        names = c.get("CH") or c.get("Choices") or []
        votes = c.get("V") or c.get("Votes") or []
        if not title or not names:
            continue
        cr = ContestResult(title=title)
        for n, v in zip(names, votes):
            cr.votes[str(n)] = _num(v)
        out[title] = cr
    return out


def fetch_county(clarity_url: str) -> tuple[dict[str, ContestResult], SourceStatus]:
    try:
        base, eid, ver_hint = _clarity_parts(clarity_url)
        ver = _clarity_version(base, eid, ver_hint)
        contests: dict[str, ContestResult] = {}
        errors = []
        # 1) summary.zip -> summary.csv (long-standing Clarity export)
        try:
            r = requests.get(f"{base}/{eid}/{ver}/reports/summary.zip", headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
                contests = _parse_clarity_summary_csv(z.read(name).decode("utf-8-sig", "replace"))
        except Exception as e:
            errors.append(f"summary.zip: {e}")
        # 2) summary.json (what the results website itself reads)
        if not contests:
            try:
                r = requests.get(f"{base}/{eid}/{ver}/json/en/summary.json", headers=HEADERS, timeout=TIMEOUT)
                r.raise_for_status()
                contests = _parse_clarity_summary_json(r.json())
            except Exception as e:
                errors.append(f"summary.json: {e}")
        if not contests:
            raise ValueError("; ".join(errors) or "no contests found")
        return contests, SourceStatus(ok=True, as_of=datetime.now().isoformat(timespec="seconds"),
                                      label=f"County results update {ver}")
    except Exception as e:
        return {}, SourceStatus(ok=False, error=f"County results: {e}")


# --------------------------------------------------------------------------
# Matching helpers
# --------------------------------------------------------------------------

def find_contest(contests: dict[str, ContestResult], must_contain: list[str]) -> ContestResult | None:
    """First contest whose name contains every phrase in must_contain (case-insensitive)."""
    wants = [_norm(w) for w in must_contain]
    for title, cr in contests.items():
        t = _norm(title)
        if all(w in t for w in wants):
            return cr
    return None


def match_choice(name: str, reported: dict[str, int | None]) -> int | None:
    """Find a configured choice in a source's choices: exact, then by last name, then Yes/No."""
    n = _norm(name)
    for k, v in reported.items():
        if _norm(k) == n:
            return v
    last = n.split()[-1] if n else ""
    for k, v in reported.items():
        if last and last in _norm(k).split():
            return v
    if n in ("YES", "NO"):
        for k, v in reported.items():
            if _norm(k).startswith(n):
                return v
    return None


# --------------------------------------------------------------------------
# Manual override
# --------------------------------------------------------------------------

def read_manual(path: Path) -> dict[tuple[str, str], int]:
    """data/manual_override.csv with columns contest_id,choice,votes. Blank votes are ignored."""
    out: dict[tuple[str, str], int] = {}
    if not path.exists():
        return out
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            v = _num(row.get("votes"))
            if v is not None and row.get("contest_id") and row.get("choice"):
                out[(row["contest_id"].strip(), row["choice"].strip())] = v
    return out
