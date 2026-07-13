# Calendar Widget

This repository builds a small GitHub Pages site from a public Google Calendar `.ics` feed and serves a lightweight widget script for Squarespace.

## What it does

- Fetches the public Google Calendar feed
- Parses upcoming events
- Generates:
  - `events.json`
  - `widget.js`
  - `index.html`
- Publishes them with GitHub Pages via GitHub Actions

## Your calendar feed

This repo is already configured for your public calendar:

`https://calendar.google.com/calendar/ical/c_4a5a1fc5afb51323ac2d430ac7566576eb3385682877769438f6eee2a1037f02%40group.calendar.google.com/public/basic.ics`

## GitHub Pages setup

1. Open the repository on GitHub.
2. Go to **Settings → Pages**.
3. Set **Source** to **GitHub Actions**.
4. Commit or push to `main`.
5. Wait for the workflow run to finish in the **Actions** tab.

GitHub documents custom Pages publishing with GitHub Actions, and GitHub Actions workflows can be scheduled. Google says the iCal address only works for a public calendar.
