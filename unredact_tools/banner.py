"""Banner, OPSEC tip box, and color handling for help screens."""
from __future__ import annotations

import os
import shutil
import sys
import textwrap

from . import __version__
from .tips import random_tip

LOGO_BIG = [
    "██╗   ██╗███╗   ██╗██████╗ ███████╗██████╗  █████╗  ██████╗████████╗",
    "██║   ██║████╗  ██║██╔══██╗██╔════╝██╔══██╗██╔══██╗██╔════╝╚══██╔══╝",
    "██║   ██║██╔██╗ ██║██████╔╝█████╗  ██║  ██║███████║██║        ██║   ",
    "██║   ██║██║╚██╗██║██╔══██╗██╔══╝  ██║  ██║██╔══██║██║        ██║   ",
    "╚██████╔╝██║ ╚████║██║  ██║███████╗██████╔╝██║  ██║╚██████╗   ██║   ",
    " ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝╚═════╝ ╚═╝  ╚═╝ ╚═════╝   ╚═╝   ",
]
LOGO_SMALL = [
    "█ █ █▄ █ █▀█ █▀▀ █▀▄ ▄▀█ █▀▀ ▀█▀",
    "█▄█ █ ▀█ █▀▄ ██▄ █▄▀ █▀█ █▄▄  █ ",
]
# per-column gradient: red -> orange, like a redaction marker being peeled back
GRADIENT = [196, 196, 202, 202, 208, 208, 214, 214, 220]
SHADOW = 239

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"


def color_enabled(stream=sys.stdout, force_off=False) -> bool:
    if force_off or os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return os.environ.get("FORCE_COLOR") is not None or stream.isatty()


def c(text, code, on):
    return f"{code}{text}{RESET}" if on else text


def _fg(n):
    return f"\033[38;5;{n}m"


def _paint_logo(line, on):
    """Solid blocks get a red->orange gradient; the ANSI-shadow box lines go dark gray."""
    if not on:
        return line
    out, width = [], len(line)
    for i, ch in enumerate(line):
        if ch == " ":
            out.append(ch)
        elif ch in "█▀▄":
            out.append(_fg(GRADIENT[min(len(GRADIENT) - 1, i * len(GRADIENT) // width)]) + ch)
        else:
            out.append(_fg(SHADOW) + ch)
    return BOLD + "".join(out) + RESET


def banner(on: bool, tip: str | None = None) -> str:
    cols = shutil.get_terminal_size((80, 20)).columns
    width = min(cols, 78)
    logo = LOGO_BIG if cols >= len(LOGO_BIG[0]) + 4 else LOGO_SMALL
    lines = [""]
    for row in logo:
        lines.append("  " + _paint_logo(row, on))
    tag = " .tools "
    ver = f"v{__version__}"
    bar = len(logo[0]) - 4 - len(tag) - 1 - len(ver)
    lines.append("  " + c("█" * 4, "\033[38;5;236m", on) + c(tag, BOLD, on) +
                 c("█" * bar, "\033[38;5;236m", on) + " " + c(ver, DIM, on))
    lines.append("  " + c("see what your files give away. then scrub it.", DIM, on))
    lines.append("")
    tip = tip or random_tip()
    inner = max(len(logo[0]) - 2, min(width - 6, 40))  # box lines up with the logo
    wrapped = textwrap.wrap(tip, inner - 2)
    label = " OPSEC TIP "
    lines.append("  " + c("╭─", _fg(240), on) + c(label, _fg(214) + BOLD, on) +
                 c("─" * (inner - len(label) - 1) + "╮", _fg(240), on))
    for w in wrapped:
        body = f" {w.ljust(inner - 2)} "
        edge = c("│", _fg(240), on)
        lines.append(f"  {edge}{body}{edge}")
    lines.append("  " + c("╰" + "─" * (inner) + "╯", _fg(240), on))
    lines.append("")
    return "\n".join(lines)
