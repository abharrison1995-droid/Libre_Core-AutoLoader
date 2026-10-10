"""ROM Backup Interface Screen module."""
import logging
from pathlib import Path
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer
from textual.containers import Container, Vertical, Horizontal
from autoloader.ui.widgets.log_panel import LogPanel
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
                self.backup_list_widget = Static("Recorded Backups:\n (Scanning backups directory...)", id="backup-list")
                yield self.backup_list_widget
                yield Static(
                    "New backup capture is unavailable until the actual chip topology and "
                    "complete recovery coverage can be verified.",
                    classes="error",
                )
                
                self.log_panel = LogPanel()
                yield self.log_panel
                
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                self.btn_create = Button("Backup unavailable", id="btn-create", disabled=True)
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
                lines = ["Recorded Backups (not reverified):"]
                for item in manifest[-4:]:  # show up to 4 most recent
                    model = item.get("model", "unknown")
                    ts = item.get("timestamp", "")
                    sha = item.get("sha256", "")[:12]
                    lines.append(f" • [{model}] {ts} - SHA256: {sha}...")
                self.backup_list_widget.update("\n".join(lines))
            else:
                self.backup_list_widget.update("Recorded Backups:\n No previous backups recorded in manifest.")
        except Exception as e:
            logger.debug(f"Could not load backup manifest: {e}")
            self.backup_list_widget.update("Recorded Backups:\n No backups found.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        if event.button.id == "btn-back":
            self.app.pop_screen()
        elif event.button.id == "btn-create":
            self.log_panel.write("[yellow]Backup capture is unavailable; no hardware was read.[/yellow]\n")
        elif event.button.id == "btn-verify":
            self._handle_verify_backups()
        elif event.button.id == "btn-restore":
            self.app.notify(
                "Restore is unavailable until a model-specific recovery plan and explicit authorization are implemented. No write was attempted.",
                title="Restore Backup",
                timeout=8.0
            )

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
