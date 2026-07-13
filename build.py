from __future__ import annotations

import calendar
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ICS_URL = os.environ.get(
    "ICS_URL",
    "https://calendar.google.com/calendar/ical/c_4a5a1fc5afb51323ac2d430ac7566576eb3385682877769438f6eee2a1037f02%40group.calendar.google.com/public/basic.ics",
)
SITE_TZ = ZoneInfo(os.environ.get("SITE_TIME_ZONE", "Europe/London"))
OUTDIR = Path("dist")
WINDOW_DAYS = int(os.environ.get("EVENT_WINDOW_DAYS", "14"))

WEEKDAY_MAP = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}


@dataclass
class RawEvent:
    uid: str
    summary: str
    start: datetime
    end: datetime | None
    all_day: bool = False
    location: str = ""
    url: str = ""
    rrule: str = ""
    rdates: list[datetime] = field(default_factory=list)
    exdates: list[datetime] = field(default_factory=list)
    recurrence_id: datetime | None = None


@dataclass
class Event:
    summary: str
    start: datetime
    end: datetime | None
    all_day: bool = False
    location: str = ""
    url: str = ""


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


def parse_dt_list(value: str, params: dict[str, str]) -> list[datetime]:
    out: list[datetime] = []
    for part in value.split(","):
        dt, _ = parse_ics_datetime(part.strip(), params)
        out.append(dt)
    return out


def parse_rrule(rrule: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for chunk in rrule.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            result[k.upper()] = v
    return result


def parse_weekdays(value: str | None) -> list[int]:
    if not value:
        return []
    out: list[int] = []
    for token in value.split(","):
        token = token.strip().upper()
        m = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", token)
        if not m:
            continue
        out.append(WEEKDAY_MAP[m.group(2)])
    return out


def parse_int_list(value: str | None) -> list[int]:
    if not value:
        return []
    out: list[int] = []
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(int(token))
        except ValueError:
            pass
    return out


def parse_until(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(d, time.max, tzinfo=SITE_TZ)
    if value.endswith("Z"):
        dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(SITE_TZ)
    dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return dt.replace(tzinfo=SITE_TZ)


def add_months(dt: datetime, months: int) -> datetime:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def combine_date_with_time(base: datetime, d: date) -> datetime:
    return datetime.combine(
        d,
        time(
            base.hour,
            base.minute,
            base.second,
            base.microsecond,
            tzinfo=base.tzinfo,
        ),
    )


def occurrence_duration(event: RawEvent) -> timedelta:
    if event.end is not None:
        return event.end - event.start
    if event.all_day:
        return timedelta(days=1)
    return timedelta(0)


def event_key(uid: str, dt: datetime) -> tuple[str, str]:
    return uid, dt.isoformat()


def parse_events(ics_text: str) -> list[RawEvent]:
    lines = unfold_ics(ics_text)
    events: list[dict] = []
    current: dict | None = None

    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {"rdates": [], "exdates": []}
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

        if name == "UID":
            current["uid"] = value
        elif name == "SUMMARY":
            current["summary"] = value
        elif name == "LOCATION":
            current["location"] = value
        elif name == "URL":
            current["url"] = value
        elif name == "RRULE":
            current["rrule"] = value
        elif name == "RECURRENCE-ID":
            rid, _ = parse_ics_datetime(value, params)
            current["recurrence_id"] = rid
        elif name == "RDATE":
            current["rdates"].extend(parse_dt_list(value, params))
        elif name == "EXDATE":
            current["exdates"].extend(parse_dt_list(value, params))
        elif name == "DTSTART":
            start_dt, all_day = parse_ics_datetime(value, params)
            current["start"] = start_dt
            current["all_day"] = all_day
        elif name == "DTEND":
            end_dt, _ = parse_ics_datetime(value, params)
            current["end"] = end_dt

    out: list[RawEvent] = []
    for i, e in enumerate(events):
        if "start" not in e:
            continue
        uid = str(e.get("uid", f"missing-uid-{i}-{e['start'].isoformat()}"))
        out.append(
            RawEvent(
                uid=uid,
                summary=str(e.get("summary", "Untitled event")),
                start=e["start"],
                end=e.get("end"),
                all_day=bool(e.get("all_day", False)),
                location=str(e.get("location", "")),
                url=str(e.get("url", "")),
                rrule=str(e.get("rrule", "")),
                rdates=list(e.get("rdates", [])),
                exdates=list(e.get("exdates", [])),
                recurrence_id=e.get("recurrence_id"),
            )
        )
    return out


def generate_rrule_starts(event: RawEvent, rule_text: str, window_end: datetime) -> list[datetime]:
    rule = parse_rrule(rule_text)
    freq = rule.get("FREQ", "").upper()
    interval = max(int(rule.get("INTERVAL", "1")), 1)
    count = int(rule["COUNT"]) if "COUNT" in rule and rule["COUNT"].isdigit() else None
    until = parse_until(rule.get("UNTIL"))

    byday = parse_weekdays(rule.get("BYDAY"))
    bymonthday = parse_int_list(rule.get("BYMONTHDAY"))
    bymonth = parse_int_list(rule.get("BYMONTH"))

    starts: list[datetime] = []
    generated = 0

    def accept(dt: datetime) -> bool:
        nonlocal generated
        if dt < event.start:
            return False
        if until and dt > until:
            return False
        if count is not None and generated >= count:
            return False
        if bymonth and dt.month not in bymonth:
            return False
        if bymonthday and dt.day not in bymonthday:
            return False
        return True

    if freq == "DAILY":
        current = event.start
        while current < window_end:
            if accept(current):
                starts.append(current)
                generated += 1
            if count is not None and generated >= count:
                break
            current = current + timedelta(days=interval)

    elif freq == "WEEKLY":
        weekdays = byday or [event.start.weekday()]
        anchor = event.start.date() - timedelta(days=event.start.weekday())
        week = 0
        while True:
            week_start = anchor + timedelta(weeks=week * interval)
            for wd in sorted(set(weekdays)):
                cand = combine_date_with_time(event.start, week_start + timedelta(days=wd))
                if cand >= window_end:
                    if until and cand > until:
                        return starts
                    continue
                if accept(cand):
                    starts.append(cand)
                    generated += 1
                if count is not None and generated >= count:
                    return starts
            week += 1
            if week_start > window_end and not weekdays:
                break
            if until and week_start > until.date():
                break

    elif freq == "MONTHLY":
        month_index = 0
        doms = bymonthday or [event.start.day]
        while True:
            month_base = add_months(event.start, month_index * interval)
            if month_base > window_end:
                break
            for dom in doms:
                last_dom = calendar.monthrange(month_base.year, month_base.month)[1]
                if dom < 1 or dom > last_dom:
                    continue
                cand = combine_date_with_time(
                    event.start,
                    date(month_base.year, month_base.month, dom),
                )
                if cand >= window_end:
                    if until and cand > until:
                        return starts
                    continue
                if accept(cand):
                    starts.append(cand)
                    generated += 1
                if count is not None and generated >= count:
                    return starts
            month_index += 1
            if until and month_base > until:
                break

    elif freq == "YEARLY":
        year_index = 0
        months = bymonth or [event.start.month]
        doms = bymonthday or [event.start.day]
        while True:
            base_year = event.start.year + year_index * interval
            if datetime(base_year, 1, 1, tzinfo=SITE_TZ) > window_end:
                break
            for month_num in months:
                if month_num < 1 or month_num > 12:
                    continue
                last_dom = calendar.monthrange(base_year, month_num)[1]
                for dom in doms:
                    if dom < 1 or dom > last_dom:
                        continue
                    cand = combine_date_with_time(
                        event.start,
                        date(base_year, month_num, dom),
                    )
                    if cand >= window_end:
                        if until and cand > until:
                            return starts
                        continue
                    if accept(cand):
                        starts.append(cand)
                        generated += 1
                    if count is not None and generated >= count:
                        return starts
            year_index += 1
            if until and datetime(base_year, 12, 31, tzinfo=SITE_TZ) > until:
                break

    else:
        # Fallback for uncommon RRULEs: treat the DTSTART as the only start.
        if event.start < window_end:
            starts.append(event.start)

    # Include explicit RDATEs.
    for rdate in event.rdates:
        if rdate < event.start:
            continue
        if rdate >= window_end:
            continue
        if until and rdate > until:
            continue
        starts.append(rdate)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[datetime] = []
    for dt in starts:
        key = dt.isoformat()
        if key in seen:
            continue
        seen.add(key)
        unique.append(dt)
    unique.sort()
    return unique


def expand_events(events: list[RawEvent]) -> list[Event]:
    now = datetime.now(tz=SITE_TZ)
    cutoff = now + timedelta(days=WINDOW_DAYS)

    overrides: dict[tuple[str, str], RawEvent] = {}
    masters: dict[str, list[RawEvent]] = {}
    singles: list[RawEvent] = []

    for event in events:
        if event.recurrence_id is not None:
            overrides[event_key(event.uid, event.recurrence_id)] = event
        elif event.rrule or event.rdates:
            masters.setdefault(event.uid, []).append(event)
        else:
            singles.append(event)

    expanded: list[Event] = []

    def emit(raw: RawEvent, start: datetime, end: datetime | None, all_day: bool) -> None:
        if end is None:
            end = start + occurrence_duration(raw)
        if end >= now and start < cutoff:
            expanded.append(
                Event(
                    summary=raw.summary,
                    start=start,
                    end=end,
                    all_day=all_day,
                    location=raw.location,
                    url=raw.url,
                )
            )

    # Single events and non-recurring overrides with no master.
    for raw in singles:
        emit(raw, raw.start, raw.end, raw.all_day)

    # Recurring masters.
    for uid, series in masters.items():
        # Usually one master per UID; if there are several, process each independently.
        for master in series:
            duration = occurrence_duration(master)
            starts = generate_rrule_starts(master, master.rrule, cutoff) if master.rrule else []
            if not starts and master.rdates:
                starts = [dt for dt in master.rdates if dt >= master.start and dt < cutoff]

            if not master.rrule and not master.rdates:
                emit(master, master.start, master.end, master.all_day)
                continue

            # Add explicit start if a rule exists but DTSTART wasn't included above.
            if master.start >= master.start and master.start < cutoff:
                if master.start not in starts:
                    starts.insert(0, master.start)

            for start in starts:
                if start < master.start:
                    continue
                if start >= cutoff:
                    continue
                if start in master.exdates:
                    continue

                override = overrides.get(event_key(master.uid, start))
                if override:
                    emit(
                        override,
                        override.start,
                        override.end or (override.start + duration),
                        override.all_day,
                    )
                else:
                    emit(master, start, start + duration if master.end is not None or master.all_day else None, master.all_day)

    expanded.sort(key=lambda e: e.start)
    return expanded


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

    listEl.innerHTML = events.map((event) => {
      const dateLabel = event.start ? formatDate(event.start) : "";
      const timeLabel = formatRange(event);
      const title = event.summary || "Untitled event";
      const location = event.location ? `<div class="calendar-widget__location">${esc(event.location)}</div>` : "";
      const titleHtml = event.url
        ? `<a href="${esc(event.url)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`
        : esc(title);

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
    raw = fetch_ics(ICS_URL)
    events = expand_events(parse_events(raw))

    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "events.json").write_text(
        json.dumps({"events": to_json(events)}, indent=2),
        encoding="utf-8",
    )
    (OUTDIR / "widget.js").write_text(widget_js(), encoding="utf-8")
    (OUTDIR / "index.html").write_text(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calendar Widget Preview</title>
  <style>body{margin:0;padding:40px 20px;background:#fff;color:#111;}</style>
</head>
<body>
  <div id="calendar-widget"></div>
  <script src="./widget.js" defer></script>
</body>
</html>
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
