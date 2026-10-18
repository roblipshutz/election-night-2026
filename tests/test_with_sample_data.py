"""Runs the whole pipeline against built-in sample responses (no internet).
   python tests/test_with_sample_data.py
The numbers here are made up purely to exercise the code."""
import io, json, sys, zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import election_night, sources  # noqa: E402

CSV = """line number,contest name,choice name,party name,total votes,percent of votes,registered voters,ballots cast,num Precinct total,num Precinct rptg,over votes,under votes
1,City of Palo Alto Member City Council,Ed Lauing,,5100,,,,,,,
2,City of Palo Alto Member City Council,Vicki Veenker,,5600,,,,,,,
3,City of Palo Alto Member City Council,Bryna Chang,,4300,,,,,,,
4,City of Palo Alto Member City Council,Yu "Yudy" Deng,,3900,,,,,,,
5,City of Palo Alto Member City Council,Gary Gechlik,,2100,,,,,,,
6,City of Palo Alto Member City Council,Raymond Lee Goins,,900,,,,,,,
7,City of Palo Alto Member City Council,Brian Hamachek,,1500,,,,,,,
8,City of Palo Alto Member City Council,Henry Etzkowitz,,1200,,,,,,,
9,Palo Alto Unified School District Governing Board Member,Linda Henigin,,6100,,,,,,,
10,Palo Alto Unified School District Governing Board Member,John Craig,,4800,,,,,,,
11,Palo Alto Unified School District Governing Board Member,Avery Wang,,5300,,,,,,,
12,Santa Clara Valley Water District Director District 7,Rebecca Eisenberg,,9000,,,,,,,
13,Santa Clara Valley Water District Director District 7,Pete Dailey,,8100,,,,,,,
14,Measure J,Yes,,7200,,,,,,,
15,Measure J,No,,5900,,,,,,,
16,Measure RTM,Yes,,120000,,,,,,,
17,Measure RTM,No,,110000,,,,,,,
"""

# Other counties, for Measure RTM's five-county total. Shapes copied from the real sites.
ALAMEDA_HTML = """<html><body><div class="lastUpdated"><span>Last Updated: Tuesday, November 03, 2026; 9:15 PM</span></div>
<h3 class="racePanelHeading">Measure RTM - Public Transit Revenue Measure District</h3>
<div class="reported">412 of 627 Precincts Partially Reported</div>
<TABLE><thead><TR><th>Contest</th><th>Votes</th><th>Percentage</th></TR></thead><tbody>
<TR><TH scope="row">Yes</TH><TD>210,000</TD><TD>60.00 %</TD></TR>
<TR><TH scope="row">No</TH><TD>140,000</TD><TD>40.00 %</TD></TR></tbody></TABLE>
<h3 class="racePanelHeading">Fire Department Measure X</h3>
<TABLE><tbody><TR><TH>Tax Yes</TH><TD>1</TD><TD>50 %</TD></TR><TR><TH>Tax No</TH><TD>1</TD><TD>50 %</TD></TR></tbody></TABLE>
</body></html>"""

SF_XML = """<?xml version="1.0" encoding="utf-8"?><Report xmlns="ElectionSummaryReportRPT"><tabBatchIdList>
<Details reported="Precincts Reported: 300 of 514 (0.58)" />
<ContestIdGroup contestId="Measure RTM   "><CandidateResults><Report Name="CandidateResultsRPT"><Tablix1><chGroup_Collection>
<chGroup><candidateNameTextBox4 candidateNameTextBox4="YES "><cgGroup_Collection><cgGroup countingGroupName="Election Day" vot7="20000" /><cgGroup countingGroupName="Vote by Mail" vot7="130000" /></cgGroup_Collection><Textbox13 vot8="150000" Textbox17="0.6" /></candidateNameTextBox4></chGroup>
<chGroup><candidateNameTextBox4 candidateNameTextBox4="NO "><cgGroup_Collection><cgGroup countingGroupName="Election Day" vot7="10000" /><cgGroup countingGroupName="Vote by Mail" vot7="90000" /></cgGroup_Collection><Textbox13 vot8="100000" Textbox17="0.4" /></candidateNameTextBox4></chGroup>
</chGroup_Collection></Tablix1></Report></CandidateResults></ContestIdGroup></tabBatchIdList></Report>"""

def fake_get(url, **kw):
    r = mock.Mock(ok=True, status_code=200)
    r.raise_for_status = lambda: None
    if "alamedacountyca.gov" in url:
        r.text = ALAMEDA_HTML
    elif "sfelections.org" in url:
        r.content = SF_XML.encode("utf-8")
    elif url.endswith("current_ver.txt"):
        r.text = "350001"
    elif url.endswith("summary.zip"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("summary.csv", CSV)
        r.content = buf.getvalue()
    elif url.endswith("insurance-commissioner"):
        r.json = lambda: {"raceTitle": "Insurance Commissioner - Statewide Results",
                          "Reporting": "41.0% (12,500 of 30,482) precincts reporting",
                          "ReportingTime": "November 3, 2026, 9:42 p.m.",
                          "candidates": [{"Name": "Ben Allen", "Votes": "2,649,578"}, {"Name": "Jane Kim", "Votes": "2,824,763"}]}
    elif url.endswith("ballot-measures"):
        r.json = lambda: {"raceTitle": "Ballot Measures - Statewide Results",  # same wrapper as the live feed
                          "ballot-measures": [{"Number": f"{n:02d}", "Name": "x", "yesVotes": f"{(n*37)%100*1000+40000:,}", "noVotes": "70,000"}
                                              for n in (1, 2, 3, 4, 5, 37, 38, 39, 40, 41, 42, 43, 44, 45)]}
    else:
        raise AssertionError("unexpected url " + url)
    return r

cfg = election_night.load_config()
cfg["county"]["clarity_url"] = "https://results.enr.clarityelections.com/CA/Santa_Clara/123456/web.350000/#/summary"
with mock.patch.object(sources.requests, "get", side_effect=fake_get), \
     mock.patch.object(election_night, "MANUAL", ROOT / "tests" / "no_such_file.csv"):
    doc = election_night.refresh(cfg, verbose=True)
election_night.print_summary(doc)
missing = [(c["id"], ch["name"]) for c in doc["contests"] for ch in c["choices"] if ch["votes"] is None]
assert not missing, missing

# Measure RTM: Santa Clara + Alameda + San Francisco; Contra Costa and San Mateo have no link
# in config.json yet, so they are listed but left out of the total.
rtm = next(c for c in doc["contests"] if c["id"] == "RTM")
votes = {ch["name"]: ch["votes"] for ch in rtm["choices"]}
assert votes == {"Yes": 120000 + 210000 + 150000, "No": 110000 + 140000 + 100000}, votes
assert rtm["counties_included"] == ["Santa Clara", "Alameda", "San Francisco"], rtm["counties_included"]
assert rtm["counties_missing"] == ["Contra Costa", "San Mateo"], rtm["counties_missing"]
assert rtm["yes_pct"] == round(100 * 480000 / 830000, 1)
print("\nOK - every race and choice matched, and Measure RTM adds up across counties.")
