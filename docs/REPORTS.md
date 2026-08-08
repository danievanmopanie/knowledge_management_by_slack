# Frontend Support Reports

Automated reports posted into `#frontend-support`.

## Jobs

| Report | Schedule | Purpose |
|--------|----------|---------|
| **Daily Focus of the Day** | Every day **07:00** | Recent incidents, themes, and practical focus for technicians starting their shift |
| **Weekly Performance** | **Mondays 07:30** | Weekly volume, problematic Assignment Groups, people with recurring issues |

## Data sources

Reports use structured incident CSVs from:

1. `data/incidents/` (preferred drop-zone for exports)
2. `data/raw/` (including files uploaded via `#knowledge-uploads`)

Flexible column matching supports typical ServiceNow-style exports:

`number`, `short_description`, `state`, `assignment_group`, `assigned_to`, `caller`, `location`, `opened_at`, `resolved_at`, `category`, …

The daily report also pulls hybrid RAG knowledge for theme context.

> **Note:** Until live ServiceNow integration is added, keep a regular CSV export (or upload) so the reports have data.

## Location filter (weekly)

You indicated the weekly location will be specified later. When ready, set:

```env
REPORT_WEEKLY_LOCATION=Your Site Name
```

Matching is case-insensitive substring against the incident `location` field.

## Manual run

```bash
python scripts/run_daily_report.py
python scripts/run_weekly_report.py
```

## Enable on the GX10 (systemd)

```bash
sudo cp deploy/systemd/frontend-daily-report.* /etc/systemd/system/
sudo cp deploy/systemd/frontend-weekly-report.* /etc/systemd/system/
# Edit WorkingDirectory / User / venv paths as needed

sudo systemctl daemon-reload
sudo systemctl enable --now frontend-daily-report.timer
sudo systemctl enable --now frontend-weekly-report.timer

sudo systemctl list-timers | grep frontend
```

Ensure the bot is invited to `#frontend-support` and `CHANNEL_FRONTEND_SUPPORT` is set in `.env`.
