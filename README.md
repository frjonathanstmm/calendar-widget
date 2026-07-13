# Calendar widget for Squarespace

This repository builds a small GitHub Pages site from a public Google Calendar iCal feed and publishes:

- `widget.js` — the script you load in Squarespace
- `events.json` — the generated event data
- `index.html` — a small demo page

## What this uses

GitHub Pages can be published with a custom GitHub Actions workflow, and GitHub Actions can run on a schedule. GitHub’s Pages docs also note that if you use a GitHub Actions publishing source, the artifact must contain the entry file at the top level. citeturn342658search0turn342658search4turn342658search5turn440728search10turn342658search8

## Your calendar feed

The repo is already set to your public iCal feed:

`https://calendar.google.com/calendar/ical/c_4a5a1fc5afb51323ac2d430ac7566576eb3385682877769438f6eee2a1037f02%40group.calendar.google.com/public/basic.ics`

## Repository name

Use the repository name `calendar-widget` so the Pages URL becomes:

`https://frjonathanstmm.github.io/calendar-widget/`

## Setup steps

1. Create a new **public** repository on GitHub named `calendar-widget`.
2. Upload all files from this folder.
3. Commit to the `main` branch.
4. In GitHub, open **Settings → Pages** and set the source to **GitHub Actions**.
5. Wait for the first Actions run to finish.

## Squarespace

Add this as a Code Block where you want the widget to appear:

```html
<div id="calendar-widget"></div>
<script src="https://frjonathanstmm.github.io/calendar-widget/widget.js"></script>
```

That is all Squarespace needs.

## Updating the feed

The workflow runs automatically on every push to `main` and on a schedule once per hour. You can also trigger it manually from the Actions tab.
