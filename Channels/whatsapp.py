# ╔══════════════════════════════════════════════════════════════════╗
# ║           PARMANA 2.0 — Channel: WhatsApp                       ║
# ║  Meta Cloud API webhook handler.                                ║
# ║  Receives messages, routes to Agent, sends replies.             ║
# ║  Runs as an async HTTP server (no framework dependency).        ║
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from typing import Optional

import httpx
import yaml
from aiohttp import web

from Core.agent import Agent

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    try:
        with open("config.yaml") as f:
            return yaml.safe_load(f).get("whatsapp", {})
    except Exception:
        return {}

_cfg             = _load_cfg()
_ENABLED         = _cfg.get("enabled", False)
_WEBHOOK_PATH    = _cfg.get("webhook_path", "/webhook/whatsapp")

_WA_TOKEN        = os.getenv("WHATSAPP_TOKEN", "")
_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
_VERIFY_TOKEN    = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
_APP_SECRET      = os.getenv("WHATSAPP_APP_SECRET", "")   # for payload signature verification

_META_API_BASE   = "https://graph.facebook.com/v19.0"
_SUPPORTED_TYPES = {"text", "image", "document", "audio", "video"}


# ── Meta API Client ───────────────────────────────────────────────────────────

class MetaAPIClient:
    """Thin async client for the WhatsApp Cloud API."""

    def __init__(self, token: str, phone_number_id: str):
        self._token = token
        self._phone_id = phone_number_id
        self._base = f"{_META_API_BASE}/{phone_number_id}"

    async def send_text(self, to: str, text: str) -> dict:
        """Send a plain text message."""
        # WhatsApp max message length is 4096 chars
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        last_resp = {}
        for chunk in chunks:
            last_resp = await self._post("messages", {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": chunk},
            })
        return last_resp

    async def send_reaction(self, to: str, message_id: str, emoji: str = "⚙️") -> dict:
        """React to a message (signals processing)."""
        return await self._post("messages", {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "reaction",
            "reaction": {"message_id": message_id, "emoji": emoji},
        })

    async def mark_read(self, message_id: str) -> dict:
        """Mark a message as read."""
        return await self._post("messages", {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        })

    async def download_media(self, media_id: str) -> bytes:
        """Download media (image/audio/document) by media ID."""
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: get media URL
            meta_resp = await client.get(
                f"{_META_API_BASE}/{media_id}",
                headers=self._auth_headers(),
            )
            meta_resp.raise_for_status()
            media_url = meta_resp.json().get("url", "")

            # Step 2: download bytes
            media_resp = await client.get(
                media_url,
                headers=self._auth_headers(),
            )
            media_resp.raise_for_status()
            return media_resp.content

    async def _post(self, path: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{self._base}/{path}",
                headers={**self._auth_headers(), "Content-Type": "application/json"},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}


# ── Signature Verification ────────────────────────────────────────────────────

def _verify_signature(body: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 header from Meta."""
    if not _APP_SECRET:
        return True  # skip verification if app secret not configured
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        _APP_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header[7:]
    return hmac.compare_digest(expected, received)


# ── Message Extraction ────────────────────────────────────────────────────────

def _extract_messages(payload: dict) -> list[dict]:
    """
    Parse the Meta webhook payload and return a flat list of message dicts.
    Each dict: {from, message_id, type, text?, media_id?, mime_type?, caption?}
    """
    messages = []
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    msg_type = msg.get("type", "")
                    if msg_type not in _SUPPORTED_TYPES:
                        continue

                    extracted = {
                        "from":       msg.get("from", ""),
                        "message_id": msg.get("id", ""),
                        "type":       msg_type,
                        "text":       None,
                        "media_id":   None,
                        "mime_type":  None,
                        "caption":    None,
                    }

                    if msg_type == "text":
                        extracted["text"] = msg.get("text", {}).get("body", "")
                    elif msg_type in {"image", "document", "audio", "video"}:
                        media_obj = msg.get(msg_type, {})
                        extracted["media_id"]  = media_obj.get("id")
                        extracted["mime_type"] = media_obj.get("mime_type", "")
                        extracted["caption"]   = media_obj.get("caption", "")

                    messages.append(extracted)
    except Exception as e:
        logger.warning(f"Failed to extract messages from payload: {e}")

    return messages


# ── WhatsApp Channel ──────────────────────────────────────────────────────────

class WhatsAppChannel:
    """
    WhatsApp Cloud API channel for Parmana.

    Runs an aiohttp server exposing a webhook endpoint.
    Handles:
        GET  /webhook/whatsapp  — Meta verification handshake
        POST /webhook/whatsapp  — Incoming message events

    Message routing:
        text     → agent.run(user_input)
        image    → agent.run(user_input=caption, image=bytes)
        document → agent.run(user_input=caption, image=bytes) if image mime
        audio    → transcript note (not yet implemented)
    """

    def __init__(self, agent: Agent):
        self._agent  = agent
        self._client = MetaAPIClient(_WA_TOKEN, _PHONE_NUMBER_ID)
        self._app    = web.Application()
        self._app.router.add_get(_WEBHOOK_PATH,  self._handle_verify)
        self._app.router.add_post(_WEBHOOK_PATH, self._handle_webhook)
        self._processing: set[str] = set()   # deduplicate in-flight message IDs

    # ── Webhook Handlers ──────────────────────────────────────────────────────

    async def _handle_verify(self, request: web.Request) -> web.Response:
        """Meta webhook verification (GET)."""
        mode      = request.rel_url.query.get("hub.mode", "")
        token     = request.rel_url.query.get("hub.verify_token", "")
        challenge = request.rel_url.query.get("hub.challenge", "")

        if mode == "subscribe" and token == _VERIFY_TOKEN:
            logger.info("WhatsApp webhook verified.")
            return web.Response(text=challenge, status=200)

        logger.warning("WhatsApp webhook verification failed.")
        return web.Response(status=403)

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Incoming message events (POST)."""
        body = await request.read()

        # Signature check
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(body, sig):
            logger.warning("Invalid webhook signature.")
            return web.Response(status=401)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400)

        # Acknowledge immediately — Meta requires < 5s response
        asyncio.create_task(self._process_payload(payload))
        return web.Response(text="OK", status=200)

    # ── Processing ────────────────────────────────────────────────────────────

    async def _process_payload(self, payload: dict) -> None:
        messages = _extract_messages(payload)
        for msg in messages:
            msg_id = msg["message_id"]

            # Deduplicate (Meta can deliver the same event twice)
            if msg_id in self._processing:
                continue
            self._processing.add(msg_id)

            try:
                await self._handle_message(msg)
            except Exception as e:
                logger.exception(f"Error handling WhatsApp message {msg_id}: {e}")
            finally:
                self._processing.discard(msg_id)

    async def _handle_message(self, msg: dict) -> None:
        sender     = msg["from"]
        msg_id     = msg["message_id"]
        msg_type   = msg["type"]

        # Mark as read + react to signal processing
        await self._client.mark_read(msg_id)
        await self._client.send_reaction(sender, msg_id, "⚙️")

        try:
            if msg_type == "text":
                await self._handle_text(sender, msg["text"] or "")

            elif msg_type in {"image", "document"}:
                mime = msg.get("mime_type", "")
                is_image = mime.startswith("image/") or msg_type == "image"

                if is_image and msg["media_id"]:
                    image_bytes = await self._client.download_media(msg["media_id"])
                    caption     = msg.get("caption") or "Describe this image."
                    await self._handle_image(sender, caption, image_bytes)
                elif msg["media_id"]:
                    # Non-image document
                    await self._client.send_text(
                        sender,
                        "Document received. Only image documents are supported for vision analysis."
                    )
                else:
                    await self._client.send_text(sender, "Could not retrieve media.")

            elif msg_type == "audio":
                await self._client.send_text(
                    sender,
                    "Audio received. Voice transcription is not yet enabled."
                )

            elif msg_type == "video":
                await self._client.send_text(
                    sender,
                    "Video received. Video analysis is not yet enabled."
                )

        except Exception as e:
            logger.exception(f"Failed to handle message type={msg_type}: {e}")
            await self._client.send_text(sender, f"Error: {e}")

    async def _handle_text(self, sender: str, text: str) -> None:
        if not text.strip():
            return

        # Check for inline commands
        if text.strip().lower() == "/new":
            self._agent.clear_session()
            await self._client.send_text(sender, "Session cleared.")
            return
        if text.strip().lower() == "/status":
            s = self._agent.status()
            reply = (
                f"Provider: {s['provider']}\n"
                f"Skills: {', '.join(s['skills'])}\n"
                f"Session: {s['session']}\n"
                f"Vector: {s['vector']}"
            )
            await self._client.send_text(sender, reply)
            return
        if text.strip().lower().startswith("/provider "):
            provider = text.strip().split(" ", 1)[1].strip()
            try:
                self._agent.set_provider(provider)
                await self._client.send_text(sender, f"Provider → {provider}")
            except Exception as e:
                await self._client.send_text(sender, f"Error: {e}")
            return

        result = await self._agent.run(user_input=text, stream=False)
        await self._client.send_text(sender, result.reply)

    async def _handle_image(self, sender: str, caption: str, image_bytes: bytes) -> None:
        result = await self._agent.run(
            user_input=caption,
            image=image_bytes,
            stream=False,
        )
        await self._client.send_text(sender, result.reply)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def run(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Start the webhook server (blocking)."""
        if not _ENABLED:
            logger.warning("WhatsApp channel is disabled in config.yaml.")
            return
        if not _WA_TOKEN or not _PHONE_NUMBER_ID or not _VERIFY_TOKEN:
            raise ValueError(
                "Missing WhatsApp env vars: WHATSAPP_TOKEN, "
                "WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN"
            )
        logger.info(f"Starting WhatsApp webhook server on {host}:{port}{_WEBHOOK_PATH}")
        web.run_app(self._app, host=host, port=port)

    async def run_async(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        """Start the webhook server (async, for embedding in larger app)."""
        runner = web.AppRunner(self._app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        logger.info(f"WhatsApp webhook running on {host}:{port}{_WEBHOOK_PATH}")
