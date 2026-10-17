# Report EC dashboard

The municipal side of Report EC: every resident report on one map, sorted by
urgency, with status updates sent back to residents on WhatsApp.

## What's in this folder

| File | What it does |
|---|---|
| `main.py` | The FastAPI app, now also serving the dashboard at `/dashboard/` |
| `static/index.html` | Page layout: top bar, sidebar, stat cards, map, issue queue, details panel |
| `static/styles.css` | Colours, fonts and spacing, based on our Figma design |
| `static/app.js` | Loads the reports and makes the page work (filters, map pins, queue, updates) |
| `static/sample-data.js` | 36 sample reports (25 issues, 11 grouped duplicates) for the demo |

## How to add it to the project

1. Copy the `static` folder into the `report-ec` folder.
2. Replace `main.py` with the one in this folder. If someone has added new
   routes to `main.py` since, don't replace it. Instead add these two lines:
   - at the top: `from fastapi.staticfiles import StaticFiles`
   - at the very bottom: `app.mount("/dashboard", StaticFiles(directory="static", html=True), name="dashboard")`
3. Start the server as usual: `uvicorn main:app --reload`
4. Open http://127.0.0.1:8000/login and sign in. See STAFF-LOGIN.md for creating accounts.

The map tiles and fonts load from the internet, so the laptop needs a connection.

## Sample data or real data

At the top of `static/app.js`:

```js
const USE_SAMPLE_DATA = false;
```

- `false` (the default): the dashboard reads live reports from the database
  every 15 seconds and saves updates with `PATCH /reports/{ref}`. See
  DATABASE-SETUP.md.
- `true`: offline sample mode, using `sample-data.js`. Nothing is saved. Only
  use this as a backup if the internet or database is down during a demo.

## What the backend needs to provide (for the database and backend team)

**`GET /reports`** returns a list of reports. The dashboard reads these fields
(the database spelling in brackets also works):

`ref`, `category`, `description`, `lat` (`latitude`), `lng` (`longitude`),
`street` (`landmark`), `suburb`, `ward`, `priority` (`urgency`), `status`, `team`,
`photo_url`, `duplicate_of`, `created_at`, `updated_at`, `first_response_at`,
`resolved_at`, and optionally `title`, `language`, `translation` and `updates`
(a list of `{ "at": ..., "text": ... }`).

Never return the resident's phone number. `main.py` already leaves it out.

The values must match the design:

- `category`: `water_leak`, `no_water`, `power_outage`, `pothole`, `streetlight`, `other`
- `priority`: `critical`, `high`, `medium`
- `status`: `received`, `awaiting_dispatch`, `team_assigned`, `in_progress`, `resolved`

The current database table uses different priority and status values and has
no `power_outage` category, so these need updating before switching to real data.

**`PATCH /reports/{ref}`** receives:

```json
{ "status": "in_progress", "team": "Water & sanitation", "message": "EC-1042 update: Work has started on your report." }
```

It should save the status and team, set `updated_at` (and `first_response_at`
if it's empty), and send the message on WhatsApp to the resident and to anyone
whose report is linked to it.

## Testing checklist (for Asa)

- [ ] The page loads with no red errors in the browser console (F12, Console tab)
- [ ] The four stat cards show numbers
- [ ] Each filter (type, priority, status) changes both the map and the queue
- [ ] Search finds a report by reference (EC-1042), street (Njoli) and ward (Ward 27)
- [ ] Clicking a queue row and clicking a map pin both open the same report on the right
- [ ] "Escalations" in the sidebar shows only overdue reports
- [ ] "Post update" changes the status in the queue and adds a line to "What has happened"
- [ ] "Assign team" changes the team
- [ ] "Export snapshot" downloads a CSV that opens in Excel, with no phone numbers in it
- [ ] Compare the page with the Figma and list any differences in colour or spacing
