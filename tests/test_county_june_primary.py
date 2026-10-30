"""Checks the county reader against Santa Clara County's real June 2, 2026 primary
export (tests/fixtures/santa_clara_2026-06-02_summary.zip, downloaded Oct 30, 2026
from results.enr.clarityelections.com/CA/Santa_Clara/126487). No internet needed.
   python tests/test_county_june_primary.py"""
import sys, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import sources  # noqa: E402

raw = zipfile.ZipFile(ROOT / "tests/fixtures/santa_clara_2026-06-02_summary.zip").read("summary.csv")
contests, ballots = sources._parse_clarity_summary_csv(sources._decode(raw))
print(f"{len(contests)} contests, {ballots:,} ballots cast")

b = sources.find_contest(contests, ["MEASURE B"])
assert b and b.votes["Yes"] == 13634 and b.votes["No"] == 8201, b
d5 = sources.find_contest(contests, ["SAN JOS", "DISTRICT 5"], candidates_only=True)
assert d5 and sources.match_choice("Peter Ortiz", d5.votes) == 6183
# Whole-word matching: "District 1" must not match "District 10"
sen = sources.find_contest(contests, ["STATE SENATOR", "DISTRICT 1"])
assert sen is None, sen.title
# Candidate matcher must skip measures: "Palo Alto Unified" only appears in Measure B here
assert sources.find_contest(contests, ["PALO ALTO UNIFIED"], candidates_only=True) is None
print("OK - county reader handles the real June 2026 export.")
assert "San José" in "".join(contests), "accented names should decode"
assert sources._clarity_parts("https://results.enr.clarityelections.com/CA/Santa_Clara/126487/web.345435/#/summary") == \
    ("https://results.enr.clarityelections.com/CA/Santa_Clara", "126487")
print("OK - encoding and link parsing.")
