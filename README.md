# 🧹 Chore Tracker

A single-page, open-source household chore dashboard designed for always-on tablets and touch screens. Installable as a PWA for true offline kiosk mode.

**[Live Demo →](https://bhs128.github.io/chore-tracker/)**

## Features

- **Touch-first kiosk UI** — landscape, no-scroll, fullscreen-friendly
- **Room × Day grid** — tap a cell to mark a room as cleaned
- **Configurable clean cycles** — each room has a "stays clean for N days" setting
- **Color gradient status** — green → neutral → red based on how overdue a room is, with future-day projections
- **Auto-sort by urgency** — rooms automatically reorder so the most overdue appear at the top, with smooth FLIP animation on state changes
- **Today column highlight** — current day is visually emphasized in the table
- **Footer summary row** — sticky bottom row showing clean/total counts per day
- **Cell tooltip** — hover or long-press a cell to see detailed status (days since cleaned, days until due, who cleaned)
- **User selection** — tap a name chip in the top bar before checking off a chore; cells show unique user-name prefixes to distinguish similar names
- **User management** — add/remove users from the settings panel
- **Sparkline history** — compact canvas chart in the top bar showing clean vs overdue trends with Sunday tick marks
- **Dark & Light themes** — toggle in settings; all colors adapt via CSS custom properties (blue accent palette)
- **Self-hosted Poppins font** — three weights (400/600/700) bundled as woff2 for offline use
- **PWA / Installable** — manifest + service worker for "Add to Home Screen" and full offline support
- **100% client-side** — all data lives in `localStorage`, nothing leaves your browser
- **Zero dependencies** — single HTML file, no build step, no frameworks
- **Import / Export** — back up and restore data as JSON


### Local Deployment / Kiosk

Just open `index.html` in any modern browser, or serve it locally:

```bash
python3 -m http.server 8765
# open http://localhost:8765
```

For kiosk mode:

- **Chromium**: `chromium --kiosk --app=http://localhost:8765`
- **Firefox**: press `F11` for fullscreen

## Usage

1. **Add rooms** — click "+ Room" and set name, description, and clean cycle (days)
2. **Add users** — open ⚙ Settings, type a name in the user input and press Enter or "+"
3. **Select yourself** — tap your name chip in the top bar (it highlights with a blue border)
4. **Mark chores done** — tap a cell in the grid; it records who cleaned and when
5. **Tap again to undo** — toggle off a mistaken entry
6. **Edit/delete rooms** — tap a room's name in the left column
7. **Adjust settings** — click ⚙ to change visible days, history range, color theme, or import/export data
8. **Install as app** — use your browser's "Add to Home Screen" or "Install App" option for a standalone kiosk experience

## Project Structure

```
index.html       — entire application (HTML + CSS + JS)
manifest.json    — PWA web app manifest
sw.js            — service worker for offline caching
fonts/           — self-hosted Poppins woff2 (400, 600, 700)
icons/           — PWA & favicon icons (16, 32, 192, 512, maskable-512)
LICENSE          — MIT license
README.md        — this file
```

## Data Model

All data is stored in `localStorage` under the key `chore-tracker-data`:

```json
{
  "rooms": [{ "id": "...", "name": "Kitchen", "desc": "...", "cleanDays": 2 }],
  "users": ["Alice", "Bob"],
  "entries": {
    "2026-02-17": {
      "room-id": { "cleaned": true, "user": "Alice" }
    }
  },
  "settings": { "daysShown": 14, "historyDays": 30, "theme": "dark" },
  "selectedUser": "Alice"
}
```

Old entries (>120 days) are automatically garbage-collected on load.

## Feature Roadmap

The following features are under consideration. They are listed in recommended implementation order.

### 1. Schedules (Room Lists / Categories)

**Problem:** Not every room needs cleaning on the same cadence or in the same context. A "Daily Tidy" schedule is different from a "Deep Clean" schedule, but they may overlap on the same physical rooms.

**Approaches considered:**

| Approach | Description | Pros | Cons |
|----------|-------------|------|------|
| **A — Tag/filter** | Add tags to rooms (e.g. `daily`, `deep`) and filter the grid view | Simple, no data model change | Tags are ambiguous; a room can appear in multiple views with conflicting state |
| **B — Room groups** | Nest rooms inside named groups; grid shows one group at a time | Clean visual separation | Still shares entries — marking "Kitchen" clean in Daily also marks it in Deep Clean |
| **C — Separate schedules** ★ | Top-level `Schedule` concept, each with its own independent rooms, entries, and settings | Clean data model, no state conflicts, scales to any workflow | Slightly more complex UI (tab bar) |

**Recommended: Approach C — Separate Schedules**

- Add `DATA.schedules[]` array, each schedule containing its own `rooms`, `entries`, and `settings`.
- Add `DATA.activeSchedule` index to track the currently viewed schedule.
- Render a tab bar (or swipeable tabs) above the grid to switch schedules.
- Existing single-schedule data migrates into `schedules[0]` automatically.
- Export/import covers all schedules in one JSON file.

**Data model sketch:**

```json
{
  "schedules": [
    {
      "id": "...",
      "name": "Daily Tidy",
      "rooms": [...],
      "entries": {...},
      "settings": { "daysShown": 14, "historyDays": 30 }
    },
    {
      "id": "...",
      "name": "Deep Clean",
      "rooms": [...],
      "entries": {...},
      "settings": { "daysShown": 14, "historyDays": 30 }
    }
  ],
  "activeSchedule": 0,
  "users": ["Alice", "Bob"],
  "selectedUser": "Alice",
  "settings": { "theme": "dark" }
}
```

### 2. Subtasks

**Problem:** Some rooms have multiple distinct steps (e.g. Kitchen → wipe counters, sweep floor, clean sink). Currently the description field lists these as free text, but there's no way to track partial completion.

**Approaches considered:**

| Approach | Description | Pros | Cons |
|----------|-------------|------|------|
| **A — Description as reminder** | Keep the current free-text description; no tracking | Zero complexity, already works | No partial-completion visibility |
| **B — Dialog checklist** ★ | Optional `subtasks` array on a room definition; tapping a cell opens a checklist dialog | Tracks partial progress, cell shows fraction (e.g. `3/5`), room only marked "cleaned" when all checked | Adds a dialog step to the tap flow |
| **C — Inline expansion** | Expand the row into sub-rows for each subtask | Most granular tracking | Clutters the grid, breaks the compact kiosk layout |

**Recommended: Start with A (free), graduate to B when tracking is needed**

- Add optional `subtasks: [{ id, label }]` to each room definition.
- When subtasks exist and user taps a cell, show a checklist dialog instead of immediate toggle.
- Cell displays checked/total (e.g. `3/5`) and only counts as fully cleaned when all are checked.
- Entry shape: `entries[date][roomId].subtasks = { taskId: true, ... }`.
- **Staleness reset:** subtask checkmarks reset based on the room's clean cycle — once a new cycle begins, previous subtask checks are irrelevant.
- Rooms without subtasks keep the current single-tap toggle behavior.

**Data model sketch:**

```json
{
  "rooms": [
    {
      "id": "...",
      "name": "Kitchen",
      "desc": "Full kitchen clean",
      "cleanDays": 2,
      "subtasks": [
        { "id": "ct", "label": "Wipe counters" },
        { "id": "sw", "label": "Sweep floor" },
        { "id": "ds", "label": "Dishes" }
      ]
    }
  ],
  "entries": {
    "2026-02-17": {
      "room-id": {
        "cleaned": true,
        "user": "Alice",
        "subtasks": { "ct": true, "sw": true, "ds": true }
      }
    }
  }
}
```

### Implementation Priority

1. **Schedules first** — structural data model change that shapes everything else.
2. **Subtasks second** — additive layer that sits on top of the existing room/entry model.

## License

MIT — see [LICENSE](LICENSE)
