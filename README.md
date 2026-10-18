# Palo Alto Election Night 2026

A full-screen results board for the November 3, 2026 general election, built for projecting in the Channing House auditorium. It covers:

- Palo Alto City Council (3 seats; Veenker, Lauing, Chang and Deng shown large, the others on one line)
- Palo Alto Unified School Board (2 seats)
- Measure J (Palo Alto sales tax) and Measure RTM (regional transit tax)
- Valley Water Board, District 7
- Insurance Commissioner
- State Propositions 1–5 and 37–45

It runs entirely on your PC: a small Python program fetches the official results every 90 seconds and a web page in Edge shows them full screen.

## How it works

```
config.json            races, where their results come from, timing
app/election_night.py  fetch loop + local web server (http://localhost:8000/web/)
app/sources.py         readers for the state API, the county site, and the manual file
web/index.html         the dashboard page; re-reads data/results.json every 20 s
data/results.json      written by the fetcher (not kept in git)
data/manual_override.csv  numbers typed by hand; these win over anything fetched
tests/test_with_sample_data.py  runs everything on made-up sample numbers, offline
```

**State results** come from the California Secretary of State's public API (`https://api.sos.ca.gov/returns/...`): `insurance-commissioner` and `ballot-measures`. The state says these update about every five minutes after polls close.

**County results** come from Santa Clara County's results site, which is hosted by Clarity Elections (`results.enr.clarityelections.com/CA/Santa_Clara/...`). The fetcher reads Clarity's `summary.zip` export (a CSV), and falls back to the site's `summary.json`.

**Measure RTM** passes on a majority of all votes in five counties combined, so the board adds up five results sites (`other_counties` in `config.json`) and lists each county under the total, marking any that are not in it yet:

| County | Site | Read as | Link in config.json |
|---|---|---|---|
| Santa Clara | Clarity | `summary.zip` | `county.clarity_url` (same as the local races) |
| Contra Costa | Clarity | `summary.zip` | paste on election night |
| Alameda | alamedacountyca.gov/rovresults | the results web page | already set (Nov 3, 2026 page, id 260) |
| San Mateo | smcacre.gov | the results web page | paste on election night |
| San Francisco | sfelections.org | `data/summary.xml` | already set (`/results/20261103/`) |

A county with no link, or no votes yet, is shown as "not in total yet" and left out of the sum.

## One-time setup

1. Install Python 3.11 or newer if needed (python.org, tick "Add to PATH").
2. Double-click `setup.bat`. It creates a private `.venv` folder and installs `requests` and `truststore` (which lets Python use Windows' certificate store; without it the Clarity and state sites fail with `CERTIFICATE_VERIFY_FAILED`). If you set up before `truststore` was added, run `setup.bat` again.
3. Optional: `git init` in this folder, or open it in VS Code and let Claude Code do it.

## Test it now

```
.venv\Scripts\python tests\test_with_sample_data.py
```

That runs the whole pipeline on sample numbers (no internet) and writes `data/results.json`. Then run:

```
.venv\Scripts\python app\election_night.py
```

and open http://localhost:8000/web/. The state side is live already: the Secretary of State is posting **test data** in its feed before the election, so you will see made-up numbers for the propositions and Insurance Commissioner until election night. The county side will say "not updating" until you paste the election-night link (next section).

## Election night checklist

1. **About 7:45 p.m.:** open the county's results page from https://vote.santaclaracounty.gov (look for "Election Results"). Copy the address from the browser bar. It looks like
   `https://results.enr.clarityelections.com/CA/Santa_Clara/123456/web.345678/#/summary`
2. Paste it into `config.json` as `county.clarity_url` and save.
   **For Measure RTM**, also paste the Contra Costa and San Mateo links into `other_counties` (each entry's `_help` says where to find it), and check that the Alameda and San Francisco links still open.
3. Run `.venv\Scripts\python app\election_night.py --list-contests`. This prints every contest name the county uses, then which contest it matched for RTM in each other county. Check that the names for Palo Alto council, PAUSD, Valley Water District 7, Measure J and Measure RTM contain the words in each race's `contest_match` in `config.json` (matching ignores capitals and punctuation). Adjust if needed.
4. Run `.venv\Scripts\python app\election_night.py --once` for a one-line-per-race check. Every race should show numbers or dashes, with no "no contest matching" warnings.
5. Double-click `run_dashboard.bat`. It starts the fetcher and opens Edge full screen. Press **Alt+F4** to close the full-screen page. Close the black window to stop the fetcher.

The fetcher re-reads `config.json` on every round, so you can fix a matcher without restarting.

## If a website fails

- The header shows "not updating" in red for the source that failed. The last good numbers stay on screen.
- To type numbers by hand, fill in `data/manual_override.csv` (Excel works; save as CSV):

  ```
  contest_id,choice,votes
  council,Vicki Veenker,5600
  J,Yes,7200
  J,No,5900
  ```

  `contest_id` and `choice` must match `config.json` exactly. For one county's Measure RTM numbers, use `RTM:` plus the county name, e.g. `RTM:San Mateo,Yes,81000` (a plain `RTM` row replaces the five-county total instead). Rows with blank votes are ignored. The footer says "includes hand-entered numbers" while any are in use. Empty the file (keep the header line) to go back to fetched numbers.

## Changing things

- **Refresh rate:** `poll_seconds` in `config.json` (minimum 30). The page re-reads every 20 seconds (`REFRESH_MS` in `web/index.html`).
- **Which council candidates are large:** `"focus": true/false` on each choice in `config.json`.
- **Look:** colors and sizes are CSS variables at the top of `web/index.html`. Fonts are Segoe UI and Georgia, which every Windows PC has, so nothing loads from the internet.
- **Another election:** edit `contests` in `config.json`. County races use `"source": "county"` with `contest_match`; propositions use `"state_prop"`; statewide candidate races use `"state_endpoint"` (the path after `/returns/`).

## Notes

- Shares are computed from votes within each race, so they can differ by a tenth of a point from what the county shows (the county includes write-ins and, for multi-seat races, divides differently).
- Measure RTM is the five-county total. Each county's site updates on its own schedule, so the total mixes counts from slightly different times.
- Nothing here needs an account or key; the program only reads public pages.
