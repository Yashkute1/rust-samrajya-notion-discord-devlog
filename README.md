# Notion → Discord Dev Log

Polls a Notion database every 30 minutes via GitHub Actions and posts rich
Discord embed "cards" for any task that's new, updated, or removed.

## Setup

### 1. Create a Notion integration

1. Go to https://www.notion.so/my-integrations
2. Click **New integration**, give it a name (e.g. "Discord Dev Log"), select
   your workspace, and create it.
3. Copy the **Internal Integration Secret** (starts with `secret_` or `ntn_`).

### 2. Share the database with the integration

1. Open the **Rust Samrajya — Tasks** database in Notion.
2. Click the **•••** menu in the top right → **Connections** → add the
   integration you just created.

Database ID (already extracted from your link):
```
6d99e9af-ef81-414d-a9e3-fc6cccfb3648
```

### 3. Add GitHub repo secrets

In this repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add three secrets:

| Name | Value |
|---|---|
| `NOTION_TOKEN` | the Internal Integration Secret from step 1 |
| `NOTION_DATABASE_ID` | `6d99e9af-ef81-414d-a9e3-fc6cccfb3648` |
| `DISCORD_WEBHOOK_URL` | your Discord webhook URL |

### 4. Enable and run the workflow

1. Go to the **Actions** tab, enable workflows if prompted.
2. Run **Notion to Discord Dev Log** once manually (▶ Run workflow) to seed
   `state.json`.

Heads up: the **first run** has no prior state, so it will post a "🆕 New
Task" card for every existing task in the database. After that, only real
changes (new, updated, or removed tasks) will be posted.

## How it works

- `check_notion_changes.py` queries the Notion database, compares each page's
  `last_edited_time` against the previous run's saved state (`state.json`),
  and builds a Discord embed for anything new, changed, or missing.
- Each embed shows: task title (linked to the Notion page), a colored
  sidebar and badge (🆕 new / ✏️ updated / 🗑️ removed), and fields for
  Status, Assignee, and Priority.
- The GitHub Action commits the updated `state.json` back to the repo after
  each run so the next run knows what's already been reported.

## Adjusting frequency

Edit the cron schedule in `.github/workflows/notion-discord.yml`:
- `*/15 * * * *` — every 15 minutes
- `*/30 * * * *` — every 30 minutes (default)
- `0 * * * *` — hourly
