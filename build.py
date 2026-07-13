from __future__ import annotations

import json
import os
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil.rrule import rruleset, rrulestr

ICS_URL = os.environ.get(
    "ICS_URL",
    "https://calendar.google.com/calendar/ical/c_4a5a1fc5afb51323ac2d430ac7566576eb3385682877769438f6eee2a1037f02%40group.calendar.google.com/public/basic.ics",
)
SITE_TZ = ZoneInfo(os.environ.get("SITE_TIME_ZONE", "Europe/London"))
OUTDIR = Path("dist")
EVENT_LIMIT = int(os.environ.get("EVENT_LIMIT", "20"))
EVENT_WINDOW_DAYS = int(os.environ.get("EVENT_WINDOW_DAYS", "365"))
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "1"))


@dataclass
class Event:
    summary: str
    start: datetime
    end: datetime | None
    all_day: bool = False
    location: str = ""
    url: str = ""
    uid: str = ""
    rrule: str = ""
    exdates: list[datetime] = field(default_factory=list)
    recurrence_id: datetime | None = None
    status: str = ""


def fetch_ics(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def unfold_ics(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = text.split("\n")
    lines: list[str] = []
    for line in raw_lines:
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


_prop_re = re.compile(r"^([A-Z0-9-]+)(;[^:]*)?:(.*)$")


def parse_line(line: str):
    m = _prop_re.match(line)
    if not m:
        return None
    name = m.group(1).upper()
    params_part = m.group(2) or ""
    value = m.group(3)
    params: dict[str, str] = {}
    if params_part:
        for chunk in params_part.lstrip(";").split(";"):
            if "=" in chunk:
                k, v = chunk.split("=", 1)
                params[k.upper()] = v
    return name, params, value


def unescape_ics_text(value: str) -> str:
    return (
        value.replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
    )


def parse_ics_datetime(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value[:8], "%Y%m%d").date()
        dt = datetime.combine(d, time.min, tzinfo=SITE_TZ)
        return dt, True

    if value.endswith("Z"):
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(SITE_TZ), False

    dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
    tz_name = params.get("TZID")
    if tz_name:
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        except Exception:
            dt = dt.replace(tzinfo=SITE_TZ)
    else:
        dt = dt.replace(tzinfo=SITE_TZ)
    return dt.astimezone(SITE_TZ), False


def parse_events(ics_text: str) -> list[Event]:
    lines = unfold_ics(ics_text)
    events: list[dict] = []
    current: dict | None = None

    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {"exdates": []}
            continue
        if line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        parsed = parse_line(line)
        if not parsed:
            continue
        name, params, value = parsed

        if name == "SUMMARY":
            current["summary"] = unescape_ics_text(value)
        elif name == "LOCATION":
            current["location"] = unescape_ics_text(value)
        elif name == "URL":
            current["url"] = value
        elif name == "UID":
            current["uid"] = value
        elif name == "RRULE":
            current["rrule"] = value
        elif name == "EXDATE":
            for chunk in value.split(","):
                ex_dt, _ = parse_ics_datetime(chunk, params)
                current.setdefault("exdates", []).append(ex_dt)
        elif name == "RECURRENCE-ID":
            rec_dt, _ = parse_ics_datetime(value, params)
            current["recurrence_id"] = rec_dt
        elif name == "STATUS":
            current["status"] = value
        elif name == "DTSTART":
            start_dt, all_day = parse_ics_datetime(value, params)
            current["start"] = start_dt
            current["all_day"] = all_day
        elif name == "DTEND":
            end_dt, _ = parse_ics_datetime(value, params)
            current["end"] = end_dt

    out: list[Event] = []
    for e in events:
        if "start" not in e:
            continue
        out.append(
            Event(
                summary=str(e.get("summary", "Untitled event")),
                start=e["start"],
                end=e.get("end"),
                all_day=bool(e.get("all_day", False)),
                location=str(e.get("location", "")),
                url=str(e.get("url", "")),
                uid=str(e.get("uid", "")),
                rrule=str(e.get("rrule", "")),
                exdates=list(e.get("exdates", [])),
                recurrence_id=e.get("recurrence_id"),
                status=str(e.get("status", "")),
            )
        )
    return out


def event_end(event: Event) -> datetime:
    if event.end is not None:
        return event.end
    if event.all_day:
        return event.start + timedelta(days=1)
    return event.start


def series_duration(event: Event) -> timedelta:
    if event.end is not None:
        return event.end - event.start
    if event.all_day:
        return timedelta(days=1)
    return timedelta(0)


def build_exdate_set(exdates: list[datetime]) -> set[datetime]:
    return {d.replace(microsecond=0) for d in exdates}


def expand_events(events: list[Event]) -> list[Event]:
    now = datetime.now(tz=SITE_TZ)
    window_start = now - timedelta(days=LOOKBACK_DAYS)
    window_end = now + timedelta(days=EVENT_WINDOW_DAYS)

    singles: list[Event] = []
    masters: list[Event] = []
    overrides_by_uid: dict[str, list[Event]] = defaultdict(list)

    for event in events:
        if event.status.upper() == "CANCELLED":
            continue
        if event.recurrence_id is not None:
            overrides_by_uid[event.uid].append(event)
        elif event.rrule:
            masters.append(event)
        else:
            if event_end(event) >= now:
                singles.append(event)

    for master in masters:
        if not master.start:
            continue

        duration = series_duration(master)
        exdates = build_exdate_set(master.exdates)
        override_starts = {
            o.recurrence_id.replace(microsecond=0)
            for o in overrides_by_uid.get(master.uid, [])
            if o.recurrence_id is not None and o.status.upper() != "CANCELLED"
        }
        exdates.update(override_starts)

        try:
            rule = rrulestr(f"RRULE:{master.rrule}", dtstart=master.start)
            schedule = rruleset()
            schedule.rrule(rule)
            for ex in exdates:
                schedule.exdate(ex)
            occurrences = schedule.between(window_start, window_end, inc=True)
        except Exception:
            occurrences = [master.start]

        for occ_start in occurrences:
            occ_start = occ_start.replace(microsecond=0)
            if occ_start in exdates:
                continue
            occ_end = occ_start + duration if duration else None
            if occ_end is not None and occ_end < now:
                continue
            singles.append(
                Event(
                    summary=master.summary,
                    start=occ_start,
                    end=occ_end,
                    all_day=master.all_day,
                    location=master.location,
                    url=master.url,
                    uid=master.uid,
                )
            )

        for override in overrides_by_uid.get(master.uid, []):
            if override.status.upper() == "CANCELLED":
                continue
            singles.append(override)

    singles = [e for e in singles if event_end(e) >= now]
    singles.sort(key=lambda e: e.start)
    return singles[:EVENT_LIMIT]


def to_json(events: list[Event]) -> list[dict]:
    return [
        {
            "summary": e.summary,
            "start": e.start.isoformat(),
            "end": e.end.isoformat() if e.end else None,
            "all_day": e.all_day,
            "location": e.location,
            "url": e.url,
        }
        for e in events
    ]


def widget_js() -> str:
    return r'''(function () {
  const DEFAULT_TARGET = "#calendar-widget";
  const DEFAULT_LIMIT = 20;
  const SITE_TZ = "Europe/London";

  const currentScript = document.currentScript;
  const scriptUrl = currentScript && currentScript.src ? new URL(currentScript.src) : null;
  const eventsUrl = scriptUrl ? new URL("events.json", scriptUrl) : new URL("events.json", window.location.href);

  const esc = (str) =>
    String(str).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    }[ch]));

  function formatDate(value) {
    const d = new Date(value);
    return new Intl.DateTimeFormat("en-GB", {
      weekday: "short",
      day: "2-digit",
      month: "short",
      year: "numeric",
      timeZone: SITE_TZ
    }).format(d);
  }

  function formatTime(value) {
    const d = new Date(value);
    return new Intl.DateTimeFormat("en-GB", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
      timeZone: SITE_TZ
    }).format(d);
  }

  function formatRange(event) {
    if (event.all_day) return "All day";
    if (!event.start) return "";
    if (!event.end) return formatTime(event.start);
    return `${formatTime(event.start)} – ${formatTime(event.end)}`;
  }

  function render(target, events) {
    const root = typeof target === "string" ? document.querySelector(target) : target;
    if (!root) return;

    root.classList.add("calendar-widget");
    root.innerHTML = `
      <div class="calendar-widget__head">
        <div>
          <div class="calendar-widget__kicker">Calendar</div>
          <h3 class="calendar-widget__title">Upcoming events</h3>
        </div>
        <div class="calendar-widget__status" data-calendar-status>Loading…</div>
      </div>
      <div class="calendar-widget__list" data-calendar-list aria-live="polite"></div>
    `;

    const statusEl = root.querySelector("[data-calendar-status]");
    const listEl = root.querySelector("[data-calendar-list]");

    if (!events.length) {
      listEl.innerHTML = `<div class="calendar-widget__empty">No upcoming events at the moment.</div>`;
      statusEl.textContent = "Up to date";
      return;
    }

    listEl.innerHTML = events.slice(0, DEFAULT_LIMIT).map((event) => {
      const dateLabel = event.start ? formatDate(event.start) : "";
      const timeLabel = formatRange(event);
      const title = event.summary || "Untitled event";
      const titleHtml = event.url
        ? `<a href="${esc(event.url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`
        : esc(title);
      const location = event.location ? `<div class="calendar-widget__location">${esc(event.location)}</div>` : "";

      return `
        <article class="calendar-widget__item">
          <div class="calendar-widget__meta">
            <span class="calendar-widget__date">${esc(dateLabel)}</span>
            <span class="calendar-widget__time">${esc(timeLabel)}</span>
          </div>
          <div class="calendar-widget__name">${titleHtml}${location}</div>
        </article>
      `;
    }).join("");

    statusEl.textContent = `${events.length} event${events.length === 1 ? "" : "s"}`;
  }

  function ensureStyles() {
    if (document.getElementById("calendar-widget-styles")) return;
    const style = document.createElement("style");
    style.id = "calendar-widget-styles";
    style.textContent = `
      .calendar-widget {
        --ink: #111;
        --muted: rgba(17, 17, 17, 0.68);
        --line: rgba(17, 17, 17, 0.14);
        --soft: rgba(17, 17, 17, 0.04);
        max-width: 760px;
        margin: 0 auto;
        font-family: Georgia, "Times New Roman", serif;
        color: var(--ink);
      }
      .calendar-widget__head {
        display: flex;
        justify-content: space-between;
        align-items: end;
        gap: 16px;
        margin-bottom: 14px;
      }
      .calendar-widget__kicker {
        text-transform: uppercase;
        letter-spacing: 0.18em;
        font-size: 0.72rem;
        color: var(--muted);
        margin-bottom: 6px;
      }
      .calendar-widget__title {
        margin: 0;
        font-size: 1.5rem;
        line-height: 1.15;
        font-weight: 500;
      }
      .calendar-widget__status {
        font-size: 0.86rem;
        color: var(--muted);
        white-space: nowrap;
      }
      .calendar-widget__list {
        max-height: 420px;
        overflow-y: auto;
        border-top: 1px solid var(--line);
        border-bottom: 1px solid var(--line);
      }
      .calendar-widget__item {
        display: grid;
        grid-template-columns: 150px 1fr;
        gap: 16px;
        padding: 16px 0;
        border-top: 1px solid var(--line);
      }
      .calendar-widget__item:first-child { border-top: 0; }
      .calendar-widget__meta {
        font-size: 0.92rem;
        line-height: 1.45;
        color: var(--muted);
      }
      .calendar-widget__date {
        display: block;
        font-weight: 600;
        color: var(--ink);
        margin-bottom: 2px;
      }
      .calendar-widget__time { display: block; }
      .calendar-widget__name {
        font-size: 1.06rem;
        line-height: 1.45;
        font-weight: 500;
        letter-spacing: 0.01em;
      }
      .calendar-widget__location {
        margin-top: 0.25rem;
        color: var(--muted);
        font-size: 0.95rem;
      }
      .calendar-widget__name a {
        color: inherit;
        text-decoration: none;
      }
      .calendar-widget__name a:hover {
        text-decoration: underline;
        text-underline-offset: 3px;
      }
      .calendar-widget__item:hover { background: var(--soft); }
      .calendar-widget__empty,
      .calendar-widget__error {
        padding: 16px 0;
        color: var(--muted);
        font-size: 0.95rem;
        line-height: 1.5;
      }
      @media (max-width: 640px) {
        .calendar-widget__head {
          flex-direction: column;
          align-items: start;
        }
        .calendar-widget__item {
          grid-template-columns: 1fr;
          gap: 6px;
        }
        .calendar-widget__title { font-size: 1.3rem; }
      }
    `;
    document.head.appendChild(style);
  }

  async function boot() {
    ensureStyles();
    const target = document.querySelector(DEFAULT_TARGET);
    if (!target) return;

    try {
      const res = await fetch(eventsUrl.toString(), { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      render(target, Array.isArray(data.events) ? data.events : []);
    } catch (err) {
      console.error(err);
      target.innerHTML = `<div class="calendar-widget__error">The calendar could not be loaded.</div>`;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
'''


def main() -> None:
    ics = fetch_ics(ICS_URL)
    events = expand_events(parse_events(ics))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "events.json").write_text(json.dumps({"events": to_json(events)}, indent=2), encoding="utf-8")
    (OUTDIR / "widget.js").write_text(widget_js(), encoding="utf-8")
    (OUTDIR / "index.html").write_text(
        """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Calendar Widget Preview</title>
  <style>body{margin:0;padding:40px 20px;background:#fff;color:#111;}</style>
</head>
<body>
  <div id=\"calendar-widget\"></div>
  <script src=\"./widget.js\" defer></script>
</body>
</html>
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
