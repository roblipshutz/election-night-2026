# To do

## Before election night
- [ ] Dry run against Santa Clara's live June 2 primary results: put
      `https://results.enr.clarityelections.com/CA/Santa_Clara/126487/`
      in `config.json` → `county.clarity_url`, run `app\election_night.py --list-contests` and `--once`
      (local races will show dashes; the point is that the county side says OK), then put the placeholder back.
- [ ] Rehearse on the auditorium projector (full screen, check everything fits and reads from the back).
- [ ] Optional: add once-a-day updates to the online dashboard for the week after the election.

## Election night (see README checklist)
- [ ] ~7:45 p.m.: paste the Santa Clara results link into `config.json` → `county.clarity_url`
      (from vote.santaclaracounty.gov/view-election-results, once it shows Nov 3).
- [ ] Paste the Contra Costa and San Mateo links into `config.json` → `other_counties` for RTM.
- [ ] Run `--list-contests` and `--once`; check every race matches.
- [ ] Start `run_dashboard.bat`.
- [ ] Keep Decision Desk HQ's Santa Clara page open in a second tab as a backup:
      https://votes.decisiondeskhq.com/elections/2026-11-03/california/santa-clara
