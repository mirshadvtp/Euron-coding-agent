"""Messaging connectors — push agent results to Slack / Discord / Telegram.

Config (config.yaml):
    notifications:
      on: [done, error]          # which events to notify on
      slack_webhook: https://hooks.slack.com/services/...
      discord_webhook: https://discord.com/api/webhooks/...
      telegram_token: 123:ABC
      telegram_chat_id: "123456"

Used by the loop (on task completion) and by scheduled agents.
"""
from __future__ import annotations

from typing import Optional


def send_slack(webhook: str, text: str) -> bool:
    import httpx

    try:
        r = httpx.post(webhook, json={"text": text}, timeout=15)
        return r.status_code < 300
    except Exception:
        return False


def send_discord(webhook: str, text: str) -> bool:
    import httpx

    try:
        r = httpx.post(webhook, json={"content": text[:1900]}, timeout=15)
        return r.status_code < 300
    except Exception:
        return False


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    import httpx

    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4000]},
            timeout=15,
        )
        return r.status_code < 300
    except Exception:
        return False


def dispatch(config: Optional[dict], text: str) -> list[str]:
    """Send `text` to every configured channel. Returns the channels notified."""
    config = config or {}
    sent = []
    if config.get("slack_webhook") and send_slack(config["slack_webhook"], text):
        sent.append("slack")
    if config.get("discord_webhook") and send_discord(config["discord_webhook"], text):
        sent.append("discord")
    if config.get("telegram_token") and config.get("telegram_chat_id"):
        if send_telegram(config["telegram_token"], str(config["telegram_chat_id"]), text):
            sent.append("telegram")
    return sent
