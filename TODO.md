# To do

## Before election night
- [ ] Run `setup.bat`, then `git init` (or ask Claude Code to set up the repo).
- [ ] Test the county reader against the Nov 2024 results: put
      `https://results.enr.clarityelections.com/CA/Santa_Clara/122582/web.345435/#/summary`
      in `config.json` → `county.clarity_url`, run `app\election_night.py --list-contests`,
      then put the placeholder back.
- [ ] Rehearse on the auditorium projector (full screen, check everything fits and reads from the back).
- [ ] **Measure RTM: show the five-county combined total.** RTM passes on a simple majority of all
      votes in Alameda, Contra Costa, San Mateo, Santa Clara and San Francisco combined; today the
      board shows Santa Clara only. Find each county's results system and download format (SF runs
      its own site), add a reader for each, sum them, and show the combined Yes % with Santa Clara
      as a sub-line. Needs one election-night link per county in `config.json`.
- [ ] Decide whether the online dashboard's hourly update should fall back to news-site numbers
      for county races (the cloud updater is blocked from the county's results site).
- [ ] Optional: add once-a-day updates to the online dashboard for the week after the election.

## Election night (see README checklist)
- [ ] ~7:45 p.m.: paste the county results link into `config.json`, run `--list-contests` and `--once`.
- [ ] Start `run_dashboard.bat`.
