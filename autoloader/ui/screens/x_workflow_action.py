"""Guided operator form for the shared X200/X220 controller stages."""
from __future__ import annotations

import json

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Select, Static
from textual.worker import Worker, get_current_worker

from autoloader.application_controller import (
    ApplicationAction,
    ApplicationActionResult,
    ApplicationController,
    ApplicationStatus,
)
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.ui.screens.controller_screen import ControllerScreen


_TITLES = {
    ApplicationAction.PREPARE: "Prepare X200/X220 Workflow",
    ApplicationAction.ACCEPT_BACKUP: "Capture and Verify Full-Chip Backup",
    ApplicationAction.BUILD: "Build Pinned Firmware Artifact",
    ApplicationAction.ACCEPT_ARTIFACT: "Reopen and Accept Completed Artifact",
    ApplicationAction.RESUME: "Resume X200/X220 Checkpoint",
}
_PROGRAMMERS = (
    ("CH341A SPI programmer", ProgrammerType.CH341A.value),
    ("Raspberry Pi SPI", ProgrammerType.RASPBERRY_PI.value),
    ("Bus Pirate SPI", ProgrammerType.BUS_PIRATE.value),
)


class XWorkflowActionScreen(ControllerScreen):
    """Collect explicit operator inputs, serialize the existing schema, and submit once."""

    def __init__(self, action: ApplicationAction = ApplicationAction.PREPARE, **kwargs) -> None:
        super().__init__(**kwargs)
        if action not in _TITLES:
            raise ValueError("Unsupported X200/X220 screen action")
        self.action = action
        self.worker: Worker | None = None
        self._operation_active = False

    @property
    def operation_active(self) -> bool:
        return self._operation_active

    def guard_navigation(self, destination: str) -> bool:
        """Keep the result screen mounted until an explicit operation returns."""
        if not self._operation_active:
            return False
        self.query_one("#x-action-status", Static).update(
            f"This explicit backend action is still running; wait for its actual result before {destination}."
        )
        return True

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static(_TITLES[self.action], classes="header-text")
            yield Static("No operator request has been submitted.", id="x-action-status", classes="warning")
            with VerticalScroll():
                yield Static(
                    "This action uses the selected local device evidence. Prepare may run local DMI and read-only SPI detection; "
                    "X220 also reads the original chip for its IFD map. Backup performs two full-chip reads. "
                    "Build invokes only the pinned profile recipe. No write, restore, read-back or boot action is available here.",
                    classes="warning",
                )
                yield Input(placeholder="Absolute workflow checkpoint path", id="x-checkpoint")

                yield Static("Operator and measurement details", classes="section-title")
                yield Static(
                    "Enter the values you observed. AutoLoader does not fill in a person, time, voltage, instrument, or test point.",
                    classes="warning",
                )
                yield Input(placeholder="Operator name", id="x-actor")
                yield Input(placeholder="When you made this attestation (UTC, e.g. 2026-10-21T14:35:00Z)", id="x-attested-at")
                yield Select(_PROGRAMMERS, prompt="Choose the external SPI programmer…", allow_blank=True, id="x-programmer")
                yield Static(
                    "Enter one measured value. It records your attestation that this same voltage was measured at both chip VCC and programmer VCC.",
                    classes="warning",
                )
                yield Input(placeholder="Measured voltage (must be exactly 3300 mV)", id="x-voltage-mv", type="integer")
                yield Input(placeholder="Meter / instrument identifier", id="x-instrument-id")
                yield Checkbox(
                    "I measured the entered voltage at both chip VCC and programmer VCC",
                    id="x-test-points-verified",
                )
                yield Checkbox("I verified the programmer interface", id="x-interface-verified")
                yield Checkbox("I verified the wiring for this setup", id="x-wiring-verified")

                yield Static("Backup storage", classes="section-title")
                yield Static(
                    "Choose two existing, separate persistent directories. Confirm persistence only after checking each destination.",
                    classes="warning",
                )
                yield Input(placeholder="Absolute primary backup directory", id="x-primary-directory")
                yield Checkbox("I confirmed the primary destination is persistent storage", id="x-primary-persistent")
                yield Input(placeholder="Absolute independent recovery backup directory", id="x-recovery-directory")
                yield Checkbox("I confirmed the recovery destination is separate persistent storage", id="x-recovery-persistent")

                yield Static("Pinned build locations", classes="section-title")
                yield Input(placeholder="Absolute pinned source checkout directory", id="x-source-directory")
                yield Input(placeholder="Absolute output root directory", id="x-output-root")
                yield Input(placeholder="Build CPU count (1–256)", id="x-cpu-count", type="integer")

            with Horizontal(classes="button-container"):
                yield Button("Run This Controller Action", id="x-submit", variant="primary")
                yield Button("Back", id="x-back")
        yield Footer()

    def request_document_from_form(self) -> str:
        """Serialize explicit form entries into the unchanged version-1 schema."""
        def text_value(widget_id: str, label: str) -> str:
            value = self.query_one(f"#{widget_id}", Input).value.strip()
            if not value:
                raise ValueError(f"Enter {label}.")
            return value

        actor = text_value("x-actor", "the operator name")
        attested_at = text_value("x-attested-at", "the UTC attestation time")
        programmer = self.query_one("#x-programmer", Select).value
        if programmer not in {item[1] for item in _PROGRAMMERS}:
            raise ValueError("Choose an external SPI programmer.")

        voltage_text = text_value("x-voltage-mv", "the measured voltage in mV")
        cpu_count_text = text_value("x-cpu-count", "the build CPU count")
        try:
            voltage_mv = int(voltage_text, 10)
            cpu_count = int(cpu_count_text, 10)
        except ValueError as exc:
            raise ValueError("Voltage and CPU count must be whole numbers.") from exc
        if voltage_mv != 3300:
            raise ValueError("Enter the measured voltage of exactly 3300 mV.")
        if not 1 <= cpu_count <= 256:
            raise ValueError("Build CPU count must be from 1 through 256.")

        for widget_id, message in (
            ("x-test-points-verified", "Confirm that both required VCC test points were measured."),
            ("x-interface-verified", "Confirm the programmer interface was checked."),
            ("x-wiring-verified", "Confirm the wiring was checked."),
            ("x-primary-persistent", "Confirm the primary backup destination is persistent."),
            ("x-recovery-persistent", "Confirm the separate recovery destination is persistent."),
        ):
            if not self.query_one(f"#{widget_id}", Checkbox).value:
                raise ValueError(message)

        document = {
            "version": 1,
            "actor": actor,
            "attested_at": attested_at,
            "programmer": programmer,
            "measured_voltage_mv": voltage_mv,
            "interface_verified": True,
            "wiring_verified": True,
            "instrument_id": text_value("x-instrument-id", "the meter / instrument identifier"),
            "measurement_test_point": "chip_vcc_and_programmer_vcc",
            "primary_directory": text_value("x-primary-directory", "the primary backup directory"),
            "recovery_directory": text_value("x-recovery-directory", "the independent recovery directory"),
            "primary_persistent_confirmed": True,
            "recovery_persistent_confirmed": True,
            "source_directory": text_value("x-source-directory", "the pinned source checkout directory"),
            "output_root": text_value("x-output-root", "the output root directory"),
            "cpu_count": cpu_count,
        }
        return json.dumps(document, sort_keys=True, separators=(",", ":"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "x-back":
            if self.guard_navigation("leaving this screen"):
                return
            self.app.pop_screen()
            return
        if event.button.id != "x-submit":
            return
        profile_id = self.controller_app.controller.selected_profile_id
        checkpoint = self.query_one("#x-checkpoint", Input).value.strip()
        if profile_id not in ("x200-libreboot", "x220-coreboot"):
            self.query_one("#x-action-status", Static).update("Select the X200 Libreboot or X220 Coreboot profile first.")
            return
        if not checkpoint:
            self.query_one("#x-action-status", Static).update("Enter the workflow checkpoint path.")
            return
        try:
            request = self.request_document_from_form()
        except ValueError as exc:
            self.query_one("#x-action-status", Static).update(str(exc))
            return

        self.query_one("#x-submit", Button).disabled = True
        self.query_one("#x-action-status", Static).update("Submitting explicit controller request…")
        self._operation_active = True
        self.worker = self.submit_request_worker(
            self.controller_app.controller, self.action, profile_id, checkpoint, request
        )

    @work(thread=True)
    def submit_request_worker(
        self,
        controller: ApplicationController,
        action: ApplicationAction,
        profile_id: str,
        checkpoint: str,
        request_document: str,
    ) -> None:
        """Do potentially slow backend work off-thread; no widget lookup here."""
        worker = get_current_worker()
        try:
            result = controller.request_action(
                action,
                profile_id=profile_id,
                checkpoint_path=checkpoint,
                request_document=request_document,
            )
        except Exception as exc:
            result = ApplicationActionResult(
                action,
                ApplicationStatus.UNKNOWN,
                "WORKFLOW_ACTION_FAILED",
                f"The controller action ended with an error: {exc}",
            )
        if self.is_mounted and not worker.is_cancelled:
            self.app.call_from_thread(self._show_result, result)

    def _show_result(self, result: ApplicationActionResult) -> None:
        self._operation_active = False
        self.worker = None
        self.query_one("#x-submit", Button).disabled = False
        self.query_one("#x-action-status", Static).update(f"{result.code}: {result.message}")
