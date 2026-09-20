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
_PAGE_UP = "PAGE_UP"
_PAGE_DOWN = "PAGE_DOWN"
_HOME = "HOME"
_END = "END"
_ENTER = "ENTER"
_ESC = "ESC"
_CANCEL_SENTINEL = "__DOCSPECBRIDGE_ESCAPE__"


class UserCancelled(Exception):
    """Raised when the user presses Esc during an interactive action."""


def _read_key() -> str:
    """Read one navigation key without third-party dependencies."""
    if os.name == "nt":  # Windows PowerShell / cmd
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            code = msvcrt.getwch()
            return {
                "H": _UP,
                "P": _DOWN,
                "I": _PAGE_UP,
                "Q": _PAGE_DOWN,
                "G": _HOME,
                "O": _END,
            }.get(code, code)
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
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            return _ESC
        second = sys.stdin.read(1)
        if second not in {"[", "O"}:
            return _ESC
        if not select.select([sys.stdin], [], [], 0.05)[0]:
            return _ESC
        third = sys.stdin.read(1)
        if third == "A":
            return _UP
        if third == "B":
            return _DOWN
        if third in {"H", "1"}:
            if third == "1" and select.select([sys.stdin], [], [], 0.01)[0]:
                sys.stdin.read(1)  # '~'
            return _HOME
        if third in {"F", "4"}:
            if third == "4" and select.select([sys.stdin], [], [], 0.01)[0]:
                sys.stdin.read(1)
            return _END
        if third in {"5", "6"}:
            if select.select([sys.stdin], [], [], 0.01)[0]:
                sys.stdin.read(1)  # '~'
            return _PAGE_UP if third == "5" else _PAGE_DOWN
        return _ESC
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _truncate(value: str, width: int) -> str:
    if width <= 1:
        return value[:1]
    if len(value) <= width:
        return value
    return value[: max(1, width - 1)] + "…"


def select_option(
    title: str,
    options: Iterable[tuple[T, str]],
    *,
    default_index: int = 0,
    allow_escape: bool = True,
    help_text: str | None = None,
) -> T | None:
    """Terminal-size-aware selector with Esc cancellation."""
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

    def viewport() -> tuple[int, int, int]:
        height = max(8, int(console.size.height or 24))
        visible = max(3, height - 4)
        visible = min(visible, len(choices))
        start = max(0, min(index - visible // 2, len(choices) - visible))
        return start, start + visible, visible

    def render() -> Group:
        start, end, _ = viewport()
        width = max(24, int(console.size.width or 80))
        lines: list[Text] = [Text(title, style="bold cyan")]
        lines.append(Text(f"{start + 1}-{end} / {len(choices)}", style="dim"))
        index_width = len(str(len(choices)))
        for idx in range(start, end):
            _, label = choices[idx]
            marker = "❯" if idx == index else " "
            prefix = f"{marker} [{idx + 1:>{index_width}}] "
            text = Text(prefix + _truncate(str(label), max(4, width - len(prefix) - 1)), no_wrap=True, overflow="ellipsis")
            if idx == index:
                text.stylize("reverse")
            lines.append(text)
        default_help = "↑/↓  PgUp/PgDn  Home/End  Enter" + ("  Esc" if allow_escape else "")
        lines.append(Text(help_text or default_help, style="dim", no_wrap=True, overflow="ellipsis"))
        return Group(*lines)

    selected: tuple[T, str] | None = None
    with Live(render(), console=console, transient=True, auto_refresh=False, vertical_overflow="crop") as live:
        while True:
            pressed = _read_key()
            _, _, page_size = viewport()
            if pressed in (_UP, "k"):
                index = (index - 1) % len(choices)
            elif pressed in (_DOWN, "j"):
                index = (index + 1) % len(choices)
            elif pressed == _PAGE_UP:
                index = max(0, index - page_size)
            elif pressed == _PAGE_DOWN:
                index = min(len(choices) - 1, index + page_size)
            elif pressed == _HOME:
                index = 0
            elif pressed == _END:
                index = len(choices) - 1
            elif pressed == _ENTER:
                selected = choices[index]
                break
            elif allow_escape and pressed == _ESC:
                return None
            else:
                if len(pressed) == 1 and pressed.isdigit():
                    numeric = int(pressed)
                    if 1 <= numeric <= min(9, len(choices)):
                        selected = choices[numeric - 1]
                        break
            live.update(render(), refresh=True)

    assert selected is not None
    console.print(f"[dim]{title}:[/dim] {selected[1]}")
    return selected[0]


def prompt_text(title: str, default: str = "", *, strip: bool = True) -> str:
    """Prompt for text while making Esc a first-class cancel operation.

    In an interactive terminal, Esc immediately aborts the current action by raising
    :class:`UserCancelled`.  In redirected input, the literal values ``esc``, ``q``
    and ``quit`` provide the equivalent behaviour for automation/tests.
    """
    if not sys.stdin.isatty():
        shown = str(default or "")
        value = input(f"{title}: {shown}\n> ").strip()
        if value.lower() in {"esc", "q", "quit"}:
            raise UserCancelled()
        value = value or shown
        return value.strip() if strip else value

    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.key_binding import KeyBindings

        bindings = KeyBindings()

        @bindings.add("escape")
        def _cancel(event) -> None:  # pragma: no cover - terminal event loop
            event.app.exit(result=_CANCEL_SENTINEL)

        @bindings.add("c-c")
        def _cancel_ctrl_c(event) -> None:  # pragma: no cover - terminal event loop
            event.app.exit(result=_CANCEL_SENTINEL)

        value = prompt(f"{title}: ", default=str(default or ""), key_bindings=bindings)
        if value == _CANCEL_SENTINEL:
            raise UserCancelled()
        return value.strip() if strip else value
    except UserCancelled:
        raise
    except (EOFError, KeyboardInterrupt):
        raise UserCancelled()
    except Exception:
        shown = str(default or "")
        value = input(f"{title}: {shown}\n> ").strip()
        if value.lower() in {"esc", "q", "quit"}:
            raise UserCancelled()
        value = value or shown
        return value.strip() if strip else value


def edit_text(title: str, default: str = "") -> str:
    """Edit a pre-filled value in-place; Esc cancels the current action."""
    return prompt_text(title, default)
