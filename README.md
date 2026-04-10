# Parmana 2.0

> A "no-filler" multi-provider AI assistant. 25+ LLM providers, persistent memory, tool calling, vision — all wired into a single CLI.

---

## Install

**Linux / Mac**
```bash
curl -sSL https://raw.githubusercontent.com/EleshVaishnav/Parmana-2.0/main/install.sh | bash
```

**Windows (PowerShell)**
```powershell
iwr -useb https://raw.githubusercontent.com/EleshVaishnav/Parmana-2.0/main/install.bat -OutFile install.bat; .\install.bat
```

Then add your API keys to `.env` (created automatically from `.env.example`).

---

## Start

```bash
# Interactive CLI (default)
python main.py

# Single shot
python main.py run "explain async iterators in python"

# Specific provider + model
python main.py --provider anthropic --model claude-opus-4-5

# Telegram bot
python main.py telegram

# WhatsApp webhook
python main.py whatsapp
```

---

## CLI Commands

| Command | What it does |
|---|---|
| `/provider <name>` | Switch LLM provider live |
| `/model <name>` | Switch model live |
| `/task <type>` | Route by task hint (`code` `fast` `reasoning` `local`) |
| `/image <path\|url>` | Analyze an image |
| `/new` | Clear session memory |
| `/reset` | Clear session + vector memory |
| `/status` | Provider, skills, memory stats |
| `/providers` | List all loaded providers |
| `/skills` | List enabled tools |
| `/reload` | Hot-reload `system_prompt.txt` |
| `/exit` | Quit |

---

## Providers

All 25+ providers are supported out of the box. Set the relevant key(s) in `.env` to activate them.

| Provider | Adapter type | Key env var |
|---|---|---|
| OpenAI | Native SDK | `OPENAI_API_KEY` |
| Anthropic | Native SDK | `ANTHROPIC_API_KEY` |
| Google Gemini | Native SDK | `GEMINI_API_KEY` |
| Groq | Native SDK | `GROQ_API_KEY` |
| Mistral | Native SDK | `MISTRAL_API_KEY` |
| Amazon Bedrock | boto3 | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| OpenRouter | OpenAI-compat | `OPENROUTER_API_KEY` |
| DeepSeek | OpenAI-compat | `DEEPSEEK_API_KEY` |
| xAI (Grok) | OpenAI-compat | `XAI_API_KEY` |
| Fireworks | OpenAI-compat | `FIREWORKS_API_KEY` |
| Alibaba DashScope (Qwen) | OpenAI-compat | `DASHSCOPE_API_KEY` |
| BytePlus | OpenAI-compat | `BYTEPLUS_API_KEY` |
| Moonshot (Kimi) | OpenAI-compat | `MOONSHOT_API_KEY` |
| StepFun | OpenAI-compat | `STEPFUN_API_KEY` |
| Chutes | OpenAI-compat | `CHUTES_API_KEY` |
| Venice AI | OpenAI-compat | `VENICE_API_KEY` |
| Z.AI | OpenAI-compat | `ZAI_API_KEY` |
| OpenCode | OpenAI-compat | `OPENCODE_API_KEY` |
| Vercel AI Gateway | OpenAI-compat | `VERCEL_AI_GATEWAY_TOKEN` |
| Cloudflare AI Gateway | Custom | `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_GATEWAY_ID` |
| MiniMax | Custom REST | `MINIMAX_API_KEY` |
| Zhipu (GLM) | Native SDK | `ZHIPUAI_API_KEY` |
| Qianfan (Baidu) | Native SDK | `QIANFAN_ACCESS_KEY` |
| Ollama | Local | _(no key — runs locally)_ |
| fal | Native SDK | `FAL_KEY` |
| Runway | REST | `RUNWAY_API_KEY` |
| ComfyUI | Local REST | _(no key — runs locally)_ |

### Task-based routing

Set in `config.yaml`. Parmana auto-selects the best provider per task type:

```yaml
routing:
  code:       openai       # GPT-4o
  reasoning:  anthropic    # Claude
  fast:       groq         # sub-second
  local:      ollama       # offline/private
  image_gen:  fal
  video_gen:  runway
  long_context: gemini     # 1M token window
```

### Fallback chain

If a provider fails, Parmana automatically tries the next one:

```yaml
fallback_chain: [openai, anthropic, groq, openrouter, ollama]
```

---

## Memory

**Session memory** — rolling conversation window (in RAM, configurable size).

**Vector memory** — ChromaDB + sentence-transformers. Persists across sessions. Automatically stores every turn and injects semantically relevant past context into each new prompt.

```yaml
memory:
  session:
    max_messages: 50
  vector:
    enabled: true
    top_k: 5
    score_threshold: 0.45
```

---

## Skills / Tools

Skills self-register on import. Two built in:

**`web_search`** — DuckDuckGo web search. No API key needed.
```
web_search(query, max_results?, region?, safe_search?)
web_news(query, max_results?, region?)
```

**`calculator`** — SymPy symbolic math + unit conversion.
```
calculator(expression, mode?)
  modes: evaluate | simplify | expand | factor | solve | diff | integrate | latex

unit_convert(value, from_unit, to_unit)
  supports: length, mass, time, speed, temperature, data
```

### Adding a custom skill

```python
# myskills/my_skill.py
from Skills.registry import Skill, SkillParam, registry

async def my_handler(query: str) -> str:
    return f"result for {query}"

registry.register(Skill(
    name="my_skill",
    description="Does something useful.",
    params=[SkillParam(name="query", type="string", description="Input.", required=True)],
    handler=my_handler,
))
```

Then import it in `main.py`:
```python
import myskills.my_skill  # noqa: F401
```

---

## Vision

Supports image input via file path, URL, raw bytes, or base64.

Vision-capable providers: OpenAI, Anthropic, Gemini, Groq, OpenRouter, Mistral, DeepSeek, xAI, Fireworks, DashScope, BytePlus, Moonshot, Zhipu, Venice.

Tesseract OCR fallback for all other providers.

```bash
# In CLI
/image /path/to/diagram.png
# Then enter a prompt, or press Enter for default ("Describe this image.")

# Programmatic
result = await agent.run(user_input="what's in this chart?", image="chart.png")
```

---

## Project Structure

```
Parmana 2.0/
├── main.py                  ← Entry point + CLI REPL
├── config.yaml              ← All runtime settings
├── .env.example             ← API keys template
├── requirements.txt         ← Dependencies
├── system_prompt.txt        ← Personality + prompt template
├── LLM_Gateway/
│   └── provider_router.py   ← All provider adapters + routing
├── Core/
│   ├── agent.py             ← Brain loop (tool calling, streaming, vision)
│   └── prompt_manager.py    ← Memory injection + turn lifecycle
├── Memory/
│   ├── session_memory.py    ← Short-term (in-RAM, rolling window)
│   └── vector_memory.py     ← Long-term (ChromaDB, semantic search)
├── Skills/
│   ├── registry.py          ← Tool manager
│   ├── web_search.py        ← DuckDuckGo
│   └── calculator.py        ← SymPy math + unit conversion
├── Vision/
│   └── vision_handler.py    ← Multi-provider vision + OCR fallback
└── Channels/
    ├── telegram.py          ← Telegram bot
    └── whatsapp.py          ← WhatsApp webhook
```

---

## Configuration

All settings live in `config.yaml`. Secrets only in `.env`. Key sections:

```yaml
app:
  default_provider: openai   # fallback when no provider specified

cli:
  stream: true               # stream tokens live
  show_provider: true        # show which provider answered
  show_tokens: false         # show token usage per turn

memory:
  vector:
    enabled: true
    top_k: 5                 # results injected per query

skills:
  web_search:
    enabled: true
    max_results: 8
  calculator:
    enabled: true
```

---

## Channels

### Telegram

```bash
python main.py telegram
# or with webhook:
python main.py telegram --webhook https://yourdomain.com --port 8443
```

Bot commands: `/start` `/new` `/status` `/provider` `/model` `/skills` `/help`

### WhatsApp

```bash
python main.py whatsapp --host 0.0.0.0 --port 8080
```

Requires a Meta Developer app with WhatsApp Cloud API enabled. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` in `.env`.

Inline commands work in chat: `/new`, `/status`, `/provider <name>`.

---

## Requirements

- Python 3.10+
- Git
- Tesseract (optional, for OCR fallback) — [install guide](https://github.com/tesseract-ocr/tesseract)
- Ollama (optional, for local models) — [ollama.com](https://ollama.com)

---

## License

MIT
