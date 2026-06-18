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


def send_google_chat(webhook: str, text: str) -> bool:
    import httpx

    try:
        r = httpx.post(webhook, json={"text": text[:4000]}, timeout=15)
        return r.status_code < 300
    except Exception:
        return False


def send_whatsapp(sid: str, token: str, frm: str, to: str, text: str) -> bool:
    """Send a WhatsApp message via Twilio."""
    import httpx

    try:
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            data={"From": f"whatsapp:{frm}", "To": f"whatsapp:{to}", "Body": text[:1500]},
            auth=(sid, token),
            timeout=15,
        )
        return r.status_code < 300
    except Exception:
        return False


def send_linear(api_key: str, team_id: str, text: str) -> bool:
    """Create a Linear issue with the result."""
    import httpx

    mutation = (
        "mutation($t:String!,$d:String!,$team:String!){"
        "issueCreate(input:{teamId:$team,title:$t,description:$d}){success}}"
    )
    try:
        r = httpx.post(
            "https://api.linear.app/graphql",
            json={"query": mutation, "variables": {
                "t": text.splitlines()[0][:80] or "Euron Agent", "d": text[:4000], "team": team_id}},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
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
    if config.get("google_chat_webhook") and send_google_chat(config["google_chat_webhook"], text):
        sent.append("google_chat")
    if all(config.get(k) for k in ("twilio_sid", "twilio_token", "whatsapp_from", "whatsapp_to")):
        if send_whatsapp(config["twilio_sid"], config["twilio_token"],
                         str(config["whatsapp_from"]), str(config["whatsapp_to"]), text):
            sent.append("whatsapp")
    if config.get("linear_api_key") and config.get("linear_team_id"):
        if send_linear(config["linear_api_key"], config["linear_team_id"], text):
            sent.append("linear")
    return sent
