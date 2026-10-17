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


def refresh(cfg: dict, verbose: bool = False) -> dict:
    contests_cfg = cfg["contests"]
    state_eps = sorted({c["state_endpoint"] for c in contests_cfg if c.get("state_endpoint")})

    county, county_status = sources.fetch_county(cfg["county"].get("clarity_url", ""))
    st_cands, st_props, state_status = sources.fetch_state(cfg["state"]["api_base"], state_eps)
    manual = sources.read_manual(MANUAL)

    # Keep last good numbers if a source fails this round.
    previous = {}
    if RESULTS.exists():
        try:
            for c in json.loads(RESULTS.read_text(encoding="utf-8")).get("contests", []):
                for ch in c["choices"]:
                    previous[(c["id"], ch["name"])] = ch.get("votes")
        except Exception:
            pass

    out_contests = []
    for c in contests_cfg:
        reported = None
        failed = False
        if c["source"] == "county":
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
        out_contests.append(contest)

    doc = {
        "title": cfg.get("title"),
        "subtitle": cfg.get("subtitle"),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "manual_override_rows": len(manual),
        "sources": {
            "county": vars(county_status),
            "state": vars(state_status),
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


def poll_forever(cfg: dict) -> None:
    while True:
        try:
            doc = refresh(cfg)
            s = doc["sources"]
            print(f"{datetime.now():%H:%M:%S} refreshed - county {'ok' if s['county']['ok'] else 'ERROR'}, "
                  f"state {'ok' if s['state']['ok'] else 'ERROR'}")
            for k in ("county", "state"):
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
