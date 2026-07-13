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

try:
    from dateutil.rrule import rruleset, rrulestr
except Exception:  # pragma: no cover
    rruleset = None
    rrulestr = None

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
    description: str = ""
    start: datetime = datetime.now(tz=SITE_TZ)
    end: datetime | None = None
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
    description: str
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


_text_unescape_re = re.compile(r"\\([nN\\;,])")


def unescape_ics_text(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        ch = match.group(1)
        if ch in ("n", "N"):
            return "\n"
        if ch == "\\":
            return "\\"
        if ch == ";":
            return ";"
        if ch == ",":
            return ","
        return ch

    return _text_unescape_re.sub(repl, value)


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
        part = part.strip()
        if not part:
            continue
        dt, _ = parse_ics_datetime(part, params)
        out.append(dt)
    return out


def parse_rrule(rrule: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for chunk in rrule.split(";"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            out[k.upper()] = v
    return out


def parse_byday_tokens(value: str | None) -> list[tuple[int | None, int]]:
    if not value:
        return []
    out: list[tuple[int | None, int]] = []
    for token in value.split(","):
        token = token.strip().upper()
        m = re.fullmatch(r"([+-]?\d+)?(MO|TU|WE|TH|FR|SA|SU)", token)
        if not m:
            continue
        ordinal = int(m.group(1)) if m.group(1) else None
        weekday = WEEKDAY_MAP[m.group(2)]
        out.append((ordinal, weekday))
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


def nth_weekday_of_month(year: int, month: int, weekday: int, ordinal: int) -> date | None:
    if ordinal == 0:
        return None

    if ordinal > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        day = 1 + offset + (ordinal - 1) * 7
        last_day = calendar.monthrange(year, month)[1]
        if day > last_day:
            return None
        return date(year, month, day)

    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    offset = (last.weekday() - weekday) % 7
    day = last_day - offset + (ordinal + 1) * 7
    if day < 1 or day > last_day:
        return None
    return date(year, month, day)


def all_weekday_dates_in_month(year: int, month: int, weekday: int) -> list[date]:
    days: list[date] = []
    last_day = calendar.monthrange(year, month)[1]
    for dom in range(1, last_day + 1):
        d = date(year, month, dom)
        if d.weekday() == weekday:
            days.append(d)
    return days


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
            current["summary"] = unescape_ics_text(value)
        elif name == "DESCRIPTION":
            current["description"] = unescape_ics_text(value)
        elif name == "LOCATION":
            current["location"] = unescape_ics_text(value)
        elif name == "URL":
            current["url"] = unescape_ics_text(value)
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
                description=str(e.get("description", "")),
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


def expand_starts(master: RawEvent, window_end: datetime) -> list[datetime]:
    if rrulestr is not None and master.rrule:
        try:
            rs = rruleset()
            rs.rrule(rrulestr(master.rrule, dtstart=master.start))
            for rdate in master.rdates:
                rs.rdate(rdate)
            for exdate in master.exdates:
                rs.exdate(exdate)
            starts = list(rs.between(master.start - timedelta(seconds=1), window_end, inc=True))
            if master.start not in starts and master.start < window_end:
                starts.insert(0, master.start)
            return sorted(set(starts))
        except Exception:
            pass

    # Lightweight fallback for environments without python-dateutil.
    starts = [master.start] + master.rdates
    return sorted({dt for dt in starts if dt < window_end and dt not in set(master.exdates)})


def expand_events(events: list[RawEvent]) -> list[Event]:
    now = datetime.now(tz=SITE_TZ)
    window_end = now + timedelta(days=WINDOW_DAYS)

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

    def emit(source: RawEvent, start: datetime, end: datetime | None, all_day: bool, description: str) -> None:
        final_end = end
        if final_end is None:
            final_end = start + occurrence_duration(source)
        if final_end >= now and start < window_end:
            expanded.append(
                Event(
                    summary=source.summary,
                    description=description,
                    start=start,
                    end=final_end,
                    all_day=all_day,
                    location=source.location,
                    url=source.url,
                )
            )

    for raw in singles:
        emit(raw, raw.start, raw.end, raw.all_day, raw.description)

    for series in masters.values():
        for master in series:
            duration = occurrence_duration(master)
            starts = expand_starts(master, window_end)

            for start in starts:
                if start < master.start:
                    continue
                if start >= window_end:
                    continue

                override = overrides.get(event_key(master.uid, start))
                if override is not None:
                    emit(
                        override,
                        override.start,
                        override.end or (override.start + duration),
                        override.all_day,
                        override.description or master.description,
                    )
                else:
                    emit(
                        master,
                        start,
                        start + duration if master.end is not None or master.all_day else None,
                        master.all_day,
                        master.description,
                    )

    expanded.sort(key=lambda e: e.start)
    return expanded


def to_json(events: list[Event]) -> list[dict]:
    return [
        {
            "summary": e.summary,
            "description": e.description,
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

  function fmtDateKey(value) {
    const d = new Date(value);
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: SITE_TZ,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(d);
    const map = Object.fromEntries(parts.map((p) => [p.type, p.value]));
    return `${map.year}-${map.month}-${map.day}`;
  }

  function formatDayHeading(value) {
    const d = new Date(value);
    return new Intl.DateTimeFormat("en-GB", {
      weekday: "long",
      day: "2-digit",
      month: "long",
      timeZone: SITE_TZ
    }).format(d);
  }

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
    return `${formatTime(event.start)} - ${formatTime(event.end)}`;
  }

  function normalizeText(value) {
    return String(value || "").replace(/\r\n?/g, "\n");
  }

  function linkifyEscapedText(text) {
    const urlRe = /(https?:\/\/[^\s<]+[^<.,:;"')\]\s])/g;
    return text.replace(urlRe, (url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
  }

  function renderDescription(value) {
    const raw = normalizeText(value).trim();
    if (!raw) {
      return `<p class="calendar-widget__description-empty">No description provided.</p>`;
    }

    return raw
      .split(/\n{2,}/)
      .map((paragraph) => {
        const escaped = esc(paragraph).replace(/\n/g, "<br>");
        return `<p>${linkifyEscapedText(escaped)}</p>`;
      })
      .join("");
  }

  function renderSummary(event) {
    const title = event.summary || "Untitled event";
    const location = event.location ? `<div class="calendar-widget__location">${esc(event.location)}</div>` : "";
    const hasDescription = normalizeText(event.description).trim().length > 0;

    return `
      <details class="calendar-widget__item" ${hasDescription ? "" : "open"}>
        <summary class="calendar-widget__summary">
          <div class="calendar-widget__meta">
            <span class="calendar-widget__date">${esc(formatDate(event.start))}</span>
            <span class="calendar-widget__time">${esc(formatRange(event))}</span>
          </div>
          <div class="calendar-widget__body">
            <div class="calendar-widget__title-row">
              <div class="calendar-widget__name">${esc(title)}</div>
              <span class="calendar-widget__chevron" aria-hidden="true">\u25b8</span>
            </div>
            ${location}
          </div>
        </summary>
        <div class="calendar-widget__description">
          ${renderDescription(event.description)}
        </div>
      </details>
    `;
  }

  function renderGroups(events) {
    const groups = [];
    let current = null;

    for (const event of events) {
      const key = fmtDateKey(event.start);
      if (!current || current.key !== key) {
        current = {
          key,
          label: formatDayHeading(event.start),
          items: []
        };
        groups.push(current);
      }
      current.items.push(event);
    }

    return groups.map((group) => `
      <section class="calendar-widget__day-group">
        <h4 class="calendar-widget__day-heading">${esc(group.label)}</h4>
        <div class="calendar-widget__day-items">
          ${group.items.map(renderSummary).join("")}
        </div>
      </section>
    `).join("");
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
      </div>
      <div class="calendar-widget__list" data-calendar-list aria-live="polite"></div>
    `;

    const listEl = root.querySelector("[data-calendar-list]");

    if (!events.length) {
      listEl.innerHTML = `<div class="calendar-widget__empty">No upcoming events at the moment.</div>`;
      return;
    }

    listEl.innerHTML = renderGroups(events);
  }

  function ensureStyles() {
    if (document.getElementById("calendar-widget-styles")) return;
    const style = document.createElement("style");
    style.id = "calendar-widget-styles";
    style.textContent = `
      @import url("https://fonts.googleapis.com/css2?family=Quattrocento:wght@400;700&family=Quattrocento+Sans:wght@400;700&display=swap");

      .calendar-widget {
        --ink: #111;
        --muted: rgba(17, 17, 17, 0.68);
        --line: rgba(17, 17, 17, 0.14);
        --soft: rgba(17, 17, 17, 0.04);
        max-width: 760px;
        margin: 0 auto;
        color: var(--ink);
      }

      .calendar-widget__head {
        display: flex;
        justify-content: space-between;
        align-items: end;
        gap: 16px;
        margin-bottom: 14px;
      }

      .calendar-widget__kicker,
      .calendar-widget__title,
      .calendar-widget__date,
      .calendar-widget__time,
      .calendar-widget__day-heading {
        font-family: "Quattrocento", Georgia, serif;
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
        font-weight: 700;
      }

      .calendar-widget__list {
        border-top: 1px solid var(--line);
        border-bottom: 1px solid var(--line);
      }

      .calendar-widget__day-group + .calendar-widget__day-group {
        margin-top: 1.1rem;
      }

      .calendar-widget__day-heading {
        margin: 0 0 0.7rem 0;
        font-size: 0.95rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--muted);
      }

      .calendar-widget__day-items {
        border-top: 1px solid var(--line);
      }

      .calendar-widget__item {
        border-bottom: 1px solid var(--line);
        margin: 0;
      }

      .calendar-widget__item:hover {
        background: var(--soft);
      }

      .calendar-widget__summary {
        display: grid;
        grid-template-columns: 150px 1fr;
        gap: 16px;
        padding: 16px 0;
        list-style: none;
        cursor: pointer;
      }

      .calendar-widget__summary::-webkit-details-marker {
        display: none;
      }

      .calendar-widget__meta {
        font-size: 0.92rem;
        line-height: 1.45;
        color: var(--muted);
      }

      .calendar-widget__date {
        display: block;
        font-weight: 700;
        color: var(--ink);
        margin-bottom: 2px;
      }

      .calendar-widget__time {
        display: block;
      }

      .calendar-widget__body {
        min-width: 0;
        font-family: "Quattrocento Sans", Arial, sans-serif;
      }

      .calendar-widget__title-row {
        display: flex;
        align-items: start;
        justify-content: space-between;
        gap: 12px;
      }

      .calendar-widget__name {
        font-size: 1.06rem;
        line-height: 1.45;
        font-weight: 700;
        letter-spacing: 0.01em;
      }

      .calendar-widget__location {
        margin-top: 0.25rem;
        color: var(--muted);
        font-size: 0.92rem;
      }

      .calendar-widget__chevron {
        flex: 0 0 auto;
        font-size: 1rem;
        line-height: 1;
        color: var(--muted);
        transition: transform 160ms ease;
        transform-origin: 50% 50%;
        margin-top: 0.2rem;
      }

      .calendar-widget__item[open] .calendar-widget__chevron {
        transform: rotate(90deg);
      }

      .calendar-widget__description {
        padding: 0 0 16px calc(150px + 16px);
        font-family: "Quattrocento Sans", Arial, sans-serif;
        color: var(--ink);
        font-size: 0.95rem;
        line-height: 1.6;
      }

      .calendar-widget__description p {
        margin: 0 0 0.75rem 0;
      }

      .calendar-widget__description p:last-child {
        margin-bottom: 0;
      }

      .calendar-widget__description a {
        color: inherit;
        text-decoration: underline;
        text-underline-offset: 0.15em;
      }

      .calendar-widget__description-empty {
        color: var(--muted);
        font-style: italic;
      }

      .calendar-widget__empty,
      .calendar-widget__error {
        padding: 16px 0;
        color: var(--muted);
        font-size: 0.95rem;
        line-height: 1.5;
        font-family: "Quattrocento Sans", Arial, sans-serif;
      }

      @media (max-width: 640px) {
        .calendar-widget__head {
          flex-direction: column;
          align-items: start;
        }

        .calendar-widget__summary {
          grid-template-columns: 1fr;
          gap: 6px;
        }

        .calendar-widget__description {
          padding-left: 0;
        }

        .calendar-widget__title {
          font-size: 1.3rem;
        }
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
    raw_ics = fetch_ics(ICS_URL)
    events = expand_events(parse_events(raw_ics))

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
