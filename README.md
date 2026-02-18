# 🧹 Chore Tracker

A single-page, open-source household chore dashboard designed for always-on tablets and touch screens. Installable as a PWA for true offline kiosk mode.

**[Live Demo →](https://bhs128.github.io/chore-tracker/)**

## Features

### Core

- **Touch-first kiosk UI** — landscape, no-scroll, fullscreen-friendly with 44px minimum tap targets
- **Dual-view grid** — switch between **🏠 Room View** (one row per room) and **📋 Task View** (one row per task type, aggregated across rooms)
- **Drill-down navigation** — tap aggregate cells or row headers to see individual tasks within a room, or individual rooms for a task type; navigate back with the ← breadcrumb bar
- **Per-room task definitions** — each room can have named tasks (e.g. "Vacuum", "Sweep/Mop"), each with its own independent clean cycle; rooms without tasks use simple single-tap toggle
- **Task type aggregation** — tasks with the same name across different rooms are automatically merged in Task View (e.g. all "Vacuum" tasks appear as one row showing completion across rooms)
- **Configurable clean cycles** — each task (or simple room) has a "stays clean for N days" setting (1–30)
- **Color gradient status** — green → neutral → red based on how overdue a task is, with future-day projections shown at reduced opacity
- **Auto-sort by urgency** — rows automatically reorder so the most overdue appear at the top, with smooth FLIP animation; sort order is frozen during drill-down to prevent disorienting jumps
- **Smart single-room toggle** — in Task View, tapping a day-cell for a task type that exists in only one room directly toggles it instead of drilling down

### Display

- **Today column highlight** — current day column emphasized with a blue inset border and auto-scrolled into view
- **Footer summary row** — sticky bottom row showing clean/total counts per day, scoped to the current view or drill-down
- **Cell tooltip** — hover or long-press a cell to see detailed status (days since cleaned, days until due, who cleaned, projected state for future dates)
- **Sparkline history** — compact canvas chart in the top bar showing clean vs overdue trends over a configurable period, with Sunday tick marks

### Users & Settings

- **User selection** — tap a name chip in the top bar before checking off a chore; cells show computed shortest-unique prefixes to distinguish users
- **User management** — add/remove users from the settings panel
- **Dark & Light themes** — toggle in settings; all colors adapt via CSS custom properties
- **Import / Export** — back up and restore all data as a JSON file
- **Reset All** — wipe all data and restore defaults from the settings panel

### Technical

- **PWA / Installable** — manifest + service worker for "Add to Home Screen" and full offline support
- **100% client-side** — all data lives in `localStorage`, nothing leaves your browser
- **Zero dependencies** — single HTML file, no build step, no frameworks
- **Self-hosted Poppins font** — three weights (400/600/700) bundled as woff2 for offline use
- **Auto-refresh** — table and history chart re-render every 60 seconds to handle day rollover


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

1. **Manage rooms** — tap "✏ Rooms" to open the room manager; add, edit, or delete rooms
2. **Add tasks to a room** — in the room editor, add named tasks (e.g. "Vacuum", "Mop") with individual clean cycles; autocomplete suggests task names used in other rooms and auto-fills the most common cycle length
3. **Add users** — open ⚙ Settings, type a name and press Enter or "+"
4. **Select yourself** — tap your name chip in the top bar (highlighted with a blue border)
5. **Switch views** — use the 🏠 / 📋 toggle to switch between Room View and Task View
6. **Mark chores done** — tap a cell in the grid; for rooms with tasks, tap aggregate cells to drill down, then check off individual tasks
7. **Tap again to undo** — toggle off a mistaken entry
8. **Drill down** — tap a room name (Room View) or task type name (Task View) to see the breakdown; tap ← Back to return
9. **Adjust settings** — click ⚙ to change visible days, history range, color theme, or import/export data
10. **Install as app** — use your browser's "Add to Home Screen" or "Install App" option for a standalone kiosk experience

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
  "rooms": [
    {
      "id": "...",
      "name": "Kitchen",
      "desc": "...",
      "cleanDays": 1,
      "tasks": [
        { "id": "...", "label": "Do Dishes", "cleanDays": 1 },
        { "id": "...", "label": "Sweep/Mop", "cleanDays": 30 }
      ]
    }
  ],
  "users": ["Alice", "Bob"],
  "entries": {
    "2026-02-17": {
      "room-id": {
        "cleaned": true,
        "user": "Alice",
        "tasks": {
          "task-id": { "cleaned": true, "user": "Alice" }
        }
      }
    }
  },
  "settings": { "daysShown": 14, "historyDays": 30, "theme": "dark" },
  "selectedUser": "Alice"
}
```

- Rooms without a `tasks` array (or with an empty one) use simple single-tap toggle and store only `{ cleaned, user }` in entries.
- Rooms with tasks store per-task completion under `entries[date][roomId].tasks[taskId]`.
- Old entries (>120 days) are automatically garbage-collected on load.

## Default Rooms

The app ships with 10 pre-configured rooms and 12 unique task types:

| Room | Tasks |
|------|-------|
| Main Bathroom | Wash Mirror (7d), Fill Soap (14d), Wash Counter (14d), Sweep/Mop (30d), Vacuum (14d), Pickup/Tidy (7d), Clean Toilet Bowl (30d), Replace Towels/Mats (14d), Empty Trash/Recycling (14d) |
| Upstairs Bathroom | Same as Main Bathroom + Wash Tub/Shower (30d) |
| Kitchen | Fill Soap (14d), Wash Counter (14d), Sweep/Mop (30d), Pickup/Tidy (7d), Replace Towels/Mats (14d), Empty Trash/Recycling (3d), Do Dishes (1d) |
| Dining Room | Vacuum (14d), Pickup/Tidy (7d) |
| Living Room | Vacuum (14d), Pickup/Tidy (7d) |
| Den/Music Room | Vacuum (14d), Pickup/Tidy (7d) |
| Kids Bedroom | Vacuum (14d), Pickup/Tidy (7d) |
| Mom & Dads Room | Vacuum (14d), Pickup/Tidy (7d) |
| Laundry Room | Fill Soap (30d), Pickup/Tidy (7d), Empty Trash/Recycling (14d), Do Laundry (1d) |
| Office | Pickup/Tidy (7d), Empty Trash/Recycling (14d) |

## Feature Roadmap

### Schedules (Room Lists / Categories)

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

## License

MIT — see [LICENSE](LICENSE)
