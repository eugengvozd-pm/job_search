#!/usr/bin/env python3
"""
PM Job Agent — Telegram delivery
================================
Runs the CV-tuned scanner (pm_job_scanner.py), remembers which vacancies it has
already seen, and pushes ONLY the new ones to your Telegram chat each run.

Designed to run on a schedule (GitHub Actions, cron, or a VPS). See the
accompanying workflow file `.github/workflows/pm-agent.yml` for the server setup.

Secrets are read from environment variables — NEVER hard-code them here:
    TELEGRAM_BOT_TOKEN   token from @BotFather
    TELEGRAM_CHAT_ID     your chat id (get it from @userinfobot)

State (seen vacancy IDs) is kept in seen_ids.json. On GitHub Actions this file
is committed back to the repo so memory persists between runs.

Local test:
    pip install requests
    export TELEGRAM_BOT_TOKEN=...   # PowerShell: $env:TELEGRAM_BOT_TOKEN=...
    export TELEGRAM_CHAT_ID=...
    python pm_agent_telegram.py
"""

import os
import sys
import json
import html
import time

import requests

# Reuse everything from the scanner living next to this file.
from pm_job_scanner import collect_ranked, fmt_salary

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

SEEN_PATH = "seen_ids.json"

# Only notify about roles scoring at least this. Raise to cut noise, lower for breadth.
MIN_FIT_TO_NOTIFY = 6

# Safety cap so a first run doesn't fire 100 messages.
MAX_MESSAGES_PER_RUN = 15

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TG_API = "https://api.telegram.org/bot{token}/sendMessage"

# ----------------------------------------------------------------------------
# Memory
# ----------------------------------------------------------------------------


def load_seen():
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_seen(seen):
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=0)


# ----------------------------------------------------------------------------
# Telegram
# ----------------------------------------------------------------------------


def send_message(text):
    """Send one HTML message. Returns True on success."""
    if not BOT_TOKEN or not CHAT_ID:
        print("! TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set", file=sys.stderr)
        return False
    try:
        r = requests.post(
            TG_API.format(token=BOT_TOKEN),
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            },
            timeout=30,
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"! Telegram send failed: {e}", file=sys.stderr)
        return False


def format_vacancy(v):
    name = html.escape(v.get("name", ""))
    company = html.escape((v.get("employer") or {}).get("name", ""))
    city = html.escape((v.get("area") or {}).get("name", ""))
    sal = html.escape(fmt_salary(v))
    tags = ", ".join(v.get("_tags", []))
    url = v.get("alternate_url", "")
    lines = [
        f"<b>{name}</b>  ·  fit {v['_score']}",
        f"{company} · {city}" + (f" · {sal}" if sal else ""),
    ]
    if tags:
        lines.append(f"<i>{html.escape(tags)}</i>")
    lines.append(f'<a href="{url}">Открыть вакансию</a>')
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------


def main():
    seen = load_seen()
    first_run = len(seen) == 0

    ranked = collect_ranked()
    fresh = [v for v in ranked
             if v["id"] not in seen and v["_score"] >= MIN_FIT_TO_NOTIFY]

    print(f"{len(ranked)} ranked, {len(fresh)} new at fit>={MIN_FIT_TO_NOTIFY}.")

    if first_run:
        # Don't spam on the very first run — just learn what's already out there,
        # send a short heads-up, and start fresh next time.
        for v in ranked:
            seen.add(v["id"])
        save_seen(seen)
        send_message(
            "🤖 PM-агент запущен. Запомнил текущие вакансии — "
            "со следующего запуска буду присылать только новые."
        )
        print("First run: baseline saved, no per-vacancy spam.")
        return

    to_send = fresh[:MAX_MESSAGES_PER_RUN]
    sent = 0
    for v in to_send:
        if send_message(format_vacancy(v)):
            sent += 1
            seen.add(v["id"])
            time.sleep(0.5)            # be gentle with Telegram rate limits

    # Mark the rest as seen too, so we don't re-notify next run.
    for v in fresh:
        seen.add(v["id"])
    save_seen(seen)

    if fresh and sent == 0:
        print("New roles found but nothing sent — check Telegram secrets.")
    elif len(fresh) > MAX_MESSAGES_PER_RUN:
        send_message(f"…и ещё {len(fresh) - MAX_MESSAGES_PER_RUN} новых — "
                     f"см. полный список в CSV.")
    print(f"Sent {sent} new vacancies to Telegram.")


if __name__ == "__main__":
    main()
