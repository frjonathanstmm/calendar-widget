(() => {
  const TARGET_ID = 'calendar-widget';
  const TIME_ZONE = 'Europe/London';
  const root = document.getElementById(TARGET_ID);
  if (!root) return;

  const currentScript = document.currentScript;
  const baseUrl = currentScript?.src ? new URL('.', currentScript.src).href : window.location.href;
  const dataUrl = new URL(`events.json?ts=${Date.now()}`, baseUrl).href;

  const style = document.createElement('style');
  style.textContent = `
    .calendar-widget {
      --ink: #111;
      --muted: rgba(17, 17, 17, 0.68);
      --line: rgba(17, 17, 17, 0.14);
      --soft: rgba(17, 17, 17, 0.04);
      max-width: 760px;
      margin: 0 auto;
      font-family: Georgia, 'Times New Roman', serif;
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
    .calendar-widget__month {
      padding: 1rem 0 0.5rem;
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      color: var(--muted);
      border-top: 1px solid var(--line);
    }
    .calendar-widget__month:first-child {
      border-top: 0;
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
    .calendar-widget__time {
      display: block;
    }
    .calendar-widget__name {
      font-size: 1.06rem;
      line-height: 1.45;
      font-weight: 500;
      letter-spacing: 0.01em;
    }
    .calendar-widget__place {
      margin-top: 0.25rem;
      font-size: 0.92rem;
      color: var(--muted);
    }
    .calendar-widget__empty,
    .calendar-widget__error {
      padding: 16px 0;
      color: var(--muted);
      font-size: 0.95rem;
      line-height: 1.5;
    }
    @media (max-width: 640px) {
      .calendar-widget__head { flex-direction: column; align-items: start; }
      .calendar-widget__item { grid-template-columns: 1fr; gap: 6px; }
      .calendar-widget__title { font-size: 1.3rem; }
    }
  `;
  document.head.appendChild(style);

  const esc = (str) => String(str).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[ch]);

  const formatDate = (value) => new Intl.DateTimeFormat('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', timeZone: TIME_ZONE
  }).format(new Date(value));

  const formatTime = (value) => new Intl.DateTimeFormat('en-GB', {
    hour: 'numeric', minute: '2-digit', hour12: true, timeZone: TIME_ZONE
  }).format(new Date(value));

  const formatRange = (event) => event.all_day ? 'All day' : `${formatTime(event.start_iso)} – ${formatTime(event.end_iso)}`;

  const toMonth = (value) => new Intl.DateTimeFormat('en-GB', {
    month: 'long', year: 'numeric', timeZone: TIME_ZONE
  }).format(new Date(value));

  const setLoading = () => {
    root.innerHTML = `
      <div class="calendar-widget">
        <div class="calendar-widget__head">
          <div>
            <div class="calendar-widget__kicker">Calendar</div>
            <h3 class="calendar-widget__title">Upcoming events</h3>
          </div>
          <div class="calendar-widget__status">Loading…</div>
        </div>
      </div>`;
  };

  const setError = (message) => {
    root.innerHTML = `
      <div class="calendar-widget">
        <div class="calendar-widget__head">
          <div>
            <div class="calendar-widget__kicker">Calendar</div>
            <h3 class="calendar-widget__title">Upcoming events</h3>
          </div>
          <div class="calendar-widget__status">Unavailable</div>
        </div>
        <div class="calendar-widget__error">${esc(message)}</div>
      </div>`;
  };

  const render = (events) => {
    if (!events.length) {
      root.innerHTML = `
        <div class="calendar-widget">
          <div class="calendar-widget__head">
            <div>
              <div class="calendar-widget__kicker">Calendar</div>
              <h3 class="calendar-widget__title">Upcoming events</h3>
            </div>
            <div class="calendar-widget__status">Up to date</div>
          </div>
          <div class="calendar-widget__empty">No upcoming events at the moment.</div>
        </div>`;
      return;
    }

    let html = `
      <div class="calendar-widget">
        <div class="calendar-widget__head">
          <div>
            <div class="calendar-widget__kicker">Calendar</div>
            <h3 class="calendar-widget__title">Upcoming events</h3>
          </div>
          <div class="calendar-widget__status">${events.length} event${events.length === 1 ? '' : 's'}</div>
        </div>
        <div class="calendar-widget__list">`;

    let currentMonth = '';
    for (const event of events) {
      const month = toMonth(event.start_iso);
      if (month !== currentMonth) {
        currentMonth = month;
        html += `<div class="calendar-widget__month">${esc(month)}</div>`;
      }

      const title = event.url
        ? `<a href="${esc(event.url)}" target="_blank" rel="noopener noreferrer">${esc(event.summary)}</a>`
        : esc(event.summary);

      html += `
        <article class="calendar-widget__item">
          <div class="calendar-widget__meta">
            <span class="calendar-widget__date">${esc(formatDate(event.start_iso))}</span>
            <span class="calendar-widget__time">${esc(formatRange(event))}</span>
          </div>
          <div>
            <div class="calendar-widget__name">${title}</div>
            ${event.location ? `<div class="calendar-widget__place">${esc(event.location)}</div>` : ''}
          </div>
        </article>`;
    }

    html += `</div></div>`;
    root.innerHTML = html;
  };

  async function init() {
    try {
      setLoading();
      const response = await fetch(dataUrl, { cache: 'no-store' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const events = Array.isArray(data.events) ? data.events : [];
      render(events);
    } catch (err) {
      console.error(err);
      setError('The calendar could not be loaded.');
    }
  }

  init();
})();
