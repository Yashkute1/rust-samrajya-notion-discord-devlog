import json
import os
import sys
from datetime import datetime, timezone

import requests

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
STATE_FILE = "state.json"

NOTION_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

COLOR_NEW = 0x57F287      # green
COLOR_UPDATED = 0x5865F2  # blurple
COLOR_REMOVED = 0xED4245  # red


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

    # Prefer properties whose name hints at their purpose.
    for key, val in props.items():
        lk = key.lower()
        t = val.get("type")
        if t in ("status", "select") and "status" in lk and status is None:
            status = get_text(val)
        if t == "people" and assignee is None:
            assignee = get_text(val)
        if t in ("select", "multi_select") and ("priority" in lk or "tag" in lk) and priority is None:
            priority = get_text(val)

    # Fallback: take the first status/select property found.
    if status is None:
        for val in props.values():
            if val.get("type") in ("status", "select"):
                status = get_text(val)
                break

    return {
        "title": title or "Untitled",
        "status": status or "—",
        "assignee": assignee or "Unassigned",
        "priority": priority or "—",
        "last_edited_time": page.get("last_edited_time"),
        "url": page.get("url"),
    }


def fetch_all_pages():
    pages = []
    payload = {"page_size": 100}
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
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
    color = {"new": COLOR_NEW, "updated": COLOR_UPDATED, "removed": COLOR_REMOVED}[change_type]
    badge = {"new": "🆕 New Task", "updated": "✏️ Task Updated", "removed": "🗑️ Task Removed"}[change_type]
    embed = {
        "title": fields["title"],
        "url": fields.get("url"),
        "color": color,
        "author": {"name": badge},
        "fields": [
            {"name": "Status", "value": fields["status"], "inline": True},
            {"name": "Assignee", "value": fields["assignee"], "inline": True},
            {"name": "Priority", "value": fields["priority"], "inline": True},
        ],
        "footer": {"text": "Rust Samrajya — Tasks"},
        "timestamp": fields.get("last_edited_time") or datetime.now(timezone.utc).isoformat(),
    }
    return embed


def post_to_discord(embeds):
    # Discord allows a max of 10 embeds per message.
    for i in range(0, len(embeds), 10):
        batch = embeds[i:i + 10]
        resp = requests.post(DISCORD_WEBHOOK_URL, json={"embeds": batch}, timeout=30)
        if resp.status_code >= 300:
            print(f"Discord post failed: {resp.status_code} {resp.text}", file=sys.stderr)
            resp.raise_for_status()


def main():
    old_state = load_state()
    pages = fetch_all_pages()

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
