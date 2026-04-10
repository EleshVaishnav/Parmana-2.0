# ╔══════════════════════════════════════════════════════════════════╗
# ║           PARMANA 2.0 — Channel: Telegram                       ║
# ║  Async Telegram bot via python-telegram-bot v21.                ║
# ║  Wired directly to Agent. Supports text, images, commands.      ║
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Optional

import yaml
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from Core.agent import Agent

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

def _load_cfg() -> dict:
    try:
        with open("config.yaml") as f:
            return yaml.safe_load(f).get("telegram", {})
    except Exception:
        return {}

_cfg              = _load_cfg()
_ENABLED          = _cfg.get("enabled", False)
_PARSE_MODE       = _cfg.get("parse_mode", "Markdown")
_STREAM           = _cfg.get("stream", False)
_ALLOWED_USER_IDS: set[int] = set(_cfg.get("allowed_user_ids", []))


# ── Auth Guard ────────────────────────────────────────────────────────────────

def _is_allowed(update: Update) -> bool:
    if not _ALLOWED_USER_IDS:
        return True  # open to all if no whitelist configured
    user = update.effective_user
    return user is not None and user.id in _ALLOWED_USER_IDS


async def _deny(update: Update) -> None:
    await update.message.reply_text("⛔ Unauthorized.")


# ── Telegram Bot ──────────────────────────────────────────────────────────────

class TelegramChannel:
    """
    Telegram interface for Parmana.

    Commands:
        /start          — greeting + status
        /new            — clear session memory
        /status         — show agent status
        /provider <name> — switch LLM provider
        /model <name>   — switch model
        /skills         — list available skills
        /help           — command reference

    Messages:
        Text            — routed to agent.run()
        Photo           — routed to agent.run() with vision
        Document (image) — same as photo

    Inline:
        Callback buttons for quick provider switching.
    """

    def __init__(self, agent: Agent, token: Optional[str] = None):
        self._agent = agent
        self._token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        if not self._token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set.")

        self._app = (
            Application.builder()
            .token(self._token)
            .build()
        )
        self._register_handlers()

    # ── Handler Registration ──────────────────────────────────────────────────

    def _register_handlers(self) -> None:
        app = self._app

        # Commands
        app.add_handler(CommandHandler("start",    self._cmd_start))
        app.add_handler(CommandHandler("new",      self._cmd_new))
        app.add_handler(CommandHandler("status",   self._cmd_status))
        app.add_handler(CommandHandler("provider", self._cmd_provider))
        app.add_handler(CommandHandler("model",    self._cmd_model))
        app.add_handler(CommandHandler("skills",   self._cmd_skills))
        app.add_handler(CommandHandler("help",     self._cmd_help))

        # Inline button callbacks
        app.add_handler(CallbackQueryHandler(self._callback_handler))

        # Messages: text
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

        # Messages: photos
        app.add_handler(MessageHandler(filters.PHOTO, self._handle_photo))

        # Messages: documents that are images
        app.add_handler(
            MessageHandler(filters.Document.IMAGE, self._handle_document_image)
        )

        logger.debug("Telegram handlers registered.")

    # ── Commands ──────────────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)
        status = self._agent.status()
        text = (
            f"*Parmana 2.0* online.\n"
            f"Provider: `{status['provider']}`\n"
            f"Skills: `{', '.join(status['skills']) or 'none'}`\n\n"
            f"Send a message to begin. /help for commands."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    async def _cmd_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)
        self._agent.clear_session()
        await update.message.reply_text("Session cleared. Fresh context.")

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)
        s = self._agent.status()
        lines = [
            f"*Provider:* `{s['provider']}`",
            f"*Loaded:* `{', '.join(s['providers_loaded'])}`",
            f"*Skills:* `{', '.join(s['skills'])}`",
            f"*Session:* `{s['session']}`",
            f"*Vector:* `{s['vector']}`",
        ]
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )

    async def _cmd_provider(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)

        args = ctx.args
        if args:
            provider = args[0].lower()
            try:
                self._agent.set_provider(provider)
                await update.message.reply_text(f"Provider → `{provider}`", parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                await update.message.reply_text(f"Error: {e}")
        else:
            # Show inline keyboard of available providers
            providers = self._agent.providers
            buttons = [
                [InlineKeyboardButton(p, callback_data=f"provider:{p}")]
                for p in providers
            ]
            markup = InlineKeyboardMarkup(buttons)
            await update.message.reply_text("Select a provider:", reply_markup=markup)

    async def _cmd_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)
        args = ctx.args
        if not args:
            await update.message.reply_text("Usage: /model <model_name>")
            return
        model = args[0]
        provider = self._agent._default_provider
        try:
            self._agent.set_provider(provider, model=model)
            await update.message.reply_text(
                f"Model → `{model}` on `{provider}`", parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await update.message.reply_text(f"Error: {e}")

    async def _cmd_skills(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)
        skills = self._agent.skills
        if not skills:
            await update.message.reply_text("No skills enabled.")
            return
        lines = [f"• `{s}`" for s in skills]
        await update.message.reply_text(
            "*Enabled skills:*\n" + "\n".join(lines),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)
        text = (
            "*Parmana Commands*\n\n"
            "/start — status\n"
            "/new — clear session\n"
            "/status — detailed agent status\n"
            "/provider [name] — switch provider\n"
            "/model <name> — switch model\n"
            "/skills — list tools\n"
            "/help — this message\n\n"
            "Send any text or image to chat."
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # ── Message Handlers ──────────────────────────────────────────────────────

    async def _handle_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)

        user_input = update.message.text.strip()
        if not user_input:
            return

        await update.message.chat.send_action(ChatAction.TYPING)

        try:
            result = await self._agent.run(
                user_input=user_input,
                stream=False,  # Telegram edits message for streaming — handled separately
            )
            await self._send_reply(update, result.reply, result)
        except Exception as e:
            logger.exception(f"Agent error on text message: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def _handle_photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)

        await update.message.chat.send_action(ChatAction.TYPING)

        caption = update.message.caption or "Describe this image."
        photo   = update.message.photo[-1]  # highest resolution
        image_bytes = await self._download_file(photo.file_id, ctx)

        try:
            result = await self._agent.run(
                user_input=caption,
                image=image_bytes,
                stream=False,
            )
            await self._send_reply(update, result.reply, result)
        except Exception as e:
            logger.exception(f"Agent error on photo: {e}")
            await update.message.reply_text(f"Error: {e}")

    async def _handle_document_image(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _is_allowed(update):
            return await _deny(update)

        await update.message.chat.send_action(ChatAction.TYPING)

        caption     = update.message.caption or "Describe this image."
        doc         = update.message.document
        image_bytes = await self._download_file(doc.file_id, ctx)

        try:
            result = await self._agent.run(
                user_input=caption,
                image=image_bytes,
                stream=False,
            )
            await self._send_reply(update, result.reply, result)
        except Exception as e:
            logger.exception(f"Agent error on document image: {e}")
            await update.message.reply_text(f"Error: {e}")

    # ── Inline Callbacks ──────────────────────────────────────────────────────

    async def _callback_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        data = query.data or ""
        if data.startswith("provider:"):
            provider = data.split(":", 1)[1]
            try:
                self._agent.set_provider(provider)
                await query.edit_message_text(f"Provider → `{provider}`", parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                await query.edit_message_text(f"Error: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _download_file(self, file_id: str, ctx: ContextTypes.DEFAULT_TYPE) -> bytes:
        file = await ctx.bot.get_file(file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        return buf.getvalue()

    async def _send_reply(
        self,
        update: Update,
        text: str,
        result: "TurnResult",
    ) -> None:
        """
        Send reply with optional provider footer.
        Splits long messages to respect Telegram's 4096 char limit.
        """
        footer = ""
        if _cfg.get("show_provider", False):
            footer = f"\n\n_via {result.provider}/{result.model}_"

        full_text = text + footer
        parse_mode = _PARSE_MODE if _PARSE_MODE in ("Markdown", "HTML") else None

        # Telegram max message length
        chunk_size = 4000
        chunks = [full_text[i:i+chunk_size] for i in range(0, len(full_text), chunk_size)]

        for chunk in chunks:
            try:
                await update.message.reply_text(chunk, parse_mode=parse_mode)
            except Exception:
                # If markdown parse fails, send as plain text
                await update.message.reply_text(chunk, parse_mode=None)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def set_commands(self) -> None:
        """Register bot commands with Telegram (shows in UI menu)."""
        commands = [
            BotCommand("start",    "Status and greeting"),
            BotCommand("new",      "Clear session memory"),
            BotCommand("status",   "Detailed agent status"),
            BotCommand("provider", "Switch LLM provider"),
            BotCommand("model",    "Switch model"),
            BotCommand("skills",   "List enabled tools"),
            BotCommand("help",     "Command reference"),
        ]
        await self._app.bot.set_my_commands(commands)
        logger.info("Telegram bot commands registered.")

    def run_polling(self) -> None:
        """Start the bot in polling mode (blocking)."""
        logger.info("Starting Telegram bot (polling)...")

        async def post_init(app: Application) -> None:
            await self.set_commands()

        self._app.post_init = post_init
        self._app.run_polling(drop_pending_updates=True)

    async def run_webhook(
        self,
        webhook_url: str,
        listen: str = "0.0.0.0",
        port: int = 8443,
        cert_path: Optional[str] = None,
        key_path: Optional[str] = None,
    ) -> None:
        """Start the bot in webhook mode (non-blocking, for production)."""
        logger.info(f"Starting Telegram bot (webhook) at {webhook_url}")
        await self.set_commands()
        await self._app.run_webhook(
            listen=listen,
            port=port,
            url_path=self._token,
            webhook_url=f"{webhook_url}/{self._token}",
            cert=cert_path,
            key=key_path,
        )
