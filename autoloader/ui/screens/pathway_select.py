"""Profile-specific workflow status and controller-derived actions."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.events import ScreenResume
from textual.widgets import Button, Footer, Header, Static

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen
from autoloader.ui.screens.x_workflow_action import XWorkflowActionScreen


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
                yield Button("Submit T430 Handoff Stage", id="action-t430-stage")
                yield Button("View Evidence", id="action-evidence")
                yield Button("1vyrain Handoff Guidance", id="action-handoff")
            with Horizontal(classes="button-container"):
                yield Button("Prepare Workflow", id="action-prepare", disabled=True)
                yield Button("Capture / Reopen Verified Backup", id="action-accept-backup", disabled=True)
                yield Button("Build Pinned Artifact", id="action-build", disabled=True)
                yield Button("Accept Completed Artifact", id="action-accept-artifact", disabled=True)
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

    def on_screen_resume(self, event: ScreenResume) -> None:
        """Refresh controller-derived stage/actions after a child action returns."""
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
            "action-t430-stage": ApplicationAction.T430_HANDOFF_STEP,
            "action-evidence": ApplicationAction.VIEW_EVIDENCE,
            "action-handoff": ApplicationAction.HANDOFF_GUIDANCE,
            "action-prepare": ApplicationAction.PREPARE,
            "action-accept-backup": ApplicationAction.ACCEPT_BACKUP,
            "action-build": ApplicationAction.BUILD,
            "action-accept-artifact": ApplicationAction.ACCEPT_ARTIFACT,
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
                if status.profile_id == "t430-1vyrain":
                    self.app.push_screen("evidence_import")
                else:
                    self.app.push_screen(XWorkflowActionScreen(ApplicationAction.RESUME))
            return
        if button_id == "action-inspect":
            status = self.controller_app.controller.profile_status()
            if ApplicationAction.INSPECT_CHECKPOINT not in status.allowed_next_actions or not status.operation_journal_ref:
                return
            result = self.controller_app.controller.request_action(
                ApplicationAction.INSPECT_CHECKPOINT,
                profile_id=status.profile_id,
                checkpoint_path=status.operation_journal_ref,
            )
            self.app.notify(f"{result.code}: {result.message}", title="Checkpoint", timeout=12)
            return
        if button_id == "action-t430-stage":
            status = self.controller_app.controller.profile_status()
            if ApplicationAction.T430_HANDOFF_STEP in status.allowed_next_actions:
                self.app.push_screen("t430_handoff")
            return
        if button_id in {"action-prepare", "action-accept-backup", "action-build", "action-accept-artifact"}:
            action = {
                "action-prepare": ApplicationAction.PREPARE,
                "action-accept-backup": ApplicationAction.ACCEPT_BACKUP,
                "action-build": ApplicationAction.BUILD,
                "action-accept-artifact": ApplicationAction.ACCEPT_ARTIFACT,
            }[button_id]
            status = self.controller_app.controller.profile_status()
            if action in status.allowed_next_actions:
                self.app.push_screen(XWorkflowActionScreen(action))
            return
        action_by_button = {
            "action-detect": ApplicationAction.DETECT_DEVICE,
            "action-evidence": ApplicationAction.VIEW_EVIDENCE,
            "action-handoff": ApplicationAction.HANDOFF_GUIDANCE,
            "action-flash": ApplicationAction.FLASH,
            "action-restore": ApplicationAction.RESTORE,
            "action-readback": ApplicationAction.READBACK,
            "action-boot": ApplicationAction.BOOT_CHECK,
        }
        routed_action = action_by_button.get(button_id or "")
        if routed_action is None:
            return
        result = self.controller_app.controller.request_action(routed_action)
        self.app.notify(f"{result.code}: {result.message}", title="Workflow status", timeout=8)
        self.refresh_status()
