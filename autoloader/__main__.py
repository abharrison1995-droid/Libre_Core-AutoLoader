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
from autoloader.detection.model import ModelDetector
from autoloader.detection.bios import BIOSDetector
from autoloader.detection.ec import ECDetector
from autoloader.database.models import get_model, FirmwareBase, PayloadType
from autoloader.rom_builder.configurator import ROMConfiguration, ROMConfigurator
from autoloader.flasher.flashrom import ProgrammerType
from autoloader.orchestrator import AppState, Orchestrator

logger = logging.getLogger(__name__)
console = Console()
PROGRAMMER_ALIASES = {
    "internal": ProgrammerType.INTERNAL,
    "ch341a": ProgrammerType.CH341A,
    "raspberry-pi": ProgrammerType.RASPBERRY_PI,
    "bus-pirate": ProgrammerType.BUS_PIRATE,
}


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
    """Run hardware detection and print results."""
    logger.info("Running hardware detection...")
    console.print(Panel(f"[bold red]{PROJECT_NAME}[/bold red] v{VERSION} - Hardware Detection", border_style="red"))
    
    model_detector = ModelDetector()
    bios_detector = BIOSDetector()
    ec_detector = ECDetector()

    model_res = model_detector.detect()
    bios_info = bios_detector.detect()
    ec_info = ec_detector.detect()

    table = Table(title="Detected ThinkPad Hardware", border_style="cyan")
    table.add_column("Property", style="bold cyan")
    table.add_column("Detected Value", style="white")

    table.add_row("Model Name", model_res.model_name or "Unknown")
    table.add_row("Model ID", model_res.model_id or "Not detected")
    table.add_row("Machine Type", model_res.machine_type or "Unknown")
    table.add_row("Serial Number", model_res.serial_number or "Unknown")
    table.add_row("Detection Confidence", model_res.confidence.upper())
    table.add_row("Current BIOS Version", bios_info.version or "Unknown")
    table.add_row("BIOS Vendor", bios_info.vendor or "Unknown")
    table.add_row("Is Stock Lenovo BIOS", "Yes" if bios_info.is_stock_lenovo else "No (Custom/Coreboot)")
    table.add_row("EC Firmware Version", ec_info.version or "Unknown")
    table.add_row("EC Modified", "Yes" if ec_info.is_modified else "No")

    console.print(table)

    if model_res.model_id:
        try:
            db_model = get_model(model_res.model_id)
        except Exception:
            db_model = None

        if db_model:
            compat_table = Table(title=f"Firmware Compatibility for {db_model.name}", border_style="green")
            compat_table.add_column("Feature", style="bold green")
            compat_table.add_column("Support Status", style="white")

            compat_table.add_row("Coreboot", "✅ Supported" if db_model.coreboot_support else "❌ Unsupported")
            compat_table.add_row("Libreboot", "✅ Supported" if db_model.libreboot_support else "❌ Unsupported")
            compat_table.add_row("Canoeboot", "✅ Supported" if db_model.canoeboot_support else "❌ Unsupported")
            compat_table.add_row("1vyrain (Software Jailbreak)", "✅ Supported" if db_model.supports_1vyrain else "❌ Unsupported (Hardware Flash Required)")
            internal_str = f"✅ Yes ({db_model.internal_flash_condition})" if db_model.internal_flash_possible else "❌ External Programmer First"
            compat_table.add_row("Internal Flashing", internal_str)
            compat_table.add_row("EC Modifications", "✅ Available" if db_model.ec_moddable else "❌ Not available for this model")
            
            console.print(compat_table)


@cli.command("build-rom")
@click.option("--model", "-m", required=True, help="Target model ID (e.g. x200, x220, t430).")
@click.option("--base", "-b", type=click.Choice(["coreboot", "libreboot", "canoeboot"]), default="coreboot", help="Firmware base.")
@click.option("--payload", "-p", type=click.Choice(["seabios", "grub", "tianocore"]), default="seabios", help="Boot payload.")
@click.option("--neuter-me/--keep-me", default=True, help="Neuter Intel ME via me_cleaner.")
@click.option("--output", "-o", type=click.Path(), help="Output path for compiled ROM.")
@click.pass_context
def build_rom(ctx: click.Context, model: str, base: str, payload: str, neuter_me: bool, output: Optional[str]) -> None:
    """Validate ROM options; ROM compilation is currently unavailable."""
    logger.info("Configuring ROM...")
    try:
        firmware_base = FirmwareBase(base)
        payload_type = PayloadType(payload)

        config = ROMConfiguration(
            model_id=model.lower(),
            firmware_base=firmware_base,
            payload=payload_type,
            remove_wifi_whitelist=True,
            neuter_intel_me=neuter_me,
            output_path=Path(output) if output else None,
        )

        configurator = ROMConfigurator()
        configurator.validate(config)
        console.print(Panel(configurator.generate_summary(config), title="ROM Configuration", border_style="cyan"))
        errors: list[str] = []
        orchestrator = Orchestrator(
            AppState(model_id=model.lower()),
            on_error=errors.append,
        )
        if not orchestrator.run_build():
            raise click.ClickException(errors[-1] if errors else "ROM build is unavailable; no ROM was produced.")
    except click.ClickException:
        raise
    except AutoLoaderError as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected Error:[/bold red] {e}")
        sys.exit(1)


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
@click.option("--model", "-m", required=True, help="Detected model ID for the two-pass backup.")
@click.option("--programmer", type=click.Choice(tuple(PROGRAMMER_ALIASES)), default="ch341a")
@click.pass_context
def backup(ctx: click.Context, model: str, programmer: str) -> None:
    """Request a backup (unavailable until observed topology is validated)."""
    config: Config = ctx.obj
    errors: list[str] = []
    state = AppState(
        model_id=model.lower(),
        programmer=PROGRAMMER_ALIASES[programmer],
        backup_dir=config.backup_dir,
    )
    result = Orchestrator(state, on_error=errors.append).run_backup()
    if result is None:
        raise click.ClickException(errors[-1] if errors else "Backup capture is unavailable; no hardware was read.")


@cli.command()
@click.option("--rom", "-r", required=True, type=click.Path(exists=True, path_type=Path), help="Prepared ROM image to compare with a read-back.")
@click.option("--programmer", type=click.Choice(tuple(PROGRAMMER_ALIASES)), default="ch341a")
@click.pass_context
def verify(ctx: click.Context, rom: Path, programmer: str) -> None:
    """Read flash and compare it with a ROM through the shared orchestrator."""
    errors: list[str] = []
    state = AppState(programmer=PROGRAMMER_ALIASES[programmer])
    if not Orchestrator(state, on_error=errors.append).run_verify(rom):
        raise click.ClickException(errors[-1] if errors else "Read-back verification failed.")
    console.print("[green]Read-back comparison passed.[/green]")


@cli.command()
@click.option("--rom", "-r", required=True, type=click.Path(exists=True, path_type=Path), help="ROM image file (no write will be attempted).")
@click.pass_context
def flash(ctx: click.Context, rom: Path) -> None:
    """Physical flashing is quarantined until the shared safety backend exists."""
    errors: list[str] = []
    state = AppState()
    Orchestrator(state, on_error=errors.append).run_flash(rom)
    raise click.ClickException(errors[-1] if errors else "Physical flashing is unavailable; no write was attempted.")


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
