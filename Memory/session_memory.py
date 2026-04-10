# ╔══════════════════════════════════════════════════════════════════╗
# ║           PARMANA 2.0 — Session Memory (Short-Term)             ║
# ║  Rolling in-RAM conversation window. No persistence.            ║
# ║  Wiped on process exit. Long-term recall → vector_memory.py     ║
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from LLM_Gateway.provider_router import Message


# ── Typed turn ────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    role: str                          # "user" | "assistant" | "system" | "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    provider: Optional[str] = None     # which LLM answered (assistant turns)
    model: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    tool_name: Optional[str] = None    # set for tool result turns
    metadata: dict = field(default_factory=dict)

    def to_message(self) -> Message:
        return Message(role=self.role, content=self.content)


# ── Session Memory ────────────────────────────────────────────────────────────

class SessionMemory:
    """
    Rolling window of conversation turns kept in RAM.

    Rules:
    - System prompt always pinned at index 0 (never evicted).
    - Non-system turns capped at `max_messages`.
    - When cap is hit, oldest non-system turn is dropped.
    - Token budget enforced independently of message count.
    """

    def __init__(
        self,
        max_messages: int = 50,
        max_tokens: int = 32_000,       # soft budget — triggers trim
        include_system: bool = True,
    ):
        self._max_messages = max_messages
        self._max_tokens = max_tokens
        self._include_system = include_system

        self._system: Optional[Turn] = None
        self._turns: deque[Turn] = deque()
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0

    # ── Write ─────────────────────────────────────────────────────────────────

    def set_system(self, content: str) -> None:
        """Set or replace the system prompt. Pinned — never evicted."""
        self._system = Turn(role="system", content=content)

    def add_user(self, content: str, **metadata) -> Turn:
        turn = Turn(role="user", content=content, metadata=metadata)
        self._append(turn)
        return turn

    def add_assistant(
        self,
        content: str,
        provider: str = "",
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        **metadata,
    ) -> Turn:
        self._total_input_tokens += input_tokens
        self._total_output_tokens += output_tokens
        turn = Turn(
            role="assistant",
            content=content,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            metadata=metadata,
        )
        self._append(turn)
        return turn

    def add_tool_result(self, tool_name: str, content: str) -> Turn:
        turn = Turn(role="tool", content=content, tool_name=tool_name)
        self._append(turn)
        return turn

    def _append(self, turn: Turn) -> None:
        self._turns.append(turn)
        self._enforce_limits()

    def _enforce_limits(self) -> None:
        # Drop oldest non-system turns when over cap
        while len(self._turns) > self._max_messages:
            self._turns.popleft()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_messages(
        self,
        include_system: Optional[bool] = None,
        last_n: Optional[int] = None,
    ) -> list[Message]:
        """
        Returns messages ready to pass to a provider.

        Args:
            include_system: Override instance default.
            last_n: Return only the last N non-system turns.
        """
        use_system = include_system if include_system is not None else self._include_system
        turns = list(self._turns)

        if last_n is not None:
            turns = turns[-last_n:]

        messages: list[Message] = []
        if use_system and self._system:
            messages.append(self._system.to_message())

        messages.extend(t.to_message() for t in turns if t.role != "system")
        return messages

    def get_turns(self, last_n: Optional[int] = None) -> list[Turn]:
        turns = list(self._turns)
        if last_n is not None:
            return turns[-last_n:]
        return turns

    def last_user_message(self) -> Optional[str]:
        for turn in reversed(self._turns):
            if turn.role == "user":
                return turn.content
        return None

    def last_assistant_message(self) -> Optional[str]:
        for turn in reversed(self._turns):
            if turn.role == "assistant":
                return turn.content
        return None

    # ── Introspection ─────────────────────────────────────────────────────────

    @property
    def turn_count(self) -> int:
        return len(self._turns)

    @property
    def total_tokens(self) -> dict:
        return {
            "input": self._total_input_tokens,
            "output": self._total_output_tokens,
            "total": self._total_input_tokens + self._total_output_tokens,
        }

    def summary_line(self) -> str:
        """Single-line status for CLI display."""
        t = self.total_tokens
        return (
            f"turns={self.turn_count} | "
            f"tokens in={t['input']} out={t['output']} total={t['total']}"
        )

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "system": self._system.content if self._system else None,
            "turns": [
                {
                    "role": t.role,
                    "content": t.content,
                    "timestamp": t.timestamp,
                    "provider": t.provider,
                    "model": t.model,
                    "input_tokens": t.input_tokens,
                    "output_tokens": t.output_tokens,
                    "tool_name": t.tool_name,
                    "metadata": t.metadata,
                }
                for t in self._turns
            ],
        }

    @classmethod
    def from_dict(cls, data: dict, **kwargs) -> "SessionMemory":
        obj = cls(**kwargs)
        if data.get("system"):
            obj.set_system(data["system"])
        for t in data.get("turns", []):
            turn = Turn(
                role=t["role"],
                content=t["content"],
                timestamp=t.get("timestamp", time.time()),
                provider=t.get("provider"),
                model=t.get("model"),
                input_tokens=t.get("input_tokens", 0),
                output_tokens=t.get("output_tokens", 0),
                tool_name=t.get("tool_name"),
                metadata=t.get("metadata", {}),
            )
            obj._turns.append(turn)
        return obj

    # ── Mutation ──────────────────────────────────────────────────────────────

    def clear(self, keep_system: bool = True) -> None:
        """Wipe turn history. Optionally preserve system prompt."""
        self._turns.clear()
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        if not keep_system:
            self._system = None

    def pop_last(self) -> Optional[Turn]:
        """Remove and return the most recent turn. Useful for retry logic."""
        if self._turns:
            return self._turns.pop()
        return None

    def inject_context(self, context: str, label: str = "recalled") -> None:
        """
        Inject retrieved memory or tool output as a synthetic system-adjacent
        turn so it's visible to the model without polluting the user's voice.
        """
        content = f"[{label}]\n{context}"
        turn = Turn(role="system", content=content)
        # Insert just after system prompt, before conversation
        self._turns.appendleft(turn)
        self._enforce_limits()

    def __repr__(self) -> str:
        return f"<SessionMemory turns={self.turn_count} system={'set' if self._system else 'none'}>"
