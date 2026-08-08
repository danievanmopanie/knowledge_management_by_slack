# Knowledge Base Backup & Scheduling

This project backs up the hybrid knowledge base:

- Chroma vector store
- Knowledge graph (`graph.json`)
- Optional raw uploaded documents

## Manual commands

```bash
# Create a backup
python scripts/backup_kb.py backup
python scripts/backup_kb.py backup --label pre-change

# List backups
python scripts/backup_kb.py list

# Restore
python scripts/backup_kb.py restore data/backups/kb_backup_YYYYMMDDTHHMMSSZ

# Run the same job used by the scheduler (backup + retention prune)
python scripts/run_scheduled_backup.py
```

## Configuration (`.env`)

```env
BACKUP_ROOT=./data/backups
BACKUP_INCLUDE_RAW=true
BACKUP_RETENTION_COUNT=14
BACKUP_LABEL_PREFIX=scheduled
```

- `BACKUP_RETENTION_COUNT` – how many recent backups to keep
- Older backups are automatically pruned after each scheduled run

## Recommended: systemd timer on the GX10

1. Copy the unit files (adjust paths/user as needed):

```bash
sudo cp deploy/systemd/kb-backup.service /etc/systemd/system/
sudo cp deploy/systemd/kb-backup.timer /etc/systemd/system/
```

2. Edit the service file so `WorkingDirectory`, `User`, `EnvironmentFile`, and `ExecStart` match your install path and virtualenv.

3. Enable and start the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kb-backup.timer
sudo systemctl list-timers | grep kb-backup
```

Default schedule: **every day at 02:15** (with a small random delay).

### Change the schedule

Edit `/etc/systemd/system/kb-backup.timer`, for example:

```ini
# Every 6 hours
OnCalendar=*-*-* 00/6:00:00

# Twice a day
OnCalendar=*-*-* 02:15:00
OnCalendar=*-*-* 14:15:00
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart kb-backup.timer
```

## Alternative: cron

```cron
# Daily at 02:15
15 2 * * * cd /opt/knowledge_management_by_slack && .venv/bin/python scripts/run_scheduled_backup.py >> /var/log/kb-backup.log 2>&1
```

## Notes

- Scheduled backups use the label prefix from `BACKUP_LABEL_PREFIX` (default: `scheduled`).
- Retention pruning only deletes folders that match `kb_backup_*`.
- Keep at least a few successful backups before relying on pruning in production.
