"""Profile-specific workflow status and controller-derived actions."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Static

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen


class PathwaySelectScreen(ControllerScreen):
    """Show only actions advertised by the shared application controller."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Selected V1 Workflow", classes="header-text")
            yield Static("No profile selected", id="profile-name")
            yield Static("UNKNOWN", id="profile-status", classes="warning")
            with VerticalScroll():
                yield Static("", id="profile-reasons")
                yield Static("", id="profile-evidence")
                yield Static("", id="profile-operation")
                yield Static("", id="profile-next-actions")
            with Horizontal(classes="button-container"):
                yield Button("Detect Device", id="action-detect")
                yield Button("Import Candidate Evidence", id="action-import")
                yield Button("Resume / Load Checkpoint", id="action-resume")
                yield Button("Inspect Operation Checkpoint", id="action-inspect")
                yield Button("View Evidence", id="action-evidence")
                yield Button("1vyrain Handoff Guidance", id="action-handoff")
            with Horizontal(classes="button-container"):
                yield Button("Prepare Workflow", id="action-prepare", disabled=True)
                yield Button("Backup", id="action-backup", disabled=True)
                yield Button("Build", id="action-build", disabled=True)
            with Horizontal(classes="button-container"):
                yield Button("Flash", id="action-flash", disabled=True)
                yield Button("Restore", id="action-restore", disabled=True)
                yield Button("Read-back", id="action-readback", disabled=True)
                yield Button("Boot Check", id="action-boot", disabled=True)
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
                yield Button("Preparation Details", id="btn-details")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_status()

    def refresh_status(self) -> None:
        controller: ApplicationController = self.controller_app.controller
        if not controller.selected_profile_id:
            self.query_one("#profile-name", Static).update("No V1 profile selected")
            self.query_one("#profile-status", Static).update("UNKNOWN")
            return
        status = controller.profile_status()
        self.query_one("#profile-name", Static).update(
            f"{status.model_id.upper()} · {status.firmware_family} · {status.profile_id}"
        )
        self.query_one("#profile-status", Static).update(f"{status.status.value} · {status.stage}")
        self.query_one("#profile-reasons", Static).update("Reasons: " + "; ".join(status.reasons))
        self.query_one("#profile-evidence", Static).update(
            "Evidence references: " + (", ".join(status.evidence_refs) if status.evidence_refs else "none")
        )
        operation_details = [f"Operation ID: {status.operation_id or 'none'}"]
        operation_details.append(f"Checkpoint: {status.checkpoint_ref or 'none'}")
        operation_details.extend(status.evidence_summary)
        self.query_one("#profile-operation", Static).update("Operation evidence:\n" + "\n".join(operation_details))
        actions = ", ".join(action.value for action in status.allowed_next_actions) or "none"
        self.query_one("#profile-next-actions", Static).update(f"Allowed next actions: {actions}")
        mapping = {
            "action-detect": ApplicationAction.DETECT_DEVICE,
            "action-import": ApplicationAction.IMPORT_EVIDENCE,
            "action-resume": ApplicationAction.RESUME,
            "action-inspect": ApplicationAction.INSPECT_CHECKPOINT,
            "action-evidence": ApplicationAction.VIEW_EVIDENCE,
            "action-handoff": ApplicationAction.HANDOFF_GUIDANCE,
            "action-prepare": ApplicationAction.PREPARE,
            "action-backup": ApplicationAction.BACKUP,
            "action-build": ApplicationAction.BUILD,
            "action-flash": ApplicationAction.FLASH,
            "action-restore": ApplicationAction.RESTORE,
            "action-readback": ApplicationAction.READBACK,
            "action-boot": ApplicationAction.BOOT_CHECK,
        }
        for widget_id, action in mapping.items():
            self.query_one(f"#{widget_id}", Button).disabled = action not in status.allowed_next_actions

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "btn-back":
            self.app.pop_screen()
            return
        if button_id == "btn-details":
            self.app.push_screen("rom_config")
            return
        if button_id == "action-import":
            status = self.controller_app.controller.profile_status()
            if ApplicationAction.IMPORT_EVIDENCE in status.allowed_next_actions:
                self.app.push_screen("evidence_import")
            return
        if button_id == "action-resume":
            status = self.controller_app.controller.profile_status()
            if ApplicationAction.RESUME in status.allowed_next_actions:
                self.app.push_screen("evidence_import")
            return
        if button_id == "action-inspect":
            status = self.controller_app.controller.profile_status()
            if ApplicationAction.INSPECT_CHECKPOINT not in status.allowed_next_actions or not status.checkpoint_ref:
                return
            result = self.controller_app.controller.request_action(
                ApplicationAction.INSPECT_CHECKPOINT,
                profile_id=status.profile_id,
                checkpoint_path=status.checkpoint_ref,
            )
            self.app.notify(f"{result.code}: {result.message}", title="Checkpoint", timeout=12)
            return
        action_by_button = {
            "action-detect": ApplicationAction.DETECT_DEVICE,
            "action-evidence": ApplicationAction.VIEW_EVIDENCE,
            "action-handoff": ApplicationAction.HANDOFF_GUIDANCE,
            "action-prepare": ApplicationAction.PREPARE,
            "action-backup": ApplicationAction.BACKUP,
            "action-build": ApplicationAction.BUILD,
            "action-flash": ApplicationAction.FLASH,
            "action-restore": ApplicationAction.RESTORE,
            "action-readback": ApplicationAction.READBACK,
            "action-boot": ApplicationAction.BOOT_CHECK,
        }
        action = action_by_button.get(button_id or "")
        if action is None:
            return
        result = self.controller_app.controller.request_action(action)
        self.app.notify(f"{result.code}: {result.message}", title="Workflow status", timeout=8)
        self.refresh_status()
