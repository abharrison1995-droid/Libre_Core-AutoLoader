import logging
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

import psutil
from autoloader.exceptions import AutoLoaderError, FlashError, SafetyError, BuildError, BIOSReversionError
from autoloader.config import Config, PROJECT_NAME, VERSION, MIN_BATTERY_PERCENT
from autoloader.detection.model import ModelDetector
from autoloader.detection.bios import BIOSDetector
from autoloader.detection.ec import ECDetector
from autoloader.database.models import get_model, FirmwareBase, PayloadType
from autoloader.rom_builder.configurator import ROMConfiguration, ROMConfigurator
from autoloader.flasher.flashrom import FlashromRunner, ProgrammerType
from autoloader.flasher.backup import BackupManager
from autoloader.flasher.verify import FlashVerifier
from autoloader.utils import is_admin

logger = logging.getLogger(__name__)
console = Console()


def setup_logging(verbose: bool, log_file: Path) -> None:
    """Set up logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers = [
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
    """Libre_Core AutoLoader - USB-bootable ThinkPad firmware modification toolkit."""
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
@click.option("--model", "-m", required=True, help="Target model ID (e.g. x230, t430, t440p, t480).")
@click.option("--base", "-b", type=click.Choice(["coreboot", "libreboot", "canoeboot"]), default="coreboot", help="Firmware base.")
@click.option("--payload", "-p", type=click.Choice(["seabios", "grub", "tianocore"]), default="seabios", help="Boot payload.")
@click.option("--neuter-me/--keep-me", default=True, help="Neuter Intel ME via me_cleaner.")
@click.option("--output", "-o", type=click.Path(), help="Output path for compiled ROM.")
@click.pass_context
def build_rom(ctx: click.Context, model: str, base: str, payload: str, neuter_me: bool, output: Optional[str]) -> None:
    """Run ROM builder in CLI mode."""
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
        console.print(Panel(configurator.generate_summary(config), title="ROM Configuration Validated", border_style="green"))
    except AutoLoaderError as e:
        console.print(f"[bold red]Configuration Error:[/bold red] {e}")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected Error:[/bold red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--output-dir", "-o", default="./iso_build", help="Target output directory for custom ISO.")
def usb(output_dir: str) -> None:
    """Prepare and build bootable recovery / autoloader USB."""
    logger.info(f"Generating USB payload in {output_dir}...")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    console.print(f"[bold green]Prepared USB payload directory at:[/bold green] {out.resolve()}")
    console.print(f"To build the ISO on Arch Linux: sudo ./usb_builder/build_usb.sh --output-dir {output_dir}")


@cli.command()
@click.option("--rom", "-r", required=True, type=click.Path(exists=True), help="ROM image file to flash.")
@click.option("--programmer", "-p", type=click.Choice(["internal", "ch341a_spi", "linux_spi", "buspirate_spi"]), default="internal")
@click.option("--chip", "-c", default=None, help="Optional flashrom chip name override (e.g. W25Q64FV).")
@click.option("--write", is_flag=True, default=False, help="Execute the write operation after safety checks pass.")
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip interactive confirmation prompt.")
@click.pass_context
def flash(ctx: click.Context, rom: str, programmer: str, chip: Optional[str], write: bool, yes: bool) -> None:
    """Run flashrom with safety checks."""
    logger.info("Initiating flashing workflow...")
    rom_path = Path(rom)
    if not rom_path.is_file():
        console.print(f"[bold red]Error:[/bold red] ROM file not found at {rom}")
        sys.exit(1)

    if programmer == "buspirate_spi":
        prog_enum = ProgrammerType.BUS_PIRATE
    elif programmer == "linux_spi":
        prog_enum = ProgrammerType.RASPBERRY_PI
    elif programmer == "ch341a_spi":
        prog_enum = ProgrammerType.CH341A
    elif programmer == "internal":
        prog_enum = ProgrammerType.INTERNAL
    else:
        try:
            prog_enum = ProgrammerType(programmer)
        except ValueError:
            console.print(f"[bold red]Invalid Programmer:[/bold red] Unknown programmer type '{programmer}'.")
            sys.exit(1)

    if prog_enum == ProgrammerType.INTERNAL and not is_admin():
        console.print("[bold red]Elevation Required:[/bold red] Internal flashing requires root / Windows Administrator privileges.")
        sys.exit(1)

    # Validate ROM structure
    verifier = FlashVerifier()
    if not verifier.check_rom_structure(rom_path):
        console.print(f"[bold red]Safety Check Failed:[/bold red] ROM {rom_path.name} failed structure verification (corrupted or blank).")
        sys.exit(1)

    # Battery safety check
    battery = psutil.sensors_battery()
    if battery:
        if not battery.power_plugged and battery.percent < MIN_BATTERY_PERCENT:
            console.print(f"[bold red]Power Warning:[/bold red] Battery level ({battery.percent}%) is below safe minimum ({MIN_BATTERY_PERCENT}%). Plug in AC power.")
            sys.exit(1)

    console.print(Panel(
        f"Target ROM: {rom_path.resolve()} ({rom_path.stat().st_size} bytes)\n"
        f"Programmer: {prog_enum.value}\n"
        f"Chip Override: {chip or 'Auto-detect'}\n"
        f"Flash Safety Check: Verified valid ROM structure",
        title="Safety-Checked Flash Operation",
        border_style="green"
    ))
    console.print("[bold green]Safety Check Passed:[/bold green] ROM image verified valid and ready.")

    if write:
        if not yes:
            if not click.confirm("Are you sure you want to write to physical hardware flash?"):
                console.print("[yellow]Flash operation cancelled by user.[/yellow]")
                return

        # Pre-flash backup
        console.print("[bold cyan]Creating mandatory 2-pass pre-flash backup...[/bold cyan]")
        backup_mgr = BackupManager()
        try:
            backup_res = backup_mgr.create_backup("current", prog_enum, "pre_flash", chip_name=chip)
            console.print(f"[bold green]Pre-flash backup verified:[/bold green] {backup_res.path1} (SHA256: {backup_res.sha256[:16]}...)")
        except Exception as e:
            console.print(f"[bold red]Pre-flash Backup Failed:[/bold red] {e}")
            console.print("[bold red]Aborting write operation to prevent unrecoverable brick without backup.[/bold red]")
            sys.exit(1)

        console.print("[bold yellow]Proceeding with flashrom write operation...[/bold yellow]")
        runner = FlashromRunner()
        layout_region = "bios" if prog_enum == ProgrammerType.INTERNAL else None
        res = runner.write_rom(prog_enum, str(rom_path.resolve()), chip_name=chip, layout_region=layout_region)
        if res.success:
            console.print("[bold green]Write Complete. Verifying physical flash...[/bold green]")
            v_res = runner.verify_rom(prog_enum, str(rom_path.resolve()), chip_name=chip, layout_region=layout_region)
            if v_res.success:
                console.print("[bold green]Flash & Verification Complete:[/bold green] ROM successfully written and verified.")
            else:
                console.print(f"[bold red]Flash Verification Failed:[/bold red] {v_res.log_output}")
                sys.exit(1)
        else:
            console.print(f"[bold red]Flash Failed:[/bold red] {res.log_output}")
            sys.exit(1)
    else:
        console.print("[cyan]Dry-run check complete. Pass --write to perform the physical hardware flash.[/cyan]")


def main() -> None:
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
