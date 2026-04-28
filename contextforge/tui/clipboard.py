"""Cross-platform clipboard utilities for the TUI."""

import platform
import subprocess


def copy(text: str) -> bool:
    """Copy *text* to the system clipboard. Returns True on success."""
    if not text:
        return False
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
        elif system == "Linux":
            # try Wayland first, then X11
            try:
                subprocess.run(["wl-copy"], input=text, text=True, check=True)
            except FileNotFoundError:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text, text=True, check=True,
                )
        else:
            return False
        return True
    except Exception:
        return False
