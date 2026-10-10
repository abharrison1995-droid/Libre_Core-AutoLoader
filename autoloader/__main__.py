import logging
import sys
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
@click.option("--profile", required=True, type=click.Choice(("t430-1vyrain",)), help="T430 human-handoff profile only.")
@click.option("--checkpoint", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Checksummed pending T430 handoff checkpoint.")
def resume_checkpoint(profile: str, checkpoint: Path) -> None:
    """Resume a validated pending T430 human-handoff record; no hardware action occurs."""
    controller = ApplicationController()
    controller.select_profile(profile)
    result = controller.request_action(ApplicationAction.RESUME, profile_id=profile, checkpoint_path=checkpoint)
    if result.code != "T430_CHECKPOINT_RESUMED":
        raise click.ClickException(f"[{result.code}] {result.message}")
    status = controller.profile_status(profile)
    console.print(f"[yellow]{result.code}: {result.message}[/yellow]")
    console.print(f"Stage: {status.stage} · operation {status.operation_id}")
    console.print("No hardware read, write, IVprep execution, or 1vyrain execution was performed.")


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
@click.option("--profile", required=True, type=click.Choice(V1_PROFILE_CHOICES), help="Allowlisted model-specific V1 workflow profile.")
@click.pass_context
def build_rom(ctx: click.Context, profile: str) -> None:
    """Request model-specific preparation through the shared controller."""
    controller = ApplicationController()
    controller.select_profile(profile)
    result = controller.request_action(ApplicationAction.BUILD, profile_id=profile)
    raise click.ClickException(f"[{result.code}] {result.message}")


@cli.command()
@click.option("--output-dir", "-o", default="./iso_build", help="Target output directory for custom ISO.")
def usb(output_dir: str) -> None:
    """Prepare an output directory for the separate Archiso build script."""
    logger.info("Preparing USB output directory at %s", output_dir)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    console.print(f"[bold green]Output directory prepared:[/bold green] {out.resolve()}")
    console.print("[yellow]No ISO was built by this command. The Archiso build script requires a supported Linux build environment.[/yellow]")


@cli.command()
@click.option("--profile", required=True, type=click.Choice(V1_PROFILE_CHOICES), help="Allowlisted V1 workflow profile.")
@click.pass_context
def backup(ctx: click.Context, profile: str) -> None:
    """Request a model-specific backup through the shared controller."""
    controller = ApplicationController()
    controller.select_profile(profile)
    result = controller.request_action(ApplicationAction.BACKUP, profile_id=profile)
    raise click.ClickException(f"[{result.code}] {result.message}")


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
@click.option("--rom", "-r", required=True, type=click.Path(exists=True, path_type=Path), help="Candidate ROM path (no write will be attempted).")
@click.pass_context
def flash(ctx: click.Context, profile: str, rom: Path) -> None:
    """Physical flashing is unavailable through the shared controller."""
    controller = ApplicationController()
    controller.select_profile(profile)
    result = controller.request_action(ApplicationAction.FLASH, profile_id=profile)
    raise click.ClickException(f"[{result.code}] {result.message}")


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
