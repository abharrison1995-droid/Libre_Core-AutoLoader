"""Import a strict JSON DeviceEvidence candidate through the controller."""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Footer, Header, Input, Static, TextArea

from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.ui.screens.controller_screen import ControllerScreen


class EvidenceImportScreen(ControllerScreen):
    """Raw evidence import is typed, referenced, and never grants compatibility."""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("Import Candidate Device Evidence", classes="header-text")
            yield Static(
                "A JSON evidence import is an unauthenticated candidate, not compatibility approval. "
                "The store checksum detects accidental corruption only.", classes="warning"
            )
            yield Input(placeholder="Evidence reference (for example: inventory-2026-10-10)", id="evidence-ref")
            yield Input(placeholder="New candidate store path (existing files are not overwritten)", id="save-path")
            yield TextArea("{}", id="evidence-json", language="json", show_line_numbers=True)
            yield Static("Load a previously saved candidate record", classes="warning")
            yield Input(placeholder="Existing candidate store path", id="load-path")
            yield Static("Resume a pending, checksummed T430 human-handoff checkpoint", classes="warning")
            yield Input(placeholder="T430 checkpoint path", id="checkpoint-path")
            yield Static("", id="evidence-result")
            with Horizontal(classes="button-container"):
                yield Button("Import Candidate", variant="primary", id="btn-import")
                yield Button("Load Candidate", id="btn-load")
                yield Button("Resume T430 Checkpoint", id="btn-resume")
                yield Button("Back", id="btn-back")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-back":
            self.app.pop_screen()
            return
        profile_id = self.controller_app.controller.selected_profile_id
        if not profile_id:
            self.query_one("#evidence-result", Static).update("Select one V1 workflow profile first.")
            return
        if event.button.id == "btn-load":
            path = self.query_one("#load-path", Input).value.strip()
            if not path:
                self.query_one("#evidence-result", Static).update("CANDIDATE_PATH_REQUIRED")
                return
            try:
                status = self.controller_app.controller.load_candidate_evidence(path, expected_profile=profile_id)
            except (OSError, ValueError) as exc:
                self.query_one("#evidence-result", Static).update(f"CANDIDATE_EVIDENCE_REJECTED: {exc}")
                return
            self.query_one("#evidence-result", Static).update(
                f"Candidate loaded · {status.status.value} · {status.stage}\n"
                + "; ".join(status.reasons)
            )
            return
        if event.button.id == "btn-resume":
            path = self.query_one("#checkpoint-path", Input).value.strip()
            result = self.controller_app.controller.request_action(
                ApplicationAction.RESUME, profile_id=profile_id, checkpoint_path=path or None
            )
            self.query_one("#evidence-result", Static).update(f"{result.code}: {result.message}")
            return
        if event.button.id != "btn-import":
            return
        evidence_ref = self.query_one("#evidence-ref", Input).value.strip()
        document = self.query_one("#evidence-json", TextArea).text
        store_path = self.query_one("#save-path", Input).value.strip()
        if not store_path:
            self.query_one("#evidence-result", Static).update("CANDIDATE_STORE_PATH_REQUIRED")
            return
        if not evidence_ref:
            evidence_ref = ApplicationController.evidence_document_reference(document)
        try:
            status = self.controller_app.controller.import_device_evidence_document(
                profile_id, document, evidence_ref=evidence_ref, store_path=store_path
            )
        except ValueError as exc:
            self.query_one("#evidence-result", Static).update(f"DEVICE_EVIDENCE_INVALID: {exc}")
            return
        self.query_one("#evidence-result", Static).update(
            f"Candidate imported and saved · {status.status.value} · {status.stage}\n"
            + "; ".join(status.reasons)
        )
