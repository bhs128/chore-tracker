# 🧹 Chore Tracker

A single-page, open-source household chore dashboard designed for always-on tablets and touch screens. Installable as a PWA for true offline kiosk mode.

**[Live Demo →](https://bhs128.github.io/chore-tracker/)**

## Features

### Core

- **Touch-first kiosk UI** — landscape, no-scroll, fullscreen-friendly with 44px minimum tap targets
- **Dual-view grid** — switch between **🏠 Room View** (one row per room), **📋 Task View** (one row per task type, aggregated across rooms), and **📝 Ungrouped View** (flat list of every task, sorted by urgency)
- **Ungrouped view** — shows every task as its own row with bold task name, room as subtitle, and cycle length inline; sorted most-overdue-first with a stable sort order (rows don't jump when you check them off)
- **Due-date filter** — dropdown to filter all views by urgency: show all tasks, due within 7 days, due within 3 days, due today, or overdue only; when all tasks are filtered out, a "🎉 You're all caught up!" message replaces the grid
- **Drill-down navigation** — tap aggregate cells or row headers to see individual tasks within a room, or individual rooms for a task type; navigate back with the ← breadcrumb bar
- **Per-room task definitions** — each room can have named tasks (e.g. "Vacuum", "Sweep/Mop"), each with its own independent clean cycle; rooms without tasks use simple single-tap toggle
- **Task type aggregation** — tasks with the same name across different rooms are automatically merged in Task View (e.g. all "Vacuum" tasks appear as one row showing completion across rooms)
- **Configurable clean cycles** — each task (or simple room) has a "stays clean for N days" setting (1–30)
- **Color gradient status** — green → neutral → red based on how overdue a task is, with future-day projections shown at reduced opacity
- **Auto-sort by urgency** — rows automatically reorder so the most overdue appear at the top, with smooth FLIP animation; sort order is frozen during drill-down to prevent disorienting jumps
- **Smart single-room toggle** — in Task View, tapping a day-cell for a task type that exists in only one room directly toggles it instead of drilling down

### Display

- **Today column highlight** — current day column emphasized with a blue inset border and auto-scrolled into view
- **Date navigation** — arrow icons on the first and last date column headers let you shift the visible date window forward or backward
- **Footer summary row** — sticky bottom row showing maintain/proactive/newly-due breakdown per day, scoped to the current view or drill-down
- **Cell tooltip** — hover or long-press a cell to see detailed status (days since cleaned, days until due, who cleaned, projected state for future dates)
- **Sparkline history** — compact canvas chart in the top bar showing clean vs overdue trends over a configurable period, with Sunday tick marks

### 📊 Review Dashboard

- **Full-width infographic** — opens as a modal dialog (up to 1200px wide) with hero stats, heatmaps, leaderboards, and room health cards
- **Hero stats** — four cards at the top showing tasks done today, overall clean percentage, current activity streak, and 7-day MVP
- **Activity heatmap** — 30-day grid with per-user rows and a total row; cells colored by intensity; month and ISO week header rows with alternating backgrounds to show period boundaries
- **Today section** — two-column layout with a prioritized hit list (top 5 most overdue tasks) on the left and done-today grouped by user plus room health mini-cards on the right
- **Weekly comparison** — This Week and Last Week side-by-side, each with due/done/net stats, user leaderboard with progress bars, medal rankings, maintain/progress breakdown, and badges (Most Tasks, Deep Clean, Proactive)
- **Monthly comparison** — This Month and Last Month side-by-side with the same leaderboard and badge format
- **Collapsible sections** — each period card can be collapsed/expanded with a click
- **Responsive** — collapses to single-column on tablet (768px), compact mode on phone (480px) with heatmap numbers hidden

### Users & Settings

- **User selection** — tap a name chip in the top bar before checking off a chore; cells show computed shortest-unique prefixes to distinguish users
- **User management** — add/remove users via a dedicated dialog (click 👤 icon in the top bar)
- **Dark & Light themes** — toggle in settings; all colors adapt via CSS custom properties
- **Import / Export** — back up and restore all data as a JSON file
- **Reset All** — wipe all data and restore defaults from the settings panel

### Technical

- **PWA / Installable** — manifest + service worker for "Add to Home Screen" and full offline support
- **Offline-first** — all data lives in `localStorage` by default; nothing leaves your browser unless you opt in to sync
- **Optional multi-device sync** — point any number of browsers at a lightweight Python sync server to share data across tablets/phones on the same network, with real-time WebSocket push updates
- **Version conflict resolution** — server detects stale writes via `X-Base-Version` header; clients automatically merge and retry on 409 Conflict
- **Sync changelog** — server records a changelog of all data changes (checked/unchecked/reassigned tasks, added/removed rooms and users) with per-entry rollback and prune support
- **Connection status dialog** — click the sync indicator to see current mode, server version, WebSocket state, last sync time, and run a multi-layer connectivity test
- **Zero dependencies** — single HTML file, no build step, no frameworks
- **Self-hosted Poppins font** — three weights (400/600/700) bundled as woff2 for offline use
- **Auto-refresh** — table and history chart re-render every 60 seconds to handle day rollover


### Local Deployment / Kiosk

Just open `index.html` in any modern browser, or serve it locally:

```bash
python3 -m http.server 8765
# open http://localhost:8765
```

### Multi-Device Sync (optional)

The sync server handles data synchronisation **and** serves the app itself, so clients only need a URL — no local files required.

```bash
# One-time setup (pick one)
pip install websockets        # via pip
apt install python3-websockets # via apt (Debian/Raspberry Pi OS)

# Start the sync server (defaults: port 8780, WS on 8781)
python3 server/server.py
# → Static:    http://0.0.0.0:8780/
# → REST:      http://0.0.0.0:8780/data
# → WebSocket: ws://0.0.0.0:8781
```

On each device, just visit `http://<server-ip>:8780` (or open ⚙ Settings and enter the server URL to enable sync from a locally-opened file). A green dot in the top bar confirms a live connection. Data syncs instantly via WebSocket; if the connection drops, the app continues working offline from localStorage and re-syncs when the server is reachable again.

**Server options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8780` | Port for REST API & static files (WebSocket listens on port+1) |
| `--data` | `chore-data.json` | Path to the JSON data file |
| `--static` | *(auto-detected)* | Directory to serve static files from; set to `''` to disable |

**Examples:**

```bash
python3 server/server.py --port 80              # serve on port 80 (requires root), WS on 81
python3 server/server.py --data /tmp/data.json   # custom data file location
python3 server/server.py --static ''             # sync-only, no static file serving
```

**Architecture:**
- `GET /` — serves `index.html` and all static assets (HTML, JS, fonts, icons, manifest)
- `GET /data` — fetch the full data blob (REST)
- `PUT /data` — save the full data blob; server stamps a `_version` field; returns 409 Conflict with server data if `X-Base-Version` is stale (REST)
- `GET /changelog` — fetch the sync changelog (REST)
- `DELETE /changelog/<ts>` — prune a specific changelog entry (REST)
- `POST /changelog/rollback` — undo a changelog entry's changes (REST)
- `ws://<host>:<port+1>` — server broadcasts `data-changed` to all connected clients on every PUT
- Clients write to localStorage immediately (instant UI), then push to the server in the background
- Each device's selected user and theme are kept local (not synced)

A systemd service file is included at `server/chore-tracker.service` for auto-starting on boot (e.g. on a Raspberry Pi).

For kiosk mode:

- **Chromium**: `chromium --kiosk --app=http://<server-ip>`
- **Firefox**: press `F11` for fullscreen

## Usage

1. **Manage rooms** — tap "✏ Rooms" to open the room manager; add, edit, or delete rooms
2. **Add tasks to a room** — in the room editor, add named tasks (e.g. "Vacuum", "Mop") with individual clean cycles; autocomplete suggests task names used in other rooms and auto-fills the most common cycle length
3. **Add users** — open ⚙ Settings, type a name and press Enter or "+"
4. **Select yourself** — tap your name chip in the top bar (highlighted with a blue border)
5. **Switch views** — use the 🏠 / 📋 / 📝 toggle to switch between Room View, Task View, and Ungrouped View
6. **Filter by urgency** — use the "Show:" dropdown to filter tasks by how soon they're due
7. **Mark chores done** — tap a cell in the grid; for rooms with tasks, tap aggregate cells to drill down, then check off individual tasks; in Ungrouped View, tap cells directly
7. **Tap again to undo** — toggle off a mistaken entry
8. **Drill down** — tap a room name (Room View) or task type name (Task View) to see the breakdown; tap ← Back to return
9. **Adjust settings** — click ⚙ to change visible days, history range, color theme, or import/export data
10. **Review progress** — tap 📊 Review in the top bar to open the full-width dashboard with hero stats, activity heatmap, hit list, leaderboards, and room health cards
11. **View sync changelog** — tap 📜 to see a timestamped log of all synced changes with rollback/prune options
12. **Check connection** — tap the sync status indicator to see connection details and run a diagnostic test
13. **Install as app** — use your browser's "Add to Home Screen" or "Install App" option for a standalone kiosk experience

## Project Structure

```
index.html       — entire application (HTML + CSS + JS)
manifest.json    — PWA web app manifest
sw.js            — service worker for offline caching
fonts/           — self-hosted Poppins woff2 (400, 600, 700)
icons/           — PWA & favicon icons (16, 32, 192, 512, maskable-512)
server/          — optional sync server for multi-device use
  server.py      — REST + WebSocket + static file server (Python 3, one dependency)
  test_server.py — pytest test suite for the sync server
  chore-tracker.service — systemd unit file for auto-start on boot
.github/         — CI configuration
  workflows/test.yml — GitHub Actions: runs server test suite on push/PR
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
  "settings": { "pastDays": 3, "futureDays": 10, "historyDays": 30, "theme": "dark" },
  "selectedUser": "Alice"
}
```

- Rooms without a `tasks` array (or with an empty one) use simple single-tap toggle and store only `{ cleaned, user }` in entries.
- Rooms with tasks store per-task completion under `entries[date][roomId].tasks[taskId]`.
- Old entries (>120 days) are automatically garbage-collected on load.

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
