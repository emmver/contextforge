"""TransferPanel — modal screen for configuring and launching a context transfer."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, RadioButton, RadioSet, Static


TOOLS = [
    ("claude_code", "Claude Code"),
    ("codex", "Codex"),
    ("altimate_code", "altimate-code"),
]

STRATEGIES = [
    ("summary_only", "summary_only  — max token efficiency"),
    ("key_messages", "key_messages  — scored important messages"),
    ("full_recent",  "full_recent   — last N messages verbatim"),
]


class TransferPanel(ModalScreen):
    """Modal overlay for selecting target tool, strategy, and launching transfer."""

    DEFAULT_CSS = """
    TransferPanel {
        align: center middle;
    }
    TransferPanel > Vertical {
        width: 60;
        height: auto;
        max-height: 40;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    TransferPanel #panel-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    TransferPanel .section-label {
        text-style: bold;
        margin-top: 1;
        color: $text-muted;
    }
    TransferPanel #btn-row {
        margin-top: 2;
        height: auto;
        layout: horizontal;
        align: right middle;
    }
    TransferPanel Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss(None)", "Cancel"),
    ]

    class Confirmed(Message):
        """Emitted when the user clicks Execute."""
        def __init__(self, session_id: str, target_tool: str, strategy: str) -> None:
            super().__init__()
            self.session_id = session_id
            self.target_tool = target_tool
            self.strategy = strategy

    def __init__(self, session_id: str, session_title: str = "") -> None:
        super().__init__()
        self._session_id = session_id
        self._session_title = session_title or session_id[:16]

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical, Horizontal

        with Vertical():
            yield Static(f"Transfer: {self._session_title}", id="panel-title")

            yield Static("Target tool:", classes="section-label")
            with RadioSet(id="tool-select"):
                for tool_id, tool_name in TOOLS:
                    yield RadioButton(tool_name, value=(tool_id == "codex"), id=f"tool-{tool_id}")

            yield Static("Compaction strategy:", classes="section-label")
            with RadioSet(id="strategy-select"):
                for strat_id, strat_label in STRATEGIES:
                    yield RadioButton(strat_label, value=(strat_id == "summary_only"), id=f"strat-{strat_id}")

            with Horizontal(id="btn-row"):
                yield Button("Cancel", variant="default", id="btn-cancel")
                yield Button("Preview command", variant="primary", id="btn-preview")
                yield Button("Execute", variant="error", id="btn-execute")

    def _selected_tool(self) -> str:
        radio_set = self.query_one("#tool-select", RadioSet)
        pressed = radio_set.pressed_button
        if pressed is None:
            return TOOLS[0][0]
        return pressed.id.removeprefix("tool-") if pressed.id else TOOLS[0][0]

    def _selected_strategy(self) -> str:
        radio_set = self.query_one("#strategy-select", RadioSet)
        pressed = radio_set.pressed_button
        if pressed is None:
            return "summary_only"
        return pressed.id.removeprefix("strat-") if pressed.id else "summary_only"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id in ("btn-preview", "btn-execute"):
            tool = self._selected_tool()
            strategy = self._selected_strategy()
            execute = event.button.id == "btn-execute"
            self.dismiss({"tool": tool, "strategy": strategy, "execute": execute})
