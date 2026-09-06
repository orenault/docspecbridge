from __future__ import annotations

import os
import select
import sys
from typing import Iterable, TypeVar

from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

T = TypeVar("T")
console = Console()

_UP = "UP"
_DOWN = "DOWN"
_ENTER = "ENTER"
_ESC = "ESC"


def _read_key() -> str:
    """Read one navigation key without third-party dependencies."""
    if os.name == "nt":  # Windows PowerShell / cmd
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            if code == "H":
                return _UP
            if code == "P":
                return _DOWN
            return code
        if ch == "\x1b":
            return _ESC
        if ch in ("\r", "\n"):
            return _ENTER
        return ch

    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch in ("\r", "\n"):
            return _ENTER
        if ch != "\x1b":
            return ch
        # Distinguish plain Esc from ANSI arrow sequences without blocking.
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            return _ESC
        second = sys.stdin.read(1)
        if second != "[":
            return _ESC
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            return _ESC
        third = sys.stdin.read(1)
        if third == "A":
            return _UP
        if third == "B":
            return _DOWN
        return _ESC
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def select_option(
    title: str,
    options: Iterable[tuple[T, str]],
    *,
    default_index: int = 0,
    allow_escape: bool = True,
) -> T | None:
    """Arrow-key selector with numeric shortcuts and Esc cancellation."""
    choices = list(options)
    if not choices:
        return None
    index = max(0, min(default_index, len(choices) - 1))

    if not sys.stdin.isatty():
        console.print(title)
        for idx, (_, label) in enumerate(choices, 1):
            console.print(f"[{idx}] {label}")
        while True:
            raw = input(f"> [{index + 1}] ").strip()
            if not raw:
                return choices[index][0]
            if allow_escape and raw.lower() in {"esc", "q", "quit"}:
                return None
            if raw.isdigit() and 1 <= int(raw) <= len(choices):
                return choices[int(raw) - 1][0]

    def render() -> Group:
        lines: list[Text] = [Text(title, style="bold cyan")]
        for idx, (_, label) in enumerate(choices):
            marker = "❯" if idx == index else " "
            text = Text(f"{marker} [{idx + 1}] {label}")
            if idx == index:
                text.stylize("reverse")
            lines.append(text)
        lines.append(Text("↑/↓  Enter  Esc" if allow_escape else "↑/↓  Enter", style="dim"))
        return Group(*lines)

    selected: tuple[T, str] | None = None
    with Live(render(), console=console, transient=True, auto_refresh=False) as live:
        while True:
            pressed = _read_key()
            if pressed in (_UP, "k"):
                index = (index - 1) % len(choices)
                live.update(render(), refresh=True)
            elif pressed in (_DOWN, "j"):
                index = (index + 1) % len(choices)
                live.update(render(), refresh=True)
            elif pressed == _ENTER:
                selected = choices[index]
                break
            elif allow_escape and pressed == _ESC:
                return None
            elif pressed.isdigit():
                numeric = int(pressed)
                if 1 <= numeric <= len(choices):
                    selected = choices[numeric - 1]
                    break

    assert selected is not None
    console.print(f"[dim]{title}:[/dim] {selected[1]}")
    return selected[0]
