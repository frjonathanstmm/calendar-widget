from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ICS_URL = "https://calendar.google.com/calendar/ical/c_4a5a1fc5afb51323ac2d430ac7566576eb3385682877769438f6eee2a1037f02%40group.calendar.google.com/public/basic.ics"
TIME_ZONE = ZoneInfo("Europe/London")
OUT_DIR = Path("docs")
OUT_JSON = OUT_DIR / "events.json"
OUT_INDEX = OUT_DIR / "index.html"


@dataclass
class Event:
    summary: str
    location: str
    url: str
    start_iso: str
    end_iso: str
    all_day: bool
    sort_key: str


def fetch_ics(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 calendar-widget-bot"})
    with urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return raw.decode("utf-8", errors="replace")


def unfold_ics(text: str) -> str:
    return re.sub(r"\r?\n[ \t]", "", text.replace("\r\n", "\n").replace("\r", "\n"))


def parse_ics_line(line: str):
    left, value = line.split(":", 1)
    parts = left.split(";")
    name = parts[0].upper()
    params = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.upper()] = v
    return name, value, params


def ics_unescape(value: str) -> str:
    return (
        value.replace(r"\\", "\\")
        .replace(r"\n", "\n")
        .replace(r"\N", "\n")
        .replace(r"\,", ",")
        .replace(r"\;", ";")
    )


def parse_dt(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    value = value.strip()
    is_date = params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value) is not None
    if is_date:
        dt = datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=TIME_ZONE)
        return dt, True

    tzid = params.get("TZID")
    if value.endswith("Z"):
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(TIME_ZONE)
        return dt, False

    dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if tzid:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(TIME_ZONE)
        except Exception:
            dt = dt.replace(tzinfo=TIME_ZONE)
    else:
        dt = dt.replace(tzinfo=TIME_ZONE)
    return dt, False


def parse_events(ics_text: str) -> list[Event]:
    lines = unfold_ics(ics_text).split("\n")
    raw_events = []
    current: Optional[dict] = None

    for line in lines:
        line = line.strip()
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current:
                raw_events.append(current)
            current = None
            continue
        if not current or ":" not in line:
            continue

        name, value, params = parse_ics_line(line)
        value = ics_unescape(value)

        if name in {"SUMMARY", "LOCATION", "URL"}:
            current[name.lower()] = value
        elif name in {"DTSTART", "DTEND"}:
            dt, all_day = parse_dt(value, params)
            current[name.lower()] = dt
            current[f"{name.lower()}_all_day"] = all_day

    now = datetime.now(TIME_ZONE)
    upper = now + timedelta(days=120)
    events: list[Event] = []

    for item in raw_events:
        start = item.get("dtstart")
        if not isinstance(start, datetime):
            continue
        if start < now or start > upper:
            continue

        end = item.get("dtend")
        all_day = bool(item.get("dtstart_all_day"))
        if not isinstance(end, datetime):
            end = start + (timedelta(days=1) if all_day else timedelta(hours=1))

        events.append(
            Event(
                summary=(item.get("summary") or "Untitled event").strip(),
                location=(item.get("location") or "").strip(),
                url=(item.get("url") or "").strip(),
                start_iso=start.isoformat(),
                end_iso=end.isoformat(),
                all_day=all_day,
                sort_key=start.isoformat(),
            )
        )

    events.sort(key=lambda e: e.sort_key)
    return events[:20]


def build_index() -> str:
    return """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Calendar widget</title>
  <style>
    body{font-family:Georgia,'Times New Roman',serif;max-width:760px;margin:3rem auto;padding:0 1rem;color:#111;}
    h1{font-size:1.6rem;font-weight:500;}
    p{color:rgba(17,17,17,.72);line-height:1.6;}
    code{background:rgba(17,17,17,.06);padding:.1rem .3rem;border-radius:.25rem;}
  </style>
</head>
<body>
  <h1>Calendar widget</h1>
  <p>This repository publishes <code>events.json</code> and <code>widget.js</code> for Squarespace.</p>
  <div id=\"calendar-widget\"></div>
  <script src=\"./widget.js\"></script>
</body>
</html>
"""


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ics = fetch_ics(ICS_URL)
    events = parse_events(ics)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "time_zone": "Europe/London",
        "events": [asdict(e) for e in events],
    }
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_INDEX.write_text(build_index(), encoding="utf-8")
    print(f"Wrote {OUT_JSON} and {OUT_INDEX} with {len(events)} events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
