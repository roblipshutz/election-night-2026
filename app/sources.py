"""Fetch and parse official results.

Sources:
  * State  - California Secretary of State JSON API (api.sos.ca.gov/returns/...)
  * County - Santa Clara County's results site, hosted by Clarity Elections
             (results.enr.clarityelections.com/CA/Santa_Clara/...)
  * Other counties, for Measure RTM's five-county total:
      Contra Costa  - Clarity, same reader as Santa Clara
      Alameda       - one HTML page (alamedacountyca.gov/rovresults/<id>/index.htm)
      San Mateo     - one HTML page (smcacre.gov/elections/<date>-election-results)
      San Francisco - summary.xml (sfelections.org/results/<date>/data/summary.xml)

Plus an optional hand-typed override file (data/manual_override.csv) that wins
over anything fetched, for when a website is down or changes format.
"""

from __future__ import annotations

import csv
import html
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

import requests

try:  # use Windows' certificate store; Python's own bundle fails on the Clarity site
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# San Francisco's site refuses requests that don't look like a browser.
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/141.0.0.0 Safari/537.36 PaloAltoElectionNight/1.0 (Channing House community display)"}
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
        data = r.json()
        # The live feed wraps the list: {"raceTitle": ..., "ballot-measures": [...]}
        for m in data.get("ballot-measures", []) if isinstance(data, dict) else data:
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
        title = html.unescape(row[c_contest].strip())
        cr = out.setdefault(title, ContestResult(title=title))
        cr.votes[html.unescape(row[c_choice].strip())] = _num(row[c_votes])
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


def fetch_county(clarity_url: str, name: str = "County") -> tuple[dict[str, ContestResult], SourceStatus]:
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
        return {}, SourceStatus(ok=False, error=f"{name} results: {e}")


# --------------------------------------------------------------------------
# Other counties (Measure RTM)
# --------------------------------------------------------------------------

def _no_link(url: str) -> bool:
    return not str(url or "").lower().startswith("http")


class _TableReader(HTMLParser):
    """Collects contests from a page where each contest is a heading (h1-h6)
    followed by a table whose rows are: choice name | votes | percent.
    Fits Alameda (h3 + table) and San Mateo (h5 + table)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.contests: dict[str, ContestResult] = {}
        self.text: list[str] = []     # all page text, for finding "Last Updated"
        self._heading = ""
        self._hbuf: list[str] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if re.fullmatch(r"h[1-6]", tag):
            self._hbuf = []
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if re.fullmatch(r"h[1-6]", tag) and self._hbuf is not None:
            self._heading = " ".join("".join(self._hbuf).split())
            self._hbuf = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            row, self._row = self._row, None
            if len(row) >= 2 and self._heading and "%" not in row[1] and _num(row[1]) is not None:
                cr = self.contests.setdefault(self._heading, ContestResult(title=self._heading))
                cr.votes.setdefault(row[0], _num(row[1]))

    def handle_data(self, data):
        if self._hbuf is not None:
            self._hbuf.append(data)
        if self._cell is not None:
            self._cell.append(data)
        self.text.append(data)


def fetch_html_tables(url: str, name: str) -> tuple[dict[str, ContestResult], SourceStatus]:
    try:
        if _no_link(url):
            raise ValueError("no election-night link in config.json yet")
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        p = _TableReader()
        p.feed(r.text)
        if not p.contests:
            raise ValueError("no result tables found on the page")
        text = " ".join(" ".join(p.text).split())
        m = re.search(r"Last Updated:?\s*(.{5,60}?[AP]M)", text, re.I)
        return p.contests, SourceStatus(ok=True, as_of=datetime.now().isoformat(timespec="seconds"),
                                        label=f"Last updated {m.group(1).strip()}" if m else "")
    except Exception as e:
        return {}, SourceStatus(ok=False, error=f"{name} results: {e}")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_sf_xml(data: bytes) -> tuple[dict[str, ContestResult], str]:
    """San Francisco's summary.xml, an export of its Election Summary Report.
    Each contest is a ContestIdGroup; each choice is an element with a
    candidateNameTextBox4 attribute whose Textbox13 child carries the total."""
    root = ET.fromstring(data)
    out: dict[str, ContestResult] = {}
    reported = ""
    for el in root.iter():
        if not reported:
            reported = next((" ".join(v.split()[:5]) for v in el.attrib.values()
                             if v.startswith("Precincts Reported:")), "")
        if _local(el.tag) != "ContestIdGroup" or not el.get("contestId", "").strip():
            continue
        title = " ".join(el.get("contestId").split())
        cr = ContestResult(title=title)
        for ch in el.iter():
            choice = ch.get("candidateNameTextBox4")
            if choice is None:
                continue
            total = None
            for sub in ch:
                if _local(sub.tag) == "Textbox13":
                    total = next((_num(v) for k, v in sub.attrib.items() if k.startswith("vot")), None)
            if total is None:  # fall back to adding up Election Day + Vote by Mail
                parts = [_num(v) for g in ch.iter() for k, v in g.attrib.items() if k.startswith("vot")]
                total = sum(p for p in parts if p is not None) if parts else None
            cr.votes[" ".join(choice.split())] = total
        out[title] = cr
    return out, reported


def fetch_sf_xml(url: str, name: str) -> tuple[dict[str, ContestResult], SourceStatus]:
    try:
        if _no_link(url):
            raise ValueError("no election-night link in config.json yet")
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        contests, reported = _parse_sf_xml(r.content)
        if not contests:
            raise ValueError("no contests found in summary.xml")
        return contests, SourceStatus(ok=True, as_of=datetime.now().isoformat(timespec="seconds"), label=reported)
    except Exception as e:
        return {}, SourceStatus(ok=False, error=f"{name} results: {e}")


def fetch_other_county(entry: dict) -> tuple[dict[str, ContestResult], SourceStatus]:
    """One entry of config.json other_counties: {name, format, url}."""
    fmt, url, name = entry.get("format"), entry.get("url", ""), entry.get("name", "County")
    if fmt == "clarity":
        if _no_link(url):
            return {}, SourceStatus(ok=False, error=f"{name} results: no election-night link in config.json yet")
        return fetch_county(url, name)
    if fmt == "html_tables":
        return fetch_html_tables(url, name)
    if fmt == "sf_xml":
        return fetch_sf_xml(url, name)
    return {}, SourceStatus(ok=False, error=f"{name} results: unknown format {fmt!r} in config.json")


# --------------------------------------------------------------------------
# Matching helpers
# --------------------------------------------------------------------------

def find_contest(contests: dict[str, ContestResult], must_contain: list[str]) -> ContestResult | None:
    """First contest whose name contains every phrase in must_contain as whole words
    (case-insensitive), so "RTM" does not match "Fire Department" or "Measure J" match "Measure JJ"."""
    wants = [re.compile(r"\b" + re.escape(_norm(w)) + r"\b") for w in must_contain]
    for title, cr in contests.items():
        t = _norm(title)
        if all(w.search(t) for w in wants):
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
    if n in ("YES", "NO"):  # counties write "Yes", "Tax Yes", "BONDS - YES", ...
        for k, v in reported.items():
            if _norm(k).startswith(n) or n in _norm(k).split():
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
