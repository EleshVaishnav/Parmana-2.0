# ╔══════════════════════════════════════════════════════════════════╗
# ║           PARMANA 2.0 — Entry Point                             ║
# ║  CLI interface + channel launcher.                              ║
# ║  Run: python main.py [--provider X] [--model Y] [--channel Z]  ║
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

# ── Load .env before anything else ───────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.text import Text
from rich.theme import Theme

# ── Skill auto-registration (import triggers registry.register()) ─────────────
import Skills.web_search   # noqa: F401
import Skills.calculator   # noqa: F401

from Core.agent import Agent
from Skills.registry import registry

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "WARNING"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Rich Console ──────────────────────────────────────────────────────────────
_THEME = Theme({
    "provider": "dim cyan",
    "model":    "dim cyan",
    "tool":     "yellow",
    "error":    "bold red",
    "meta":     "dim white",
    "prompt":   "bold green",
    "banner":   "bold white",
})

console = Console(theme=_THEME, highlight=False)

# ── Typer App ─────────────────────────────────────────────────────────────────
app = typer.Typer(
    name="parmana",
    help="Parmana 2.0 — multi-provider AI assistant.",
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


# ── CLI REPL ──────────────────────────────────────────────────────────────────

async def _repl(
    agent: Agent,
    stream: bool,
    show_provider: bool,
    show_tokens: bool,
    prompt_symbol: str,
):
    """Interactive REPL loop."""
    console.print(
        Panel(
            "[banner]Parmana 2.0[/banner]\n"
            f"[meta]provider=[/meta][provider]{agent._default_provider}[/provider]  "
            f"[meta]skills=[/meta][tool]{', '.join(agent.skills) or 'none'}[/tool]\n"
            "[meta]Type /help for commands. Ctrl+C or /exit to quit.[/meta]",
            expand=False,
            border_style="dim white",
        )
    )

    while True:
        # ── Input ──────────────────────────────────────────────────────────
        try:
            raw = Prompt.ask(f"\n[prompt]{prompt_symbol}[/prompt]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[meta]bye.[/meta]")
            break

        user_input = raw.strip()
        if not user_input:
            continue

        # ── Built-in REPL commands ─────────────────────────────────────────
        if user_input.lower() in ("/exit", "/quit", "exit", "quit"):
            console.print("[meta]bye.[/meta]")
            break

        if user_input.lower() == "/help":
            _print_help()
            continue

        if user_input.lower() == "/status":
            _print_status(agent)
            continue

        if user_input.lower() == "/new":
            agent.clear_session()
            console.print("[meta]Session cleared.[/meta]")
            continue

        if user_input.lower() == "/reset":
            agent.reset()
            console.print("[meta]Full reset: session + vector memory cleared.[/meta]")
            continue

        if user_input.lower() == "/providers":
            console.print("[meta]Loaded:[/meta] " + ", ".join(agent.providers))
            continue

        if user_input.lower() == "/skills":
            console.print("[meta]Skills:[/meta] " + ", ".join(agent.skills))
            continue

        if user_input.lower().startswith("/provider "):
            _cmd_set_provider(agent, user_input)
            continue

        if user_input.lower().startswith("/model "):
            _cmd_set_model(agent, user_input)
            continue

        if user_input.lower().startswith("/task "):
            _cmd_set_task(agent, user_input)
            continue

        if user_input.lower().startswith("/image "):
            await _cmd_image(agent, user_input, show_provider)
            continue

        if user_input.lower().startswith("/reload"):
            agent._prompt.reload_template()
            console.print("[meta]system_prompt.txt reloaded.[/meta]")
            continue

        # ── Agent turn ────────────────────────────────────────────────────
        console.print()

        collected_tokens: list[str] = []

        def on_token(delta: str) -> None:
            console.print(delta, end="", markup=False)
            collected_tokens.append(delta)

        try:
            result = await agent.run(
                user_input=user_input,
                stream=stream,
                on_token=on_token if stream else None,
            )
        except Exception as e:
            console.print(f"\n[error]Error: {e}[/error]")
            logger.exception(e)
            continue

        # If streaming, output already printed via on_token
        if not stream:
            try:
                console.print(Markdown(result.reply))
            except Exception:
                console.print(result.reply)

        # Tool call summary
        if result.tool_calls:
            console.print()
            for tc in result.tool_calls:
                console.print(
                    f"[tool]⚙ {tc['tool']}[/tool]",
                    end="  ",
                )
            console.print()

        # Footer
        if show_provider or show_tokens:
            parts = []
            if show_provider:
                parts.append(
                    f"[provider]{result.provider}[/provider]"
                    f"[meta]/[/meta][model]{result.model}[/model]"
                )
            if show_tokens:
                parts.append(
                    f"[meta]in={result.input_tokens} "
                    f"out={result.output_tokens} "
                    f"({result.latency_ms:.0f}ms)[/meta]"
                )
            console.print(Rule(" ".join(parts), style="dim white"))


# ── REPL Helper Commands ──────────────────────────────────────────────────────

def _print_help() -> None:
    console.print(
        Panel(
            "[bold]/help[/bold]            this message\n"
            "[bold]/status[/bold]          agent + memory status\n"
            "[bold]/new[/bold]             clear session memory\n"
            "[bold]/reset[/bold]           clear session + vector memory\n"
            "[bold]/providers[/bold]       list loaded providers\n"
            "[bold]/skills[/bold]          list enabled skills\n"
            "[bold]/provider <name>[/bold] switch provider\n"
            "[bold]/model <name>[/bold]    switch model\n"
            "[bold]/task <type>[/bold]     hint routing (code/fast/reasoning/local)\n"
            "[bold]/image <path>[/bold]    analyze an image file\n"
            "[bold]/reload[/bold]          hot-reload system_prompt.txt\n"
            "[bold]/exit[/bold]            quit",
            title="Commands",
            border_style="dim white",
            expand=False,
        )
    )


def _print_status(agent: Agent) -> None:
    s = agent.status()
    lines = [
        f"[meta]provider:[/meta]  [provider]{s['provider']}[/provider]",
        f"[meta]loaded:[/meta]    [meta]{', '.join(s['providers_loaded'])}[/meta]",
        f"[meta]skills:[/meta]    [tool]{', '.join(s['skills'])}[/tool]",
        f"[meta]session:[/meta]   [meta]{s['session']}[/meta]",
        f"[meta]vector:[/meta]    [meta]{s['vector']}[/meta]",
    ]
    console.print(Panel("\n".join(lines), title="Status", border_style="dim white", expand=False))


def _cmd_set_provider(agent: Agent, raw: str) -> None:
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        console.print("[error]Usage: /provider <name>[/error]")
        return
    name = parts[1].strip()
    try:
        agent.set_provider(name)
        console.print(f"[meta]provider →[/meta] [provider]{name}[/provider]")
    except Exception as e:
        console.print(f"[error]{e}[/error]")


def _cmd_set_model(agent: Agent, raw: str) -> None:
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        console.print("[error]Usage: /model <name>[/error]")
        return
    model = parts[1].strip()
    provider = agent._default_provider
    try:
        agent.set_provider(provider, model=model)
        console.print(f"[meta]model →[/meta] [model]{model}[/model]")
    except Exception as e:
        console.print(f"[error]{e}[/error]")


def _cmd_set_task(agent: Agent, raw: str) -> None:
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        console.print("[error]Usage: /task <type>[/error]")
        return
    task = parts[1].strip()
    routing = agent._cfg.get("routing", {})
    if task not in routing:
        console.print(
            f"[error]Unknown task '{task}'. Valid: {', '.join(routing.keys())}[/error]"
        )
        return
    provider = routing[task]
    try:
        agent.set_provider(provider)
        console.print(f"[meta]task={task} → provider=[/meta][provider]{provider}[/provider]")
    except Exception as e:
        console.print(f"[error]{e}[/error]")


async def _cmd_image(agent: Agent, raw: str, show_provider: bool) -> None:
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        console.print("[error]Usage: /image <path_or_url>[/error]")
        return
    source = parts[1].strip()
    prompt = Prompt.ask("[meta]Prompt (or Enter for default)[/meta]") or "Describe this image."

    console.print()
    try:
        result = await agent.run(user_input=prompt, image=source)
        try:
            console.print(Markdown(result.reply))
        except Exception:
            console.print(result.reply)
        if show_provider:
            console.print(Rule(
                f"[provider]{result.provider}[/provider][meta]/[/meta][model]{result.model}[/model]",
                style="dim white",
            ))
    except Exception as e:
        console.print(f"[error]Vision error: {e}[/error]")


# ── Typer Commands ────────────────────────────────────────────────────────────

@app.command()
def cli(
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="LLM provider to use."),
    model:    Optional[str] = typer.Option(None, "--model",    "-m", help="Model name."),
    stream:   bool          = typer.Option(True,  "--stream/--no-stream", help="Stream tokens."),
    show_provider: bool     = typer.Option(True,  "--show-provider/--hide-provider"),
    show_tokens:   bool     = typer.Option(False, "--show-tokens/--hide-tokens"),
    prompt_symbol: str      = typer.Option("▸",   "--symbol", help="CLI prompt symbol."),
    config:   str           = typer.Option("config.yaml", "--config", "-c"),
):
    """Start Parmana in interactive CLI mode."""
    agent = Agent(config_path=config)

    if provider:
        agent.set_provider(provider, model=model)
    elif model:
        agent.set_provider(agent._default_provider, model=model)

    asyncio.run(_repl(
        agent=agent,
        stream=stream,
        show_provider=show_provider,
        show_tokens=show_tokens,
        prompt_symbol=prompt_symbol,
    ))


@app.command()
def telegram(
    config: str = typer.Option("config.yaml", "--config", "-c"),
    webhook_url: Optional[str] = typer.Option(None, "--webhook", help="Use webhook instead of polling."),
    port: int = typer.Option(8443, "--port"),
):
    """Launch the Telegram bot."""
    from Channels.telegram import TelegramChannel
    agent   = Agent(config_path=config)
    bot     = TelegramChannel(agent)
    if webhook_url:
        asyncio.run(bot.run_webhook(webhook_url=webhook_url, port=port))
    else:
        bot.run_polling()


@app.command()
def whatsapp(
    config: str = typer.Option("config.yaml", "--config", "-c"),
    host:   str = typer.Option("0.0.0.0", "--host"),
    port:   int = typer.Option(8080, "--port"),
):
    """Launch the WhatsApp webhook server."""
    from Channels.whatsapp import WhatsAppChannel
    agent   = Agent(config_path=config)
    channel = WhatsAppChannel(agent)
    channel.run(host=host, port=port)


@app.command()
def status(
    config: str = typer.Option("config.yaml", "--config", "-c"),
):
    """Print agent status and exit."""
    agent = Agent(config_path=config)
    _print_status(agent)


@app.command()
def run(
    message: str            = typer.Argument(..., help="Single message to send."),
    provider: Optional[str] = typer.Option(None, "--provider", "-p"),
    model:    Optional[str] = typer.Option(None, "--model",    "-m"),
    config:   str           = typer.Option("config.yaml", "--config", "-c"),
):
    """
    Send a single message non-interactively and print the reply.
    Useful for shell scripting:  python main.py run "what is 2+2"
    """
    agent = Agent(config_path=config)
    if provider:
        agent.set_provider(provider, model=model)

    async def _once() -> None:
        result = await agent.run(user_input=message, stream=False)
        try:
            console.print(Markdown(result.reply))
        except Exception:
            console.print(result.reply)

    asyncio.run(_once())


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
