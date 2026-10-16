import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from autoloader.exceptions import AutoLoaderError
from autoloader.config import Config, PROJECT_NAME, VERSION
from autoloader.application_controller import ApplicationAction, ApplicationController
from autoloader.workflow.profiles import V1_PROFILES
from autoloader.workflow.write_approval import WriteApprovalError, _require_tty_file_descriptor
from autoloader.workflow.write_approval import _directory_fsync_supported
from autoloader.workflow.write_service import HardwareSetupAttestation
from usb_builder import build_usb as live_usb_builder

logger = logging.getLogger(__name__)
console = Console()
V1_PROFILE_CHOICES = tuple(V1_PROFILES)


def setup_logging(verbose: bool, log_file: Path) -> None:
    """Set up logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file),
    ]
    if verbose:
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=handlers,
    )


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """In-development ThinkPad firmware workflow toolkit."""
    config = Config.load()
    if verbose:
        config.verbose = True
        
    setup_logging(config.verbose, config.log_file)
    logger.debug("AutoLoader CLI started")
    ctx.obj = config


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Launch the Textual TUI app."""
    logger.info("Starting Textual TUI app...")
    from autoloader.app import AutoLoaderApp
    app = AutoLoaderApp()
    app.run()


@cli.command()
@click.pass_context
def detect(ctx: click.Context) -> None:
    """Capture read-only DMI/BIOS evidence through the shared controller."""
    logger.info("Running hardware detection...")
    console.print(Panel(f"[bold red]{PROJECT_NAME}[/bold red] v{VERSION} - Hardware Detection", border_style="red"))
    controller = ApplicationController()
    outcome = controller.request_action(ApplicationAction.DETECT_DEVICE)
    capture = controller.capture
    if capture is None:
        raise click.ClickException(f"[{outcome.code}] {outcome.message}")
    device = capture.device

    table = Table(title="Detected ThinkPad Hardware", border_style="cyan")
    table.add_column("Property", style="bold cyan")
    table.add_column("Detected Value", style="white")
    table.add_row("DMI Product Name", device.raw_dmi_product_name or "Unknown")
    table.add_row("DMI Product Version", device.raw_dmi_product_version or "Unknown")
    table.add_row("DMI System Family", device.raw_dmi_system_family or "Unknown")
    table.add_row("Machine Type/MTM", device.raw_mtm or "Unknown")
    table.add_row("BIOS Version (raw)", device.raw_bios_version or "Unknown")
    table.add_row("BIOS Vendor", device.raw_bios_vendor or "Unknown")
    table.add_row("Evidence reference", outcome.evidence_refs[0] if outcome.evidence_refs else "Unknown")

    console.print(table)

    console.print(f"[yellow]{outcome.message}[/yellow]")
    console.print("[yellow]Device detection is a candidate only; workflow compatibility requires model-specific evidence.[/yellow]")


@cli.command("import-evidence")
@click.option("--profile", required=True, type=click.Choice(V1_PROFILE_CHOICES), help="Allowlisted V1 workflow profile.")
@click.option("--file", "evidence_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="JSON object with raw DeviceEvidence fields.")
@click.option("--save-to", required=True, type=click.Path(dir_okay=False, path_type=Path), help="New path for a persistent candidate record; existing files are never overwritten.")
def import_evidence(profile: str, evidence_file: Path, save_to: Path) -> None:
    """Import raw fields and persist them as an unauthenticated candidate."""
    controller = ApplicationController()
    document = evidence_file.read_text(encoding="utf-8")
    evidence_ref = controller.evidence_document_reference(document)
    try:
        status = controller.import_device_evidence_document(
            profile, document, evidence_ref=evidence_ref, store_path=save_to
        )
    except ValueError as exc:
        raise click.ClickException(f"[DEVICE_EVIDENCE_INVALID] {exc}") from exc
    console.print(f"[yellow]Imported unauthenticated candidate evidence for {profile}: {evidence_ref}[/yellow]")
    console.print(f"Saved candidate record: {save_to}")
    console.print("The record checksum detects accidental corruption only; it does not authenticate the evidence.")
    console.print(f"Status: {status.status.value} · {status.stage}")
    console.print("[yellow]Import does not authenticate evidence or approve a firmware operation.[/yellow]")


@cli.command("status")
@click.option("--profile", required=True, type=click.Choice(V1_PROFILE_CHOICES), help="Allowlisted V1 workflow profile.")
@click.option("--evidence-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Reload a candidate record saved by import-evidence.")
def workflow_status(profile: str, evidence_file: Path | None) -> None:
    """Show the controller's status, evidence references, and next actions."""
    controller = ApplicationController()
    controller.select_profile(profile)
    if evidence_file is not None:
        try:
            controller.load_candidate_evidence(evidence_file, expected_profile=profile)
        except (OSError, ValueError) as exc:
            raise click.ClickException(f"[CANDIDATE_EVIDENCE_REJECTED] {exc}") from exc
    status = controller.profile_status(profile)
    table = Table(title=f"{status.profile_id} workflow status", border_style="cyan")
    table.add_column("Field", style="bold cyan")
    table.add_column("Controller result", style="white")
    table.add_row("Status", status.status.value)
    table.add_row("Compatibility decision", status.decision.value)
    table.add_row("Stage", status.stage)
    table.add_row("Reasons", "; ".join(status.reasons) or "None")
    table.add_row("Evidence references", ", ".join(status.evidence_refs) or "None")
    table.add_row("Allowed next actions", ", ".join(action.value for action in status.allowed_next_actions) or "None")
    table.add_row("Operation ID", status.operation_id or "None")
    table.add_row("Checkpoint", status.checkpoint_ref or "None")
    table.add_row("Operation evidence", "\n".join(status.evidence_summary) or "None")
    console.print(table)


@cli.command("resume-checkpoint")
@click.option("--profile", required=True, type=click.Choice(V1_PROFILE_CHOICES), help="Allowlisted V1 workflow profile.")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Checksummed handoff or operation checkpoint.")
@click.option("--request", "request_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Fresh X200/X220 operator setup request; required for those profiles.")
def resume_checkpoint(profile: str, checkpoint: Path, request_file: Path | None) -> None:
    """Resume from freshly revalidated evidence and an exact safe checkpoint."""
    controller = ApplicationController()
    controller.select_profile(profile)
    document = request_file.read_text(encoding="utf-8") if request_file is not None else None
    result = controller.request_action(
        ApplicationAction.RESUME, profile_id=profile, checkpoint_path=checkpoint, request_document=document
    )
    if result.code not in {"T430_CHECKPOINT_RESUMED", "EXACT_PLAN_WORKFLOW_RESUMED"}:
        raise click.ClickException(f"[{result.code}] {result.message}")
    status = controller.profile_status(profile)
    console.print(f"[yellow]{result.code}: {result.message}[/yellow]")
    console.print(f"Stage: {status.stage} · operation {status.operation_id}")
    console.print("No hardware read, write, IVprep execution, or 1vyrain execution was performed.")


@cli.command("t430-stage")
@click.option("--request", "request_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Version 1 T430 human-attestation JSON; DMI fields are captured locally and cannot be supplied here.")
@click.option("--checkpoint", required=True, type=click.Path(dir_okay=False, path_type=Path), help="New absolute path for an approval, or existing pending checkpoint for a return.")
def t430_stage(request_file: Path, checkpoint: Path) -> None:
    """Submit an explicit human T430 stage; no upstream program is executed."""
    controller = ApplicationController()
    controller.select_profile("t430-1vyrain")
    try:
        document = request_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"[T430_REQUEST_UNREADABLE] {exc}") from exc
    result = controller.request_action(
        ApplicationAction.T430_HANDOFF_STEP,
        profile_id="t430-1vyrain",
        checkpoint_path=checkpoint,
        request_document=document,
    )
    accepted_codes = {
        "T430_IVPREP_HANDOFF_PENDING_RETURN",
        "T430_ONEVYRAIN_HANDOFF_PENDING_RETURN",
        "T430_IVPREP_RETURN_ACCEPTED_ONEVYRAIN_PENDING_RETURN",
        "T430_POST_INSTALL_EVIDENCE_PENDING_ACCEPTANCE",
    }
    console.print(f"[{result.code}] {result.message}")
    for reason in result.reasons:
        console.print(f"- {reason}")
    console.print("No IVprep/1vyrain execution, media write, firmware write, or hardware success was performed.")
    if result.code not in accepted_codes:
        raise click.ClickException(f"[{result.code}] T430 stage was not accepted")


@cli.command("prepare-workflow")
@click.option("--profile", required=True, type=click.Choice(("x200-libreboot", "x220-coreboot")), help="Allowlisted X200/X220 profile.")
@click.option("--checkpoint", required=True, type=click.Path(dir_okay=False, path_type=Path), help="New absolute evidence checkpoint path; parent directory must already exist.")
@click.option("--request", "request_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Versioned operator request with storage paths and user-reported SPI setup measurement.")
def prepare_workflow(profile: str, checkpoint: Path, request_file: Path) -> None:
    """Capture fresh identity/probe evidence and persist the exact pre-build intent."""
    controller = ApplicationController()
    controller.select_profile(profile)
    try:
        document = request_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"[WORKFLOW_REQUEST_UNREADABLE] {exc}") from exc
    result = controller.request_action(
        ApplicationAction.PREPARE, profile_id=profile, checkpoint_path=checkpoint, request_document=document
    )
    if result.code != "WORKFLOW_INTENT_VALIDATED":
        raise click.ClickException(f"[{result.code}] {result.message}")
    console.print(f"[{result.code}] {result.message}")


@cli.command("accept-verified-backup")
@click.option("--profile", required=True, type=click.Choice(("x200-libreboot", "x220-coreboot")), help="Allowlisted X200/X220 profile.")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Existing evidence checkpoint path.")
@click.option("--request", "request_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Fresh versioned operator request; this action captures two full-chip reads.")
def accept_verified_backup(profile: str, checkpoint: Path, request_file: Path) -> None:
    """Capture a new backup or reopen an existing verified dual-read backup."""
    controller = ApplicationController()
    controller.select_profile(profile)
    try:
        document = request_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"[WORKFLOW_REQUEST_UNREADABLE] {exc}") from exc
    result = controller.request_action(
        ApplicationAction.ACCEPT_BACKUP, profile_id=profile, checkpoint_path=checkpoint, request_document=document
    )
    if result.code != "VERIFIED_BACKUP_ACCEPTED":
        raise click.ClickException(f"[{result.code}] {result.message}")
    console.print(f"[{result.code}] {result.message}")


@cli.command("accept-completed-artifact")
@click.option("--profile", required=True, type=click.Choice(("x200-libreboot", "x220-coreboot")), help="Allowlisted X200/X220 profile.")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Existing evidence checkpoint path.")
@click.option("--request", "request_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Fresh versioned operator request to revalidate local evidence.")
def accept_completed_artifact(profile: str, checkpoint: Path, request_file: Path) -> None:
    """Reopen and revalidate an existing pinned build result; no compile is run."""
    controller = ApplicationController()
    controller.select_profile(profile)
    try:
        document = request_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"[WORKFLOW_REQUEST_UNREADABLE] {exc}") from exc
    result = controller.request_action(
        ApplicationAction.ACCEPT_ARTIFACT, profile_id=profile, checkpoint_path=checkpoint, request_document=document
    )
    if result.code != "ARTIFACT_STAGED_HARDWARE_PENDING":
        raise click.ClickException(f"[{result.code}] {result.message}")
    console.print(f"[{result.code}] {result.message}")


@cli.command("inspect-checkpoint")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Checksummed X200/X220 workflow journal to inspect read-only.")
def inspect_checkpoint(checkpoint: Path) -> None:
    """Validate and summarize an operation journal without resuming it."""
    controller = ApplicationController()
    result = controller.request_action(ApplicationAction.INSPECT_CHECKPOINT, checkpoint_path=checkpoint)
    if result.code != "CHECKPOINT_INSPECTED_READ_ONLY":
        raise click.ClickException(f"[{result.code}] {result.message}")
    console.print(f"[yellow]{result.code} · verified read-only journal[/yellow]")
    console.print(result.message)
    if result.reasons:
        console.print("Transitions: " + "; ".join(result.reasons))
    console.print("Evidence references: " + ", ".join(result.evidence_refs))


@cli.command("build-rom")
@click.option("--profile", required=True, type=click.Choice(("x200-libreboot", "x220-coreboot")), help="Allowlisted X200/X220 profile.")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Existing evidence checkpoint with verified backup.")
@click.option("--request", "request_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Fresh versioned operator request; X200/X220 build requires Linux tooling.")
def build_rom(profile: str, checkpoint: Path, request_file: Path) -> None:
    """Run the pinned profile-specific build through the shared controller."""
    controller = ApplicationController()
    controller.select_profile(profile)
    try:
        document = request_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"[WORKFLOW_REQUEST_UNREADABLE] {exc}") from exc
    result = controller.request_action(
        ApplicationAction.BUILD, profile_id=profile, checkpoint_path=checkpoint, request_document=document
    )
    if result.code != "ARTIFACT_STAGED_HARDWARE_PENDING":
        raise click.ClickException(f"[{result.code}] {result.message}")
    console.print(f"[{result.code}] {result.message}")


@cli.command()
@click.option("--output-dir", "-o", default="./iso_build", help="Target output directory for custom ISO.")
def usb(output_dir: str) -> None:
    """Prepare an output directory only; this does not build an ISO."""
    logger.info("Preparing USB output directory at %s", output_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    console.print(f"[bold green]Output directory prepared:[/bold green] {out.resolve()}")
    console.print("[yellow]No ISO was built by this command. The Archiso build script requires a supported Linux build environment.[/yellow]")


@cli.command("build-usb")
@click.option("--source-root", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Absolute clean Git checkout containing this project.")
@click.option("--profile-dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Absolute checked-in Archiso profile directory.")
@click.option("--snapshot-lock", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Absolute checked-in package snapshot lock.")
@click.option("--output-dir", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Absolute existing empty output directory; files are never overwritten.")
@click.option("--work-parent", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path), help="Absolute existing directory for an owned temporary build tree.")
@click.option("--network-mode", required=True, type=click.Choice(("online", "offline")), help="Use the fixed dated archive or a verified local package repository.")
@click.option("--offline-repository", type=click.Path(exists=True, file_okay=False, path_type=Path), help="Absolute local repository root; required for offline mode.")
@click.option("--offline-manifest", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Absolute file/hash manifest for the offline repository.")
def build_usb_command(
    source_root: Path,
    profile_dir: Path,
    snapshot_lock: Path,
    output_dir: Path,
    work_parent: Path,
    network_mode: str,
    offline_repository: Path | None,
    offline_manifest: Path | None,
) -> None:
    """Build a candidate ISO through the pinned Archiso contract (Linux only)."""
    config = live_usb_builder.BuildConfig(
        source_root=source_root,
        profile_dir=profile_dir,
        output_dir=output_dir,
        work_parent=work_parent,
        snapshot_lock=snapshot_lock,
        network_mode=network_mode,
        offline_repository=offline_repository,
        offline_manifest=offline_manifest,
    )
    try:
        result = live_usb_builder.execute_build(config)
    except live_usb_builder.BuildContractError as exc:
        raise click.ClickException(f"[BUILD_BLOCKED] {exc}") from exc
    except OSError as exc:
        raise click.ClickException(f"[BUILD_FAILED] {exc}") from exc
    console.print(f"[yellow]Candidate ISO build completed:[/yellow] {result.iso_path}")
    console.print(f"SHA-256: {result.sha256}")
    console.print(f"Build provenance: {result.manifest_path}")
    console.print("BIOS boot: NOT_PERFORMED")
    console.print("UEFI boot: NOT_PERFORMED")
    console.print("Release validation: PENDING")


@cli.command()
@click.option("--profile", required=True, type=click.Choice(("x200-libreboot", "x220-coreboot")), help="Allowlisted X200/X220 workflow profile.")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Existing evidence checkpoint path.")
@click.option("--request", "request_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Fresh operator setup request; captures two full-chip reads when no backup exists.")
def backup(profile: str, checkpoint: Path, request_file: Path) -> None:
    """Capture/reopen the model-specific full-chip backup through the controller."""
    controller = ApplicationController()
    controller.select_profile(profile)
    try:
        document = request_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"[WORKFLOW_REQUEST_UNREADABLE] {exc}") from exc
    result = controller.request_action(
        ApplicationAction.ACCEPT_BACKUP, profile_id=profile, checkpoint_path=checkpoint, request_document=document
    )
    if result.code not in {"VERIFIED_BACKUP_ACCEPTED", "VERIFIED_BACKUP_REOPENED"}:
        raise click.ClickException(f"[{result.code}] {result.message}")
    console.print(f"[{result.code}] {result.message}")


@cli.command()
@click.option("--profile", required=True, type=click.Choice(V1_PROFILE_CHOICES), help="Allowlisted V1 workflow profile.")
@click.pass_context
def verify(ctx: click.Context, profile: str) -> None:
    """Report read-back status without probing or reading physical flash."""
    controller = ApplicationController()
    controller.select_profile(profile)
    result = controller.request_action(ApplicationAction.READBACK, profile_id=profile)
    raise click.ClickException(f"[{result.code}] {result.message}")


@cli.command()
@click.option("--profile", required=True, type=click.Choice(V1_PROFILE_CHOICES), help="Allowlisted V1 workflow profile.")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Exact staged X200/X220 workflow checkpoint; arbitrary ROM paths are not accepted.")
@click.option("--request", "request_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Current versioned operator setup request used to reopen the staged plan.")
@click.pass_context
def flash(ctx: click.Context, profile: str, checkpoint: Path, request_file: Path) -> None:
    """Request one exact, interactively approved X200/X220 external SPI write."""
    if profile not in {"x200-libreboot", "x220-coreboot"}:
        raise click.ClickException(
            "[PROFILE_WRITE_UNAVAILABLE] T430 uses its separate human-run IVprep/1vyrain handoff; external SPI write prompts are not offered."
        )
    _require_live_write_tty()
    try:
        request_document = request_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise click.ClickException(f"[WRITE_REQUEST_UNREADABLE] {exc}") from exc
    setup = _collect_live_write_setup()
    controller = ApplicationController()
    controller.select_profile(profile)
    result = controller.request_action(
        ApplicationAction.FLASH,
        profile_id=profile,
        checkpoint_path=checkpoint,
        request_document=request_document,
        hardware_setup=setup,
        frontend="cli-tty",
    )
    if result.evidence_refs:
        console.print("Operation evidence: " + " · ".join(result.evidence_refs))
    if result.status.value != "WRITE_VERIFIED":
        raise click.ClickException(f"[{result.code}] {result.message}")
    console.print(f"[yellow]{result.code}[/yellow] {result.message}")
    console.print("Independent full-chip read-back: matched")
    console.print("Boot validation: NOT_PERFORMED")


def _require_live_write_tty() -> None:
    """Reject noninteractive wrappers before the controller can probe hardware."""
    try:
        input_fd = _require_tty_file_descriptor(sys.stdin, "input")
        output_fd = _require_tty_file_descriptor(sys.stdout, "output")
        if not os.isatty(input_fd) or not os.isatty(output_fd):
            raise WriteApprovalError("a real interactive OS TTY is required for firmware write approval")
    except WriteApprovalError as exc:
        raise click.ClickException(f"[TTY_REQUIRED] {exc}") from exc
    if not _directory_fsync_supported():
        raise click.ClickException(
            "[DURABLE_APPROVAL_UNAVAILABLE] Physical write approval requires durable directory fsync support; no hardware probe was invoked."
        )


def _collect_live_write_setup() -> HardwareSetupAttestation:
    """Collect fresh human setup/EC attestations; never read them from JSON."""
    actor = click.prompt("Operator name", type=str).strip()
    instrument = click.prompt("Meter / instrument ID", type=str).strip()
    test_point = click.prompt("Measured programmer and chip test point", type=str).strip()
    programmer_mv = click.prompt("Measured programmer output (mV)", type=int)
    chip_mv = click.prompt("Measured target chip voltage (mV)", type=int)
    interface = click.confirm("Have you verified the external programmer interface?", default=False)
    wiring = click.confirm("Have you verified the wiring and chip orientation?", default=False)
    powered_off = click.confirm("Is the target powered off before attaching the programmer?", default=False)
    ac_disconnected = click.confirm("Is AC power disconnected?", default=False)
    battery_removed = click.confirm("Is the removable battery removed?", default=False)
    pin1 = click.confirm("Have you verified chip pin-1 orientation from this exact device/setup?", default=False)
    disconnect_before_repower = click.confirm(
        "Will you disconnect the programmer before reconnecting AC/battery or repowering?", default=False
    )
    click.echo("EC updates are never automated. Record the observed EC version and whether it is already current or was updated before replacement, or explicitly waive the later OEM EC update route.")
    ec_version = click.prompt("Observed EC version (leave blank only to record an explicit waiver)", default="", show_default=False).strip()
    if ec_version:
        ec_evidence_ref = click.prompt("EC version observation evidence reference").strip()
        ec_observed_at = datetime.now(timezone.utc).isoformat()
        ec_update_decision = click.prompt(
            "EC decision (already-current or updated-before-replacement)",
            type=click.Choice(("already-current", "updated-before-replacement"), case_sensitive=True),
        )
        ec_waiver_reason = ""
    else:
        ec_evidence_ref = ""
        ec_observed_at = ""
        ec_update_decision = "waived"
        ec_waiver_reason = click.prompt(
            "Named EC waiver reason (must acknowledge possible loss of later OEM EC updates)", type=str
        ).strip()
    return HardwareSetupAttestation(
        actor=actor,
        instrument_id=instrument,
        test_point=test_point,
        measured_at_utc=datetime.now(timezone.utc).isoformat(),
        programmer_voltage_mv=programmer_mv,
        chip_voltage_mv=chip_mv,
        interface_verified=interface,
        wiring_verified=wiring,
        target_powered_off=powered_off,
        ac_disconnected=ac_disconnected,
        removable_battery_removed=battery_removed,
        chip_pin1_orientation_verified=pin1,
        programmer_disconnect_before_repower=disconnect_before_repower,
        ec_version=ec_version,
        ec_evidence_ref=ec_evidence_ref,
        ec_observed_at_utc=ec_observed_at,
        ec_update_decision=ec_update_decision,
        ec_waiver_reason=ec_waiver_reason,
    )


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
