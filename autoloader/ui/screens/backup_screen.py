"""ROM Backup Interface Screen module."""
import logging
from pathlib import Path
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer
from textual.containers import Container, Vertical, Horizontal
from textual import work
from textual.worker import get_current_worker, Worker

from autoloader.ui.widgets.log_panel import LogPanel
from autoloader.orchestrator import Orchestrator, AppState
from autoloader.flasher.backup import BackupManager

logger = logging.getLogger(__name__)

class BackupScreen(Screen[None]):
    """Screen for handling ROM backups."""
    
    def compose(self) -> ComposeResult:
        """Compose the backup screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("ROM Backup Operations", classes="header-text")
            
            with Vertical():
                self.backup_list_widget = Static("Existing Backups:\n (Scanning backups directory...)", classes="success", id="backup-list")
                yield self.backup_list_widget
                
                self.log_panel = LogPanel()
                yield self.log_panel
                
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                self.btn_create = Button("Create Backup", variant="primary", id="btn-create")
                yield self.btn_create
                self.btn_restore = Button("Restore Guidance", id="btn-restore")
                yield self.btn_restore
                self.btn_verify = Button("Verify Backups", id="btn-verify")
                yield self.btn_verify
        yield Footer()

    def on_mount(self) -> None:
        """Refresh backup manifest on mount."""
        self._refresh_backup_list()

    def _refresh_backup_list(self) -> None:
        """Reads the backup manifest and updates display text."""
        try:
            manager = BackupManager()
            manifest = manager.get_manifest()
            if manifest:
                lines = ["Existing Verified Backups:"]
                for item in manifest[-4:]:  # show up to 4 most recent
                    model = item.get("model", "unknown")
                    ts = item.get("timestamp", "")
                    sha = item.get("sha256", "")[:12]
                    lines.append(f" • [{model}] {ts} - SHA256: {sha}...")
                self.backup_list_widget.update("\n".join(lines))
            else:
                self.backup_list_widget.update("Existing Backups:\n No previous backups recorded in manifest.")
        except Exception as e:
            logger.debug(f"Could not load backup manifest: {e}")
            self.backup_list_widget.update("Existing Backups:\n No backups found.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-back":
            self.app.pop_screen()
        elif event.button.id == "btn-create":
            self.execute_backup()
        elif event.button.id == "btn-verify":
            self._handle_verify_backups()
        elif event.button.id == "btn-restore":
            self.app.notify(
                "Restore Guidance:\nTo restore a backup, flash the saved ROM via external programmer:\n"
                "flashrom -p <programmer> -w <backup.rom>",
                title="Restore Backup",
                timeout=8.0
            )

    @work(thread=True)
    def execute_backup(self) -> None:
        """Runs the two-pass verified backup in a background worker thread."""
        worker = get_current_worker()
        app_state = getattr(self.app, "state", None) or AppState(
            model_id=getattr(self.app, "selected_model_id", None)
        )
        
        self.app.call_from_thread(setattr, self.btn_create, "disabled", True)
        self.app.call_from_thread(self.log_panel.clear)
        self.app.call_from_thread(self.log_panel.write, "[cyan]Initiating two-pass verified ROM backup...[/cyan]\n")

        def on_log(msg: str) -> None:
            if self.is_mounted and not worker.is_cancelled:
                self.app.call_from_thread(self.log_panel.write, f"{msg}\n")

        def on_complete() -> None:
            if self.is_mounted and not worker.is_cancelled:
                self.app.call_from_thread(setattr, self.btn_create, "disabled", False)
                self.app.call_from_thread(self._refresh_backup_list)

        def on_error(err_msg: str) -> None:
            if self.is_mounted and not worker.is_cancelled:
                self.app.call_from_thread(self.log_panel.write, f"[bold red]{err_msg}[/bold red]\n")
                self.app.call_from_thread(setattr, self.btn_create, "disabled", False)

        orchestrator = Orchestrator(
            app_state,
            on_log=on_log,
            on_complete=on_complete,
            on_error=on_error,
        )
        orchestrator.run_backup()

    def _handle_verify_backups(self) -> None:
        """Inspects all recorded backup pairs in the manifest."""
        manager = BackupManager()
        manifest = manager.get_manifest()
        if not manifest:
            self.log_panel.write("[yellow]No backups recorded in manifest to verify.[/yellow]\n")
            return
        self.log_panel.write(f"[cyan]Verifying {len(manifest)} recorded backup entries...[/cyan]\n")
        valid_count = 0
        for entry in manifest:
            p1 = Path(entry.get("path1", ""))
            p2 = Path(entry.get("path2", ""))
            if p1.exists() and p2.exists():
                match, sha = manager.verify_backup_pair(p1, p2)
                if match:
                    self.log_panel.write(f"[green]✔ {p1.name} matches {p2.name} (SHA: {sha[:12]}...)[/green]\n")
                    valid_count += 1
                else:
                    self.log_panel.write(f"[red]✖ Mismatch between {p1.name} and {p2.name}[/red]\n")
            else:
                self.log_panel.write(f"[yellow]⚠ Backup files missing for entry: {entry.get('timestamp')}[/yellow]\n")
        self.log_panel.write(f"[bold cyan]Verification complete: {valid_count}/{len(manifest)} valid pairs.[/bold cyan]\n")
