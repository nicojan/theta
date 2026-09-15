"""Terminal interface over the sideload pipeline.

The pipeline runs on a thread worker; every line it narrates is posted back as a
message rather than written to a widget directly, because Textual widgets are
not thread-safe.
"""

import os
import traceback
from datetime import datetime

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Rule,
    Static,
    Switch,
)

from . import bootstrap_profile, cli, config, device as device_mod, keychain
from . import profile as profile_mod
from . import pipeline
from .errors import SideloadError


class Narrate(Message):
    """A line of progress from the worker thread."""

    def __init__(self, text, style=""):
        super().__init__()
        self.text = text
        self.style = style


class Finished(Message):
    def __init__(self, result=None, error=None):
        super().__init__()
        self.result = result
        self.error = error


class DeviceItem(ListItem):
    def __init__(self, device):
        super().__init__()
        self.device = device

    def compose(self):
        ready = self.device.is_ready
        mark = "●" if ready else "○"
        colour = "green" if ready else "dim"
        yield Label(f"[{colour}]{mark}[/] {self.device.name}")
        yield Label(
            f"   [dim]{self.device.model} · iOS {self.device.os_version} · {self.device.status}[/]"
        )


class IpaItem(ListItem):
    def __init__(self, path):
        super().__init__()
        self.path = path

    def compose(self):
        size = os.path.getsize(self.path) / 1_000_000
        stamp = datetime.fromtimestamp(os.path.getmtime(self.path))
        yield Label(os.path.basename(self.path))
        yield Label(f"   [dim]{os.path.dirname(self.path)}/ · {size:,.0f} MB · {stamp:%b %d %H:%M}[/]")


class SideloadApp(App):
    TITLE = "sideload"
    SUB_TITLE = "re-sign and install an IPA"

    CSS = """
    Screen { layout: horizontal; }

    #left { width: 46%; min-width: 44; padding: 1 2 1 2; scrollbar-gutter: stable; }
    #right { width: 1fr; padding: 1 2 1 1; }

    .heading {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 1;
    }
    .heading-first { margin: 0; }

    ListView { height: auto; max-height: 12; background: $surface; border: round $panel; }
    ListView:focus { border: round $accent; }
    ListItem { padding: 0 1; }

    #profile-summary {
        background: $surface;
        border: round $panel;
        padding: 0 1;
        height: auto;
    }

    .option-row { height: 3; width: 100%; }
    .option-row Label {
        width: 1fr;
        height: 3;
        padding: 0 1;
        content-align-vertical: middle;
    }
    .option-row Switch { width: auto; margin: 0 1 0 0; }

    #ipa-path { margin: 0 0 1 0; }

    #actions { height: auto; margin: 1 0 0 0; }
    #actions Button { margin: 0 1 0 0; }

    RichLog {
        background: $surface;
        border: round $panel;
        padding: 0 1;
        overflow-x: hidden;
    }

    #status { height: 1; padding: 0 1; }
    """

    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("s", "sideload", "Sideload"),
        Binding("b", "bootstrap", "Get profile"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, root="."):
        super().__init__()
        self.root = root
        self.settings = config.load()
        self.profile = None
        self.busy = False

    # ---- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="left"):
            yield Static("Device", classes="heading heading-first")
            yield ListView(id="devices")

            yield Static("IPA", classes="heading")
            yield ListView(id="ipas")
            yield Input(placeholder="…or a path to any .ipa", id="ipa-path")

            yield Static("Provisioning profile", classes="heading")
            yield Static("", id="profile-summary")

            yield Static("Options", classes="heading")
            with Horizontal(classes="option-row"):
                yield Label("Remove app extensions")
                yield Switch(value=self.settings.get("remove_extensions", True), id="opt-extensions")
            with Horizontal(classes="option-row"):
                yield Label("Allow the debugger to attach")
                yield Switch(value=self.settings.get("get_task_allow", True), id="opt-debug")
            with Horizontal(classes="option-row"):
                yield Label("Replace a build from another team")
                yield Switch(value=True, id="opt-uninstall")
            with Horizontal(classes="option-row"):
                yield Label("Launch after installing")
                yield Switch(value=False, id="opt-launch")

            with Horizontal(id="actions"):
                yield Button("Sideload", variant="primary", id="go")
                yield Button("Get profile", id="mint")
                yield Button("Refresh", id="refresh")

        with Vertical(id="right"):
            yield RichLog(id="log", markup=True, wrap=True, highlight=False)
            yield Static("", id="status")
        yield Footer()

    def on_mount(self):
        self.query_one("#log", RichLog).write(
            "[dim]Pick a device and an IPA, then press[/] [b]s[/][dim].[/]"
        )
        self.action_refresh()

    # ---- state ------------------------------------------------------------

    def action_refresh(self):
        self._load_devices()
        self._load_ipas()
        self._load_profile()

    def _load_devices(self):
        listing = self.query_one("#devices", ListView)
        listing.clear()
        try:
            devices = device_mod.ios_devices()
        except SideloadError as exc:
            self.notify(str(exc), severity="error")
            return
        for entry in devices:
            listing.append(DeviceItem(entry))
        if devices:
            listing.index = self._preselect(devices)

    @staticmethod
    def _preselect(devices):
        """Prefer the remembered device, then any ready one, then the first.

        Landing on a sleeping phone just because it sorts first invites a run
        that cannot succeed.
        """
        preferred = config.load().get("device", "")
        for index, entry in enumerate(devices):
            if entry.name == preferred and entry.is_ready:
                return index
        for index, entry in enumerate(devices):
            if entry.is_ready:
                return index
        return 0

    def _load_ipas(self):
        listing = self.query_one("#ipas", ListView)
        listing.clear()
        for path in cli.find_ipas(self.root):
            listing.append(IpaItem(path))
        if len(listing) > 0:
            listing.index = 0

    def _load_profile(self):
        summary = self.query_one("#profile-summary", Static)
        path = self.settings.get("profile", "")
        if not path or not os.path.exists(path):
            self.profile = None
            summary.update(
                "[yellow]none[/]\n[dim]Press [/][b]b[/][dim] to have Xcode mint one.[/]"
            )
            return
        try:
            self.profile = profile_mod.decode(path)
        except SideloadError as exc:
            self.profile = None
            summary.update(f"[red]{exc.message}[/]")
            return

        prof = self.profile
        kind = "wildcard" if prof.is_wildcard else "explicit"
        colour = "red" if prof.is_expired else "green"
        summary.update(
            f"{prof.name}\n"
            f"[dim]{prof.app_id} · {kind} · {len(prof.device_udids)} devices[/]\n"
            f"[{colour}]expires {prof.expires:%Y-%m-%d}[/]"
        )

    # ---- selection --------------------------------------------------------

    @property
    def selected_device(self):
        listing = self.query_one("#devices", ListView)
        item = listing.highlighted_child
        return item.device if isinstance(item, DeviceItem) else None

    @property
    def selected_ipa(self):
        typed = self.query_one("#ipa-path", Input).value.strip()
        if typed:
            return os.path.expanduser(typed)
        item = self.query_one("#ipas", ListView).highlighted_child
        return item.path if isinstance(item, IpaItem) else None

    def _switch(self, widget_id):
        return self.query_one(f"#{widget_id}", Switch).value

    # ---- actions ----------------------------------------------------------

    @on(Button.Pressed, "#go")
    def _button_go(self):
        self.action_sideload()

    @on(Button.Pressed, "#mint")
    def _button_mint(self):
        self.action_bootstrap()

    @on(Button.Pressed, "#refresh")
    def _button_refresh(self):
        self.action_refresh()

    def action_sideload(self):
        if self.busy:
            self.notify("Already running.", severity="warning")
            return

        device = self.selected_device
        ipa = self.selected_ipa
        if device is None:
            self.notify("No device selected.", severity="error")
            return
        if not device.is_ready:
            self.notify(f"{device.name} is {device.status}. Connect and unlock it.",
                        severity="error")
            return
        if not ipa or not os.path.exists(ipa):
            self.notify("No IPA selected.", severity="error")
            return
        if self.profile is None:
            self.notify("No provisioning profile. Press b to mint one.", severity="error")
            return

        options = pipeline.Options(
            ipa=ipa,
            device=device.udid,
            identity=self.settings.get("identity", ""),
            profile=self.profile.path,
            remove_extensions=self._switch("opt-extensions"),
            get_task_allow=self._switch("opt-debug"),
            uninstall_conflicting=self._switch("opt-uninstall"),
            launch=self._switch("opt-launch"),
        )
        self._begin(f"Sideloading {os.path.basename(ipa)} to {device.name}")
        self._run_pipeline(options)

    def action_bootstrap(self):
        if self.busy:
            self.notify("Already running.", severity="warning")
            return
        self._begin("Asking Xcode for a provisioning profile")
        self._run_bootstrap(self.selected_device)

    def _begin(self, headline):
        self.busy = True
        log = self.query_one("#log", RichLog)
        log.write("")
        log.write(f"[b]{headline}[/]")
        log.write("[dim]" + "─" * 48 + "[/]")
        self.query_one("#status", Static).update("[dim]working…[/]")
        self.query_one("#go", Button).disabled = True

    # ---- workers ----------------------------------------------------------

    @work(thread=True, exclusive=True)
    def _run_pipeline(self, options):
        try:
            result = pipeline.run(
                options,
                progress=lambda text: self.post_message(Narrate(text)),
                # The switch is the answer: the user pre-authorises the
                # uninstall rather than being interrupted by a modal
                # part-way through a long run.
                confirm=lambda _question: options.uninstall_conflicting,
            )
            self.post_message(Finished(result=result))
        except SideloadError as exc:
            self.post_message(Finished(error=exc))
        except Exception as exc:  # noqa: BLE001 - surfaced in the log, not swallowed
            self.post_message(Narrate(traceback.format_exc(), style="dim red"))
            self.post_message(Finished(error=SideloadError(str(exc))))

    @work(thread=True, exclusive=True)
    def _run_bootstrap(self, device):
        try:
            identity = keychain.find_identity(
                self.settings.get("identity") or "Apple Development"
            )
            path = bootstrap_profile.mint(
                team_id=identity.team_id,
                device=device,
                progress=lambda text: self.post_message(Narrate(text)),
            )
            self.settings["profile"] = path
            self.settings["identity"] = identity.sha1
            config.save(self.settings)
            self.post_message(Finished(result=path))
        except SideloadError as exc:
            self.post_message(Finished(error=exc))
        except Exception as exc:  # noqa: BLE001
            self.post_message(Finished(error=SideloadError(str(exc))))

    # ---- worker messages --------------------------------------------------

    @on(Narrate)
    def _on_narrate(self, message: Narrate):
        prefix = "  " if message.text.startswith("  ") else ""
        text = message.text.strip()
        style = message.style or ("dim" if prefix else "")
        log = self.query_one("#log", RichLog)
        log.write(f"{prefix}[{style}]{text}[/]" if style else f"{prefix}{text}")

    @on(Finished)
    def _on_finished(self, message: Finished):
        self.busy = False
        self.query_one("#go", Button).disabled = False
        log = self.query_one("#log", RichLog)
        status = self.query_one("#status", Static)

        if message.error is not None:
            log.write(f"[red]error[/]  {message.error.message}")
            if message.error.remedy:
                log.write(f"[yellow]try[/]    {message.error.remedy}")
            status.update("[red]failed[/]")
            self.notify(message.error.message, severity="error")
            return

        result = message.result
        if isinstance(result, str):
            log.write(f"[green]done[/]   profile saved to {result}")
            status.update("[green]profile ready[/]")
            self._load_profile()
            self.notify("Provisioning profile ready.")
            return

        log.write(f"[green]done[/]   {result.bundle_id} installed on {result.device_name}")
        if result.dropped_entitlements:
            log.write(
                f"[dim]       {len(result.dropped_entitlements)} entitlements were dropped — "
                "push, universal links, iCloud and app groups will not work, "
                "and the login session resets.[/]"
            )
        status.update("[green]installed[/]")
        self.notify(f"{result.bundle_id} installed on {result.device_name}.")

        self.settings["device"] = result.device_name
        config.save(self.settings)


def run_tui(argv=None):
    root = argv[0] if argv else "."
    SideloadApp(root=root).run()
    return 0
