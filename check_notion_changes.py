import json
import os
import sys
from datetime import datetime, timezone

import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "state.json"

# Notion split "databases" into databases + data sources in this API version.
# Query endpoints now live under /v1/data_sources/{id}/query instead of
# /v1/databases/{id}/query.
NOTION_VERSION = "2025-09-03"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

SERVER_NAME = "Rust Samrajya"

# Colors keyed by task Status, so a player can tell progress at a glance
# regardless of whether the card is a "new"/"updated"/"removed" event.
STATUS_COLOR = {
    "to do": 0x99AAB5,    # grey
    "doing": 0xFAA61A,    # orange
    "done": 0x57F287,     # green
}
DEFAULT_COLOR = 0x5865F2  # blurple, used when status is unrecognized
REMOVED_COLOR = 0xED4245  # red, always used for removed tasks

STATUS_EMOJI = {
    "to do": "📋",
    "doing": "🔨",
    "done": "✅",
}
PRIORITY_EMOJI = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🟢",
    "done": "✅",
}
CATEGORY_EMOJI = {
    "bug": "🐛",
    "feature": "✨",
    "server config": "⚙️",
}

CHANGE_BADGE = {
    "new": "🆕  New Task",
    "updated": "✏️  Task Updated",
    "removed": "🗑️  Task Removed",
}


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def get_text(prop):
    if not prop:
        return None
    t = prop.get("type")
    if t == "title":
        parts = prop.get("title", [])
        return "".join(p.get("plain_text", "") for p in parts) or None
    if t == "rich_text":
        parts = prop.get("rich_text", [])
        return "".join(p.get("plain_text", "") for p in parts) or None
    if t == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    if t == "status":
        sel = prop.get("status")
        return sel.get("name") if sel else None
    if t == "multi_select":
        return ", ".join(o.get("name") for o in prop.get("multi_select", [])) or None
    if t == "people":
        return ", ".join(p.get("name", "Unknown") for p in prop.get("people", [])) or None
    if t == "date":
        d = prop.get("date")
        return d.get("start") if d else None
    if t == "checkbox":
        return "Yes" if prop.get("checkbox") else "No"
    if t == "number":
        return str(prop.get("number")) if prop.get("number") is not None else None
    return None


def format_due(raw):
    if not raw:
        return None
    # Notion dates are ISO 8601, e.g. "2026-06-20" or "2026-06-20T15:00:00.000+00:00"
    try:
        date_part = raw[:10]
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except ValueError:
        return raw


def extract_fields(page):
    props = page.get("properties", {})

    title = None
    for val in props.values():
        if val.get("type") == "title":
            title = get_text(val)
            break

    status = None
    assignee = None
    priority = None
    category = None
    due = None

    for key, val in props.items():
        lk = key.lower()
        t = val.get("type")
        if t in ("status", "select") and "status" in lk and status is None:
            status = get_text(val)
        if t == "people" and assignee is None:
            assignee = get_text(val)
        if t in ("select", "multi_select") and ("priority" in lk or "tag" in lk) and priority is None:
            priority = get_text(val)
        if t in ("select", "multi_select") and "categor" in lk and category is None:
            category = get_text(val)
        if t == "date" and due is None:
            due = get_text(val)

    if status is None:
        for val in props.values():
            if val.get("type") in ("status", "select"):
                status = get_text(val)
                break

    return {
        "title": title or "Untitled",
        "status": status or "—",
        "assignee": assignee,
        "priority": priority or "—",
        "category": category,
        "due": due,
        "last_edited_time": page.get("last_edited_time"),
        "url": page.get("url"),
    }


def resolve_data_source_id():
    """Look up the data source id(s) backing this database.

    Under API version 2025-09-03, queries must target a data source, not the
    database container itself. Most databases (including ones created before
    this change) have exactly one data source, so we just take the first.
    """
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    if resp.status_code == 404:
        raise SystemExit(
            "Notion API returned 404 looking up the database.\n"
            "This almost always means one of:\n"
            "  1. NOTION_DATABASE_ID is wrong, or\n"
            "  2. The Notion integration has not been connected to this "
            "database (open the database in Notion -> ... menu -> "
            "Connections -> add your integration).\n"
            f"Database ID used: {DATABASE_ID}"
        )
    resp.raise_for_status()
    data = resp.json()
    data_sources = data.get("data_sources") or []
    if not data_sources:
        raise SystemExit(
            "Database lookup succeeded but no data_sources were returned. "
            "Response: " + json.dumps(data)[:500]
        )
    return data_sources[0]["id"]


def fetch_all_pages(data_source_id):
    pages = []
    payload = {"page_size": 100}
    url = f"https://api.notion.com/v1/data_sources/{data_source_id}/query"
    while True:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        pages.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")
    return pages


def build_embed(change_type, fields):
    status_key = (fields.get("status") or "").lower()
    priority_key = (fields.get("priority") or "").lower()
    category_key = (fields.get("category") or "").lower()

    if change_type == "removed":
        color = REMOVED_COLOR
    else:
        color = STATUS_COLOR.get(status_key, DEFAULT_COLOR)

    status_label = f"{STATUS_EMOJI.get(status_key, '•')} {fields['status']}"
    priority_label = f"{PRIORITY_EMOJI.get(priority_key, '•')} {fields['priority']}"

    title_prefix = CATEGORY_EMOJI.get(category_key, "")
    title = f"{title_prefix} {fields['title']}".strip()

    fields_block = [
        {"name": "Status", "value": status_label, "inline": True},
        {"name": "Priority", "value": priority_label, "inline": True},
    ]

    if fields.get("category"):
        fields_block.append({"name": "Category", "value": fields["category"], "inline": True})

    due_label = format_due(fields.get("due"))
    if due_label:
        fields_block.append({"name": "📅 Due", "value": due_label, "inline": True})

    if fields.get("assignee"):
        fields_block.append({"name": "Assignee", "value": fields["assignee"], "inline": True})

    embed = {
        "title": title,
        "url": fields.get("url"),
        "color": color,
        "author": {"name": CHANGE_BADGE[change_type]},
        "fields": fields_block,
        "footer": {"text": f"{SERVER_NAME} — Dev Log"},
        "timestamp": fields.get("last_edited_time") or datetime.now(timezone.utc).isoformat(),
    }
    return embed


def post_to_discord(embeds):
    for i in range(0, len(embeds), 10):
        batch = embeds[i:i + 10]
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": batch}, timeout=30)
        if resp.status_code >= 300:
            print(f"Discord post failed: {resp.status_code} {resp.text}", file=sys.stderr)
            resp.raise_for_status()


def main():
    old_state = load_state()

    data_source_id = resolve_data_source_id()
    pages = fetch_all_pages(data_source_id)

    new_state = {}
    embeds = []

    for page in pages:
        pid = page["id"]
        fields = extract_fields(page)
        new_state[pid] = fields

        old = old_state.get(pid)
        if old is None:
            embeds.append(build_embed("new", fields))
        elif old.get("last_edited_time") != fields.get("last_edited_time"):
            embeds.append(build_embed("updated", fields))

    for pid, old_fields in old_state.items():
        if pid not in new_state:
            embeds.append(build_embed("removed", old_fields))

    if embeds:
        print(f"Posting {len(embeds)} change(s) to Discord...")
        post_to_discord(embeds)
    else:
        print("No changes detected.")

    save_state(new_state)


if __name__ == "__main__":
    main()
