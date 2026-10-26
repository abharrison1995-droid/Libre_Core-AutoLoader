"""Dedicated mounted-screen challenge for the shared X200/X220 write route."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.widgets import Button, Checkbox, Footer, Header, Input, Static, TextArea
from textual.worker import Worker, get_current_worker

from autoloader.application_controller import (
    ApplicationAction,
    ApplicationActionResult,
    ApplicationController,
    ApplicationStatus,
    HardwareSetupAttestation,
    ProfileStatus,
)
from autoloader.ui.screens.controller_screen import ControllerScreen
from autoloader.workflow.write_approval import WriteApprovalChallenge, WriteApprovalError


class WriteConfirmationScreen(ControllerScreen):
    """Collect live setup evidence and confirm one immutable service challenge.

    A challenge is displayed only by the service worker through the private
    mounted-screen bridge. The screen's object identity and mount nonce bind
    the one-use receipt; no JSON field can provide approval evidence.
    """

    BINDINGS = [
        ("b", "back", "Back"),
        ("q", "quit", "Quit"),
    ]
    CSS = """
    WriteConfirmationScreen > Container.container { height: 1fr; }
    WriteConfirmationScreen VerticalScroll { height: 1fr; }
    WriteConfirmationScreen #write-request { height: 8; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.mount_nonce = ""
        self.worker: Worker | None = None
        self._operation_active = False
        self._submitted_once = False
        self._bridge = None
        self._challenge: WriteApprovalChallenge | None = None

    @property
    def operation_active(self) -> bool:
        return self._operation_active

    def on_mount(self) -> None:
        self.mount_nonce = secrets.token_urlsafe(32)
        controller = self.controller_app.controller
        profile = controller.selected_profile_id
        if profile not in {"x200-libreboot", "x220-coreboot"}:
            self.query_one("#write-screen-status", Static).update(
                "Only the staged X200 Libreboot and X220 Coreboot external SPI profiles can request this action."
            )
            self.query_one("#write-submit", Button).disabled = True
            return
        status = self._eligible_staged_status(controller, profile)
        if status is None:
            self.query_one("#write-submit", Button).disabled = True
            self.query_one("#write-screen-status", Static).update(
                "No validated staged X200/X220 write route is attached. Stage the exact workflow through Pathway before requesting a challenge; no probe or write is available from this screen yet."
            )
            self.query_one("#write-profile", Static).update(f"Profile: {profile} · stage: not staged")
            return
        self.query_one("#write-checkpoint", Input).value = status.checkpoint_ref or ""
        self.query_one("#write-profile", Static).update(
            f"Profile: {profile} · stage: {status.stage} · operation: {status.operation_id or 'not attached'}"
        )

    @staticmethod
    def _eligible_staged_status(controller: ApplicationController, profile: str) -> ProfileStatus | None:
        try:
            status = controller.profile_status(profile)
        except (TypeError, ValueError):
            return None
        if (
            status.status is not ApplicationStatus.STAGED_HARDWARE_PENDING
            or ApplicationAction.FLASH not in status.allowed_next_actions
            or not status.operation_id
            or not status.checkpoint_ref
            or not status.operation_journal_ref
        ):
            return None
        return status

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(classes="container"):
            yield Static("External SPI Write · Human Confirmation", classes="header-text")
            yield Static("No device probe or write is started until you submit this explicit request.", id="write-screen-status", classes="warning")
            yield Static("", id="write-profile")
            with VerticalScroll():
                yield Static(
                    "Irreversible firmware operation. This screen is limited to a reopened, staged X200/X220 whole-chip plan. "
                    "T430 remains on its separate human-run 1vyrain path. Restore and retry are not offered.",
                    classes="error",
                )
                yield Input(placeholder="Absolute staged workflow checkpoint path", id="write-checkpoint")
                yield Static("Versioned operator request JSON (never contains approval or measured voltage evidence):", classes="warning")
                yield TextArea("{}", id="write-request", language="json", show_line_numbers=True)
                yield Static("Live setup attestation — enter the person and meter details now:", classes="section-title")
                yield Input(placeholder="Operator name", id="write-actor")
                yield Input(placeholder="Meter / instrument identifier", id="write-instrument")
                yield Input(placeholder="Measured chip/programmer test point", id="write-test-point")
                yield Input(placeholder="Programmer output in mV", id="write-programmer-mv", type="integer")
                yield Input(placeholder="Observed chip voltage in mV", id="write-chip-mv", type="integer")
                yield Checkbox("I verified the programmer interface", id="write-interface-verified")
                yield Checkbox("I verified voltage/wiring at the chip", id="write-wiring-verified")
                yield Checkbox("Target is powered off before connecting the programmer", id="write-target-off")
                yield Checkbox("AC power is disconnected", id="write-ac-disconnected")
                yield Checkbox("Removable battery is removed", id="write-battery-removed")
                yield Checkbox("Chip pin-1 orientation was checked for this exact setup", id="write-pin1-verified")
                yield Checkbox("Programmer will be disconnected before repowering", id="write-disconnect-before-repower")
                yield Static(
                    "These are operator attestations, not measurements by AutoLoader. EC updates are never automated. "
                    "Record the observed EC version, evidence and whether it is already current or was updated before replacement; "
                    "or record a timestamped waiver of the later OEM EC update route.",
                    classes="warning",
                )
                yield Input(placeholder="Observed EC version (or leave blank for waiver)", id="write-ec-version")
                yield Input(placeholder="EC version evidence reference", id="write-ec-evidence")
                yield Input(placeholder="EC decision: already-current or updated-before-replacement", id="write-ec-decision")
                yield Input(placeholder="EC waiver reason, including possible loss of later OEM EC updates", id="write-ec-waiver")
                yield Static(
                    "The challenge below is generated from the reopened plan, selected ROM, fixed command, and verified backup/recovery evidence. "
                    "Any changed evidence requires a new challenge.",
                    classes="warning",
                )
                yield Static("Challenge has not been generated.", id="write-challenge-details")
                yield Input(placeholder="Exact phrase shown in the challenge", id="write-challenge-phrase", disabled=True)
                yield Static("", id="write-challenge-status")
            with Horizontal(classes="button-container"):
                yield Button("Revalidate and Request Challenge", id="write-submit", variant="primary")
                yield Button("Confirm Exact Phrase", id="write-confirm", disabled=True)
                yield Button("Cancel Challenge", id="write-cancel", disabled=True)
                yield Button("Back", id="write-back")
        yield Footer()

    def guard_navigation(self, destination: str) -> bool:
        if not self._operation_active:
            return False
        self.query_one("#write-screen-status", Static).update(
            f"The explicit write action is still active; wait for its actual result before {destination}."
        )
        return True

    def action_back(self) -> None:
        if self.guard_navigation("going back"):
            return
        self.app.pop_screen()

    def action_quit(self) -> None:
        if self.guard_navigation("quitting"):
            return
        self.app.exit()

    def on_unmount(self) -> None:
        if self._bridge is not None:
            self._bridge.invalidate("The confirmation screen was unmounted before approval.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "write-back":
            if self.guard_navigation("going back"):
                return
            self.app.pop_screen()
            return
        if button_id == "write-submit":
            self._submit()
        elif button_id == "write-confirm":
            self._confirm()
        elif button_id == "write-cancel":
            self._cancel()

    def _submit(self) -> None:
        if self._operation_active or self._submitted_once:
            return
        controller = self.controller_app.controller
        profile = controller.selected_profile_id
        if profile not in {"x200-libreboot", "x220-coreboot"}:
            self.query_one("#write-screen-status", Static).update("T430 and non-V1 profiles cannot use external SPI write approval.")
            return
        status = self._eligible_staged_status(controller, profile)
        if status is None:
            self.query_one("#write-submit", Button).disabled = True
            self.query_one("#write-screen-status", Static).update(
                "The staged X200/X220 write route is no longer available. Return to Pathway and revalidate the workflow before requesting a challenge."
            )
            return
        checkpoint = self.query_one("#write-checkpoint", Input).value.strip()
        request = self.query_one("#write-request", TextArea).text
        if checkpoint != status.checkpoint_ref:
            self.query_one("#write-screen-status", Static).update(
                "The checkpoint does not match the currently staged workflow; refresh Pathway before requesting a challenge."
            )
            return
        actor = self.query_one("#write-actor", Input).value.strip()
        instrument = self.query_one("#write-instrument", Input).value.strip()
        test_point = self.query_one("#write-test-point", Input).value.strip()
        programmer_mv = self.query_one("#write-programmer-mv", Input).value.strip()
        chip_mv = self.query_one("#write-chip-mv", Input).value.strip()
        if not checkpoint or request.strip() in {"", "{}"}:
            self.query_one("#write-screen-status", Static).update("A staged checkpoint and versioned operator request are required.")
            return
        if not actor or not instrument or not test_point or not programmer_mv.isdecimal() or not chip_mv.isdecimal():
            self.query_one("#write-screen-status", Static).update("Enter the live actor, meter, test point, and both measured voltages.")
            return
        if not self.query_one("#write-interface-verified", Checkbox).value or not self.query_one("#write-wiring-verified", Checkbox).value:
            self.query_one("#write-screen-status", Static).update("Both interface and wiring attestations are required before requesting a challenge.")
            return
        safety_checks = {
            "#write-target-off": "target powered off",
            "#write-ac-disconnected": "AC disconnected",
            "#write-battery-removed": "removable battery removed",
            "#write-pin1-verified": "chip pin-1 orientation verified",
            "#write-disconnect-before-repower": "programmer disconnect before repowering",
        }
        missing = [label for widget_id, label in safety_checks.items() if not self.query_one(widget_id, Checkbox).value]
        if missing:
            self.query_one("#write-screen-status", Static).update(
                "Required human setup attestations are missing: " + ", ".join(missing) + "."
            )
            return
        ec_version = self.query_one("#write-ec-version", Input).value.strip()
        ec_evidence = self.query_one("#write-ec-evidence", Input).value.strip()
        ec_decision = self.query_one("#write-ec-decision", Input).value.strip()
        ec_waiver = self.query_one("#write-ec-waiver", Input).value.strip()
        has_any_version_field = bool(ec_version or ec_evidence)
        has_complete_version = bool(ec_version and ec_evidence)
        if has_any_version_field and not has_complete_version:
            self.query_one("#write-screen-status", Static).update(
                "EC version and evidence reference must be entered together; a waiver cannot contain partial version fields."
            )
            return
        if has_complete_version == bool(ec_waiver):
            self.query_one("#write-screen-status", Static).update(
                "Enter an observed EC version and evidence reference, or one explicit EC waiver; choose exactly one."
            )
            return
        if has_complete_version and ec_decision not in {"already-current", "updated-before-replacement"}:
            self.query_one("#write-screen-status", Static).update(
                "Record whether the observed EC version is already current or was updated before replacement."
            )
            return
        if ec_waiver and ec_decision not in {"", "waived"}:
            self.query_one("#write-screen-status", Static).update("An EC waiver cannot also claim an observed-version decision.")
            return
        if ec_waiver:
            ec_decision = "waived"
        setup = HardwareSetupAttestation(
            actor=actor,
            instrument_id=instrument,
            test_point=test_point,
            measured_at_utc=datetime.now(timezone.utc).isoformat(),
            programmer_voltage_mv=int(programmer_mv),
            chip_voltage_mv=int(chip_mv),
            interface_verified=True,
            wiring_verified=True,
            target_powered_off=True,
            ac_disconnected=True,
            removable_battery_removed=True,
            chip_pin1_orientation_verified=True,
            programmer_disconnect_before_repower=True,
            ec_version=ec_version,
            ec_evidence_ref=ec_evidence,
            ec_observed_at_utc=datetime.now(timezone.utc).isoformat() if ec_version else "",
            ec_update_decision=ec_decision,
            ec_waiver_reason=ec_waiver,
        )
        self._operation_active = True
        self._submitted_once = True
        self._challenge = None
        self._bridge = None
        self.query_one("#write-submit", Button).disabled = True
        for widget_id in (
            "#write-checkpoint", "#write-request", "#write-actor", "#write-instrument",
            "#write-test-point", "#write-programmer-mv", "#write-chip-mv",
            "#write-interface-verified", "#write-wiring-verified",
            "#write-target-off", "#write-ac-disconnected", "#write-battery-removed",
            "#write-pin1-verified", "#write-disconnect-before-repower",
            "#write-ec-version", "#write-ec-evidence", "#write-ec-decision", "#write-ec-waiver",
        ):
            self.query_one(widget_id).disabled = True
        self.query_one("#write-screen-status", Static).update(
            "Reopening staged evidence and collecting fresh local identity/probe evidence…"
        )
        self.worker = self.submit_write_worker(controller, profile, checkpoint, request, setup, self.mount_nonce)

    @work(thread=True)
    def submit_write_worker(self, controller, profile, checkpoint, request, setup, mount_nonce) -> None:
        worker = get_current_worker()
        try:
            result = controller.request_action(
                ApplicationAction.FLASH,
                profile_id=profile,
                checkpoint_path=checkpoint,
                request_document=request,
                hardware_setup=setup,
                frontend="textual",
                confirmation_screen=self,
                screen_mount_nonce=mount_nonce,
            )
        except Exception as exc:
            result = ApplicationActionResult(
                ApplicationAction.FLASH,
                ApplicationStatus.UNKNOWN,
                "TUI_WRITE_ACTION_FAILED",
                f"The controller write request ended with an error: {exc}",
            )
        if self.is_mounted and not worker.is_cancelled:
            self.app.call_from_thread(self._show_result, result)

    def _present_write_challenge(self, challenge: WriteApprovalChallenge, bridge, mount_nonce: str) -> None:
        """UI-thread callback invoked by the private one-shot worker bridge."""
        if not self.is_mounted or self.app.screen is not self or mount_nonce != self.mount_nonce:
            bridge.invalidate("The challenge screen is stale or no longer mounted.")
            return
        self._bridge = bridge
        self._challenge = challenge
        command = " ".join((challenge.executable, *challenge.argv))
        backup_lines = [
            f"Primary manifest: {challenge.primary_manifest_path} · SHA-256 {challenge.primary_manifest_sha256}",
            f"Recovery manifest: {challenge.recovery_manifest_path} · SHA-256 {challenge.recovery_manifest_sha256}",
        ]
        backup_lines.extend(
            f"Verified backup copy {index}: {path} · SHA-256 {digest}"
            for index, (path, digest) in enumerate(zip(challenge.backup_copy_paths, challenge.backup_copy_sha256), 1)
        )
        details = (
            f"IRREVERSIBLE WRITE CHALLENGE\nModel: {challenge.model_id.upper()}\n"
            f"Chip: {challenge.chip_name} · {challenge.capacity_bytes} bytes\n"
            f"Operation: {challenge.operation_id}\nFixed executable/argv: {command}\n"
            f"Selected ROM: {challenge.artifact_path} · SHA-256 {challenge.artifact_sha256}\n"
            f"Artifact manifest: {challenge.artifact_manifest_path} · SHA-256 {challenge.artifact_manifest_sha256}\n"
            f"Live setup attestation: {challenge.setup_actor} · instrument {challenge.setup_instrument_id} · "
            f"test point {challenge.setup_test_point} · programmer {challenge.programmer_voltage_mv} mV · "
            f"chip {challenge.chip_voltage_mv} mV · interface verified {challenge.interface_verified} · "
            f"wiring verified {challenge.wiring_verified} · measured {challenge.setup_measured_at_utc}\n"
            f"Human setup attestations: target off={challenge.target_powered_off}; AC disconnected={challenge.ac_disconnected}; "
            f"removable battery removed={challenge.removable_battery_removed}; pin-1 orientation verified={challenge.chip_pin1_orientation_verified}; "
            f"programmer disconnected before repower={challenge.programmer_disconnect_before_repower}\n"
            f"EC decision={challenge.ec_update_decision}; version={challenge.ec_version or 'not supplied'}; "
            f"evidence={challenge.ec_evidence_ref or 'not supplied'}; observed={challenge.ec_observed_at_utc or 'n/a'}; "
            f"waiver={challenge.ec_waiver_reason or 'none'}; waiver recorded at setup time {challenge.setup_measured_at_utc}\n"
            + (
                f"X200 GbE MAC preservation: original={challenge.original_mac}; selected ROM before preservation="
                f"{challenge.selected_mac_before}; approved final={challenge.final_mac}; policy={challenge.mac_policy}; "
                f"source artifact SHA-256={challenge.mac_source_artifact_sha256}; "
                f"manifest={challenge.mac_preservation_manifest_path} · SHA-256 {challenge.mac_preservation_manifest_sha256}\n"
                if challenge.model_id == "x200" else ""
            )
            + "\n".join(backup_lines)
            + f"\n\nType exactly: {challenge.exact_phrase}"
        )
        self.query_one("#write-challenge-details", Static).update(details)
        self.query_one("#write-challenge-phrase", Input).disabled = False
        self.query_one("#write-confirm", Button).disabled = False
        self.query_one("#write-cancel", Button).disabled = False
        self.query_one("#write-challenge-status", Static).update(
            "Review every path, digest, chip, model and fixed command before confirming."
        )
        self.query_one("#write-screen-status", Static).update("Fresh local evidence passed initial validation; waiting for exact human confirmation.")

    def _confirm(self) -> None:
        if self._bridge is None or self._challenge is None:
            self.query_one("#write-challenge-status", Static).update("No live service challenge is awaiting confirmation.")
            return
        phrase = self.query_one("#write-challenge-phrase", Input).value
        try:
            self._bridge._confirm_from_screen(self, self.mount_nonce, phrase)
        except WriteApprovalError as exc:
            self.query_one("#write-challenge-status", Static).update(str(exc))
            return
        self.query_one("#write-challenge-phrase", Input).disabled = True
        self.query_one("#write-confirm", Button).disabled = True
        self.query_one("#write-challenge-status", Static).update("One-use confirmation submitted; the service is reacquiring evidence and validating before any writer boundary.")

    def _cancel(self) -> None:
        if self._bridge is None:
            self.query_one("#write-challenge-status", Static).update("No challenge is available to cancel yet; navigation remains guarded while the request is active.")
            return
        try:
            self._bridge._cancel_from_screen(self, self.mount_nonce)
        except WriteApprovalError as exc:
            self.query_one("#write-challenge-status", Static).update(str(exc))
            return
        self.query_one("#write-challenge-phrase", Input).disabled = True
        self.query_one("#write-confirm", Button).disabled = True
        self.query_one("#write-cancel", Button).disabled = True
        self.query_one("#write-challenge-status", Static).update("Challenge cancelled. Waiting for the controller’s blocked result.")

    def _show_result(self, result: ApplicationActionResult) -> None:
        self._operation_active = False
        self.worker = None
        self.query_one("#write-submit", Button).disabled = True
        self.query_one("#write-screen-status", Static).update(f"{result.status.value} · {result.code}: {result.message}")
        if result.evidence_refs:
            self.query_one("#write-challenge-status", Static).update("Evidence references: " + " · ".join(result.evidence_refs))
