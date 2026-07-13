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
    from dateutil.rrule import rruleset, rrulestr  # type: ignore
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
    start: datetime
    end: datetime | None
    all_day: bool = False
    location: str = ""
    url: str = ""
    description: str = ""
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
    description: str = ""


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


def decode_ical_text(value: str) -> str:
    return (
        value.replace("\\\\", "\\")
        .replace("\\n", "\n")
        .replace("\\N", "\n")
        .replace("\\,", ",")
        .replace("\\;", ";")
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
            current["uid"] = decode_ical_text(value)
        elif name == "SUMMARY":
            current["summary"] = decode_ical_text(value)
        elif name == "LOCATION":
            current["location"] = decode_ical_text(value)
        elif name == "URL":
            current["url"] = decode_ical_text(value)
        elif name == "DESCRIPTION":
            current["description"] = decode_ical_text(value)
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
                description=str(e.get("description", "")),
                rrule=str(e.get("rrule", "")),
                rdates=list(e.get("rdates", [])),
                exdates=list(e.get("exdates", [])),
                recurrence_id=e.get("recurrence_id"),
            )
        )
    return out


def expand_starts_with_dateutil(master: RawEvent, window_end: datetime) -> list[datetime]:
    if rrulestr is None or rruleset is None:
        return []

    try:
        rs = rruleset()
        if master.rrule:
            rs.rrule(rrulestr(master.rrule, dtstart=master.start))
        else:
            rs.rdate(master.start)
        for rdate in master.rdates:
            rs.rdate(rdate)
        for exdate in master.exdates:
            rs.exdate(exdate)
        after = master.start - timedelta(seconds=1)
        return list(rs.between(after, window_end, inc=True))
    except Exception:
        return []


def expand_starts_fallback(master: RawEvent, window_end: datetime) -> list[datetime]:
    if not master.rrule:
        starts = [master.start] + master.rdates
        exset = {d.isoformat() for d in master.exdates}
        unique = sorted({dt for dt in starts if dt < window_end and dt.isoformat() not in exset})
        return unique

    rule = parse_rrule(master.rrule)
    freq = rule.get("FREQ", "").upper()
    interval = max(int(rule.get("INTERVAL", "1") or "1"), 1)
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until = parse_until(rule.get("UNTIL"))
    bymonth = parse_int_list(rule.get("BYMONTH"))
    bymonthday = parse_int_list(rule.get("BYMONTHDAY"))
    byday = parse_byday_tokens(rule.get("BYDAY"))
    exset = {d.isoformat() for d in master.exdates}

    starts: list[datetime] = []
    added = 0

    def can_add(dt: datetime) -> bool:
        nonlocal added
        if dt < master.start:
            return False
        if dt >= window_end:
            return False
        if until and dt > until:
            return False
        if dt.isoformat() in exset:
            return False
        if bymonth and dt.month not in bymonth:
            return False
        if bymonthday and dt.day not in bymonthday:
            return False
        if count is not None and added >= count:
            return False
        return True

    if freq == "DAILY":
        current = master.start
        guard = 0
        while current < window_end and guard < 5000:
            if can_add(current):
                starts.append(current)
                added += 1
            if count is not None and added >= count:
                break
            current += timedelta(days=interval)
            guard += 1

    elif freq == "WEEKLY":
        weekdays = [weekday for ordinal, weekday in byday if ordinal is None] or [master.start.weekday()]
        anchor = master.start.date() - timedelta(days=master.start.weekday())
        week_index = 0
        guard = 0
        while guard < 2000:
            week_start = anchor + timedelta(weeks=week_index * interval)
            if datetime.combine(week_start, time.min, tzinfo=SITE_TZ) >= window_end:
                break
            for weekday in sorted(set(weekdays)):
                cand_date = week_start + timedelta(days=weekday)
                cand = combine_date_with_time(master.start, cand_date)
                if can_add(cand):
                    starts.append(cand)
                    added += 1
                    if count is not None and added >= count:
                        break
            if count is not None and added >= count:
                break
            week_index += 1
            guard += 1

    elif freq == "MONTHLY":
        month_index = 0
        guard = 0
        while guard < 500:
            base = add_months(master.start, month_index * interval)
            if base >= window_end:
                break

            candidates: list[date] = []
            year, month = base.year, base.month

            if byday:
                ordinals_present = any(ordinal is not None for ordinal, _ in byday)
                if ordinals_present:
                    for ordinal, weekday in byday:
                        if ordinal is None:
                            candidates.extend(all_weekday_dates_in_month(year, month, weekday))
                        else:
                            d = nth_weekday_of_month(year, month, weekday, ordinal)
                            if d is not None:
                                candidates.append(d)
                else:
                    for _, weekday in byday:
                        candidates.extend(all_weekday_dates_in_month(year, month, weekday))
            elif bymonthday:
                last_day = calendar.monthrange(year, month)[1]
                for dom in bymonthday:
                    if 1 <= dom <= last_day:
                        candidates.append(date(year, month, dom))
            else:
                dom = min(master.start.day, calendar.monthrange(year, month)[1])
                candidates.append(date(year, month, dom))

            for d in sorted(set(candidates)):
                cand = combine_date_with_time(master.start, d)
                if can_add(cand):
                    starts.append(cand)
                    added += 1
                    if count is not None and added >= count:
                        break

            if count is not None and added >= count:
                break
            month_index += 1
            guard += 1

    elif freq == "YEARLY":
        year_index = 0
        guard = 0
        months = bymonth or [master.start.month]
        doms = bymonthday or [master.start.day]

        while guard < 200:
            year = master.start.year + year_index * interval
            if datetime(year, 1, 1, tzinfo=SITE_TZ) >= window_end:
                break

            for month in months:
                if month < 1 or month > 12:
                    continue
                last_day = calendar.monthrange(year, month)[1]
                for dom in doms:
                    if dom < 1 or dom > last_day:
                        continue
                    cand = combine_date_with_time(master.start, date(year, month, dom))
                    if can_add(cand):
                        starts.append(cand)
                        added += 1
                        if count is not None and added >= count:
                            break
                if count is not None and added >= count:
                    break

            if count is not None and added >= count:
                break
            year_index += 1
            guard += 1

    else:
        starts.append(master.start)

    starts.extend([r for r in master.rdates if master.start <= r < window_end and r.isoformat() not in exset])

    unique: list[datetime] = []
    seen: set[str] = set()
    for dt in sorted(starts):
        key = dt.isoformat()
        if key in seen:
            continue
        seen.add(key)
        unique.append(dt)
    return unique


def expand_occurrence_starts(master: RawEvent, window_end: datetime) -> list[datetime]:
    starts = expand_starts_with_dateutil(master, window_end)
    if starts:
        return starts
    return expand_starts_fallback(master, window_end)


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

    def emit(source: RawEvent, start: datetime, end: datetime | None, all_day: bool) -> None:
        final_end = end
        if final_end is None:
            final_end = start + occurrence_duration(source)
        if final_end >= now and start < window_end:
            expanded.append(
                Event(
                    summary=source.summary,
                    start=start,
                    end=final_end,
                    all_day=all_day,
                    location=source.location,
                    url=source.url,
                    description=source.description,
                )
            )

    for raw in singles:
        emit(raw, raw.start, raw.end, raw.all_day)

    for series in masters.values():
        for master in series:
            duration = occurrence_duration(master)
            starts = expand_occurrence_starts(master, window_end)

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
                    )
                else:
                    emit(
                        master,
                        start,
                        start + duration if master.end is not None or master.all_day else None,
                        master.all_day,
                    )

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
            "description": e.description,
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
    return `${formatTime(event.start)} - ${formatTime(event.end)}`;
  }

  function linkifyText(text) {
    const urlRegex = /(https?:\/\/[^\s<]+)/g;
    return esc(text).replace(urlRegex, (url) => `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`);
  }

  function descriptionToHtml(description) {
    if (!description) return "";
    const normalized = String(description).replace(/\r\n/g, "\n").replace(/\r/g, "\n").trim();
    if (!normalized) return "";
    const paragraphs = normalized.split(/\n{2,}/);
    return paragraphs
      .map((para) => {
        const lines = para.split("\n").map((line) => linkifyText(line));
        return `<p>${lines.join("<br>")}</p>`;
      })
      .join("");
  }

  function groupByDay(events) {
    const groups = [];
    let currentKey = null;
    let currentGroup = null;

    for (const event of events) {
      const key = event.start ? event.start.slice(0, 10) : "unknown";
      if (key !== currentKey) {
        currentKey = key;
        currentGroup = { key, events: [] };
        groups.push(currentGroup);
      }
      currentGroup.events.push(event);
    }
    return groups;
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

    const groups = groupByDay(events);

    listEl.innerHTML = groups.map((group) => {
      const dayDate = new Date(group.events[0].start);
      const dayLabel = new Intl.DateTimeFormat("en-GB", {
        weekday: "long",
        day: "2-digit",
        month: "short",
        year: "numeric",
        timeZone: SITE_TZ
      }).format(dayDate);

      const items = group.events.map((event, idx) => {
        const dateLabel = event.start ? formatDate(event.start) : "";
        const timeLabel = formatRange(event);
        const title = event.summary || "Untitled event";
        const location = event.location ? `<div class="calendar-widget__location">${esc(event.location)}</div>` : "";
        const descriptionHtml = descriptionToHtml(event.description || "");
        const descriptionBlock = descriptionHtml
          ? `<div class="calendar-widget__description">${descriptionHtml}</div>`
          : `<div class="calendar-widget__description calendar-widget__description--empty"></div>`;
        const descId = `calendar-desc-${group.key.replace(/[^a-zA-Z0-9_-]/g, "")}-${idx}`;

        return `
          <article class="calendar-widget__item">
            <div class="calendar-widget__meta">
              <span class="calendar-widget__date">${esc(dateLabel)}</span>
              <span class="calendar-widget__time">${esc(timeLabel)}</span>
            </div>
            <details class="calendar-widget__details">
              <summary class="calendar-widget__summary" aria-controls="${descId}">
                <span class="calendar-widget__summary-title">${esc(title)}</span>
                <span class="calendar-widget__chevron" aria-hidden="true">▸</span>
              </summary>
              ${location}
              <div id="${descId}">
                ${descriptionBlock}
              </div>
            </details>
          </article>
        `;
      }).join("");

      return `
        <section class="calendar-widget__day-group">
          <div class="calendar-widget__day-heading">${esc(dayLabel)}</div>
          ${items}
        </section>
      `;
    }).join("");

    // Force all disclosure panels closed on load, even if the browser tries
    // to restore a previously open state.
    root.querySelectorAll(".calendar-widget__details").forEach((details) => {
      details.open = false;
    });
  }

  function ensureStyles() {
    if (document.getElementById("calendar-widget-styles")) return;
    const style = document.createElement("style");
    style.id = "calendar-widget-styles";
    style.textContent = `
      @import url('https://fonts.googleapis.com/css2?family=Quattrocento:wght@400;700&family=Quattrocento+Sans:wght@400;700&family=Lora:wght@400;700&family=Lato:wght@400;700&display=swap');

      .calendar-widget {
        --ink: #111;
        --muted: rgba(17, 17, 17, 0.68);
        --line: rgba(17, 17, 17, 0.14);
        --soft: rgba(17, 17, 17, 0.04);
        max-width: 760px;
        margin: 0 auto;
        color: var(--ink);
        font-family: "Quattrocento Sans", "Lato", sans-serif;
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
        font-family: "Quattrocento Sans", "Lato", sans-serif;
      }
      .calendar-widget__title {
        margin: 0;
        font-size: 1.5rem;
        line-height: 1.15;
        font-weight: 400;
        font-family: "Quattrocento", "Lora", serif;
      }
      .calendar-widget__list {
        max-height: 420px;
        overflow-y: auto;
        border-top: 1px solid var(--line);
        border-bottom: 1px solid var(--line);
      }
      .calendar-widget__day-group + .calendar-widget__day-group {
        border-top: 1px solid var(--line);
      }
      .calendar-widget__day-heading {
        padding: 0.95rem 0 0.6rem;
        font-family: "Quattrocento", "Lora", serif;
        font-size: 0.98rem;
        font-weight: 700;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--ink);
      }
      .calendar-widget__item {
        display: grid;
        grid-template-columns: 150px 1fr;
        gap: 16px;
        padding: 16px 0;
        border-top: 1px solid var(--line);
      }
      .calendar-widget__item:first-of-type {
        border-top: 0;
      }
      .calendar-widget__meta {
        font-family: "Quattrocento", "Lora", serif;
        font-size: 0.94rem;
        line-height: 1.45;
        color: var(--muted);
      }
      .calendar-widget__date {
        display: block;
        font-weight: 400;
        color: var(--ink);
        margin-bottom: 2px;
      }
      .calendar-widget__time {
        display: block;
        font-weight: 400;
      }
      .calendar-widget__details {
        margin: 0;
      }
      .calendar-widget__summary {
        list-style: none;
        display: inline-flex;
        flex-direction: row;
        align-items: center;
        gap: 0.35rem;
        cursor: pointer;
        font-family: "Quattrocento Sans", "Lato", sans-serif;
        font-size: 1.06rem;
        line-height: 1.45;
        font-weight: 400;
        letter-spacing: 0.01em;
        user-select: none;
      }
      .calendar-widget__summary::-webkit-details-marker {
        display: none;
      }
      .calendar-widget__summary-title {
        display: inline;
      }
      .calendar-widget__chevron {
        flex: 0 0 auto;
        transition: transform 180ms ease;
        color: var(--muted);
        transform: translateY(0);
      }
      .calendar-widget__details[open] .calendar-widget__chevron {
        transform: rotate(90deg) translateX(0);
      }
      .calendar-widget__location {
        margin-top: 0.2rem;
        color: var(--muted);
        font-family: "Quattrocento Sans", "Lato", sans-serif;
        font-size: 0.93rem;
      }
      .calendar-widget__description {
        margin-top: 0.75rem;
        font-family: "Quattrocento Sans", "Lato", sans-serif;
        font-size: 0.96rem;
        line-height: 1.58;
        color: var(--ink);
      }
      .calendar-widget__description p {
        margin: 0 0 0.8rem;
      }
      .calendar-widget__description p:last-child {
        margin-bottom: 0;
      }
      .calendar-widget__description a {
        color: inherit;
        text-decoration: underline;
        text-underline-offset: 0.15em;
      }
      .calendar-widget__summary:hover .calendar-widget__summary-title {
        text-decoration: underline;
        text-underline-offset: 0.16em;
      }
      .calendar-widget__empty,
      .calendar-widget__error {
        padding: 16px 0;
        color: var(--muted);
        font-family: "Quattrocento", "Lora", serif;
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
