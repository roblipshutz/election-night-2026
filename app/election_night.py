"""Palo Alto Election Night dashboard - fetcher + local web server.

Run:  python app/election_night.py               (fetch every poll_seconds and serve the page)
      python app/election_night.py --once        (fetch once, print a summary, exit)
      python app/election_night.py --list-contests  (print every county contest name)
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sources

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
DATA = ROOT / "data"
RESULTS = DATA / "results.json"
MANUAL = DATA / "manual_override.csv"


def load_config() -> dict:
    with CONFIG.open(encoding="utf-8") as f:
        return json.load(f)


def standings(contest: dict) -> None:
    """Adds pct and status to each choice, and status/yes_pct to the contest, in place."""
    choices = contest["choices"]
    total = sum(c["votes"] or 0 for c in choices)
    counted = total > 0
    for c in choices:
        c["pct"] = round(100 * (c["votes"] or 0) / total, 1) if counted else None
    if contest.get("threshold") is not None:  # a Yes/No measure
        yes = next((c["votes"] or 0 for c in choices if c["name"] == "Yes"), 0)
        contest["yes_pct"] = round(100 * yes / total, 1) if counted else None
        passing = counted and yes / total > contest["threshold"]
        contest["status"] = "Awaiting" if not counted else ("Passing" if passing else "Failing")
        for c in choices:
            c["status"] = contest["status"] if c["name"] == "Yes" else ""
    else:  # candidates: top N seats are leading
        ranked = sorted(choices, key=lambda c: c["votes"] or 0, reverse=True)
        for i, c in enumerate(ranked):
            c["status"] = "Awaiting" if not counted else ("Leading" if i < contest.get("seats", 1) else "Trailing")
        contest["status"] = "Awaiting" if not counted else "Counting"


def combine_counties(c: dict, home: tuple, others: dict, manual: dict, previous: dict, verbose: bool) -> list[dict]:
    """For a contest with also_counties (Measure RTM): one entry per county with its own votes.
    home is (name, contests, status) for the main county; others maps name -> (entry, contests, status).
    A county whose site fails keeps its last good numbers; one with no numbers is left out of the total."""
    rows = [home] + [(n, *others[n][1:]) if n in others else (n, {}, None) for n in c["also_counties"]]
    out = []
    for name, contests, status in rows:
        match = c["contest_match"]
        if name in others:
            match = others[name][0].get("contest_match", {}).get(c["id"], match)
        found = sources.find_contest(contests, match) if contests else None
        if contests and not found and verbose:
            print(f"  ! {name} has no contest matching {match} (for {c['id']})")
        votes, stale = {}, False
        for ch in c["choices"]:
            v = sources.match_choice(ch["name"], found.votes) if found else None
            if v is None and not (status and status.ok):
                v = previous.get((c["id"], name, ch["name"]))
                stale = stale or v is not None
            key = (f"{c['id']}:{name}", ch["name"])
            if key in manual:
                v = manual[key]
            votes[ch["name"]] = v
        has = sum(v or 0 for v in votes.values()) > 0  # a zero report is not "in" yet
        out.append({
            "name": name,
            "votes": votes,
            "included": has,
            "ok": bool(status and status.ok),
            "stale": stale,
            "label": (status.label if status else "") or "",
            "error": (status.error if status else f"{name} is not set up in config.json other_counties"),
        })
    return out


def refresh(cfg: dict, verbose: bool = False) -> dict:
    contests_cfg = cfg["contests"]
    state_eps = sorted({c["state_endpoint"] for c in contests_cfg if c.get("state_endpoint")})

    county, county_status = sources.fetch_county(cfg["county"].get("clarity_url", ""))
    st_cands, st_props, state_status = sources.fetch_state(cfg["state"]["api_base"], state_eps)
    manual = sources.read_manual(MANUAL)

    # Other counties are only fetched if some contest asks for them (Measure RTM).
    wanted = {n for c in contests_cfg for n in c.get("also_counties", [])}
    others = {}
    for entry in cfg.get("other_counties", []):
        if entry.get("name") in wanted:
            others[entry["name"]] = (entry, *sources.fetch_other_county(entry))
    home_name = cfg["county"].get("name", "County").replace(" County", "")

    # Keep last good numbers if a source fails this round.
    previous = {}
    if RESULTS.exists():
        try:
            for c in json.loads(RESULTS.read_text(encoding="utf-8")).get("contests", []):
                for ch in c["choices"]:
                    previous[(c["id"], ch["name"])] = ch.get("votes")
                for bc in c.get("by_county") or []:
                    for name, v in bc["votes"].items():
                        previous[(c["id"], bc["name"], name)] = v
        except Exception:
            pass

    out_contests = []
    for c in contests_cfg:
        reported = None
        failed = False
        by_county = None
        if c["source"] == "county" and c.get("also_counties"):
            by_county = combine_counties(c, (home_name, county, county_status), others, manual, previous, verbose)
            inc = [bc for bc in by_county if bc["included"]]
            reported = {ch["name"]: sum(bc["votes"][ch["name"]] or 0 for bc in inc) for ch in c["choices"]} if inc else None
        elif c["source"] == "county":
            found = sources.find_contest(county, c["contest_match"]) if county else None
            reported = found.votes if found else None
            failed = not county_status.ok
            if county and not found and verbose:
                print(f"  ! county has no contest matching {c['contest_match']} (for {c['id']})")
        elif c.get("state_prop"):
            found = st_props.get(c["state_prop"])
            reported = found.votes if found else None
            failed = not state_status.ok
        elif c.get("state_endpoint"):
            found = st_cands.get(c["state_endpoint"])
            reported = found.votes if found else None
            failed = not state_status.ok

        choices = []
        for ch in c["choices"]:
            v = sources.match_choice(ch["name"], reported) if reported else None
            if v is None and failed:
                v = previous.get((c["id"], ch["name"]))
            if (c["id"], ch["name"]) in manual:
                v = manual[(c["id"], ch["name"])]
            choices.append({**ch, "votes": v})
        contest = {k: c.get(k) for k in ("id", "title", "group", "short", "seats", "threshold", "source")}
        contest["choices"] = choices
        standings(contest)
        if by_county is not None:
            for bc in by_county:
                total = sum(v or 0 for v in bc["votes"].values())
                bc["yes_pct"] = round(100 * (bc["votes"].get("Yes") or 0) / total, 1) if total else None
            contest["by_county"] = by_county
            contest["counties_included"] = [bc["name"] for bc in by_county if bc["included"]]
            contest["counties_missing"] = [bc["name"] for bc in by_county if not bc["included"]]
        out_contests.append(contest)

    doc = {
        "title": cfg.get("title"),
        "subtitle": cfg.get("subtitle"),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "manual_override_rows": len(manual),
        "sources": {
            "county": vars(county_status),
            "state": vars(state_status),
            **{f"county:{n}": vars(st) for n, (_, _, st) in others.items()},
        },
        "contests": out_contests,
    }
    DATA.mkdir(exist_ok=True)
    tmp = RESULTS.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, RESULTS)
    return doc


def print_summary(doc: dict) -> None:
    for name, s in doc["sources"].items():
        print(f"{name:>6}: {'OK ' if s['ok'] else 'ERR'} {s.get('label') or ''} {s.get('error') or ''}")
    for c in doc["contests"]:
        bits = ", ".join(f"{ch['name']} {ch['votes'] if ch['votes'] is not None else '-'}" for ch in c["choices"])
        print(f"  {c['title']:<34} [{c['status']}] {bits}")
        for bc in c.get("by_county") or []:
            v = ", ".join(f"{k} {n if n is not None else '-'}" for k, n in bc["votes"].items())
            note = "" if bc["included"] else "  (not in total yet)"
            print(f"      {bc['name']:<30} {v}{' (last good numbers)' if bc['stale'] else ''}{note}")


def poll_forever(cfg: dict) -> None:
    while True:
        try:
            doc = refresh(cfg)
            s = doc["sources"]
            print(f"{datetime.now():%H:%M:%S} refreshed - county {'ok' if s['county']['ok'] else 'ERROR'}, "
                  f"state {'ok' if s['state']['ok'] else 'ERROR'}")
            for k in s:
                if s[k].get("error"):
                    print("   ", s[k]["error"])
        except Exception as e:
            print(f"{datetime.now():%H:%M:%S} refresh failed: {e}")
        time.sleep(max(30, int(cfg.get("poll_seconds", 90))))
        cfg = load_config()  # pick up edits to config.json without restarting


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):  # keep the console readable
        pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="fetch once, print a summary and exit")
    ap.add_argument("--list-contests", action="store_true", help="print all county contest names and exit")
    args = ap.parse_args()
    cfg = load_config()

    if args.list_contests:
        county, st = sources.fetch_county(cfg["county"].get("clarity_url", ""))
        if not st.ok:
            print(st.error)
        for title in county:
            print(title)
        # Other counties: only the contests that look like what we match on, e.g. RTM.
        for c in cfg["contests"]:
            for entry in cfg.get("other_counties", []):
                if entry.get("name") not in c.get("also_counties", []):
                    continue
                contests, st = sources.fetch_other_county(entry)
                match = entry.get("contest_match", {}).get(c["id"], c["contest_match"])
                print(f"\n{entry['name']} (looking for {match}):")
                if not st.ok:
                    print("  ", st.error)
                    continue
                found = sources.find_contest(contests, match)
                print("   matched:", found.title if found else "NOTHING - adjust contest_match")
        return
    if args.once:
        print_summary(refresh(cfg, verbose=True))
        return

    port = int(cfg.get("port", 8000))
    threading.Thread(target=poll_forever, args=(cfg,), daemon=True).start()
    handler = functools.partial(Handler, directory=str(ROOT))
    print(f"Dashboard: http://localhost:{port}/web/   (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


if __name__ == "__main__":
    main()
