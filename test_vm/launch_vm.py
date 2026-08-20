"""
Libre_Core AutoLoader - 1-Click VM Sandbox & Hardware Simulator
Launches QEMU with ThinkPad-specific SMBIOS tables for zero-risk dry runs.
"""

import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm

console = Console()

VM_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = VM_DIR.parent
PROFILES_FILE = VM_DIR / "smbios_profiles.json"
MOCK_CHIPS_DIR = VM_DIR / "mock_chips"


def find_qemu_executable() -> str | None:
    """Find qemu-system-x86_64 in PATH or standard install directories."""
    qemu_cmd = shutil.which("qemu-system-x86_64")
    if qemu_cmd:
        return qemu_cmd

    # Standard Windows install locations
    common_paths = [
        Path(r"C:\Program Files\qemu\qemu-system-x86_64.exe"),
        Path(r"C:\Program Files (x86)\qemu\qemu-system-x86_64.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "qemu" / "qemu-system-x86_64.exe"
    ]
    for p in common_paths:
        if p.exists():
            return str(p)
    return None


def prompt_install_qemu() -> bool:
    """Offer 1-click install via winget on Windows."""
    console.print(Panel(
        "[bold yellow]QEMU Emulator is not currently detected on your system.[/bold yellow]\n\n"
        "QEMU is required to boot the isolated virtual testing environment with ThinkPad SMBIOS emulation.",
        title="Dependency Check",
        border_style="yellow"
    ))
    
    if shutil.which("winget"):
        if Confirm.ask("Would you like to install QEMU automatically via Windows Package Manager (winget)?"):
            console.print("[cyan]Running: winget install SoftwareFreedomConservancy.QEMU ...[/cyan]")
            try:
                subprocess.run(["winget", "install", "--id", "SoftwareFreedomConservancy.QEMU", "-e", "--accept-package-agreements", "--accept-source-agreements"], check=True)
                console.print("[bold green]QEMU installed successfully! Please restart this launcher.[/bold green]")
                return True
            except Exception as e:
                console.print(f"[bold red]Automatic installation encountered an issue:[/bold red] {e}")
    
    console.print("\n[bold]Manual Installation Options:[/bold]")
    console.print("1. Download installer from: https://www.qemu.org/download/#windows")
    console.print("2. Or install via winget: winget install SoftwareFreedomConservancy.QEMU\n")
    return False


def load_profiles() -> dict:
    if PROFILES_FILE.exists():
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    console.clear()
    console.print(Panel.fit(
        "[bold red]Libre_Core AutoLoader[/bold red] - [bold white]Safe VM Dry-Run & Simulator[/bold white]\n"
        "[dim]Test drive the firmware workflow with simulated ThinkPad hardware and zero bricking risk.[/dim]",
        border_style="red"
    ))

    qemu_bin = find_qemu_executable()
    if not qemu_bin:
        prompt_install_qemu()
        # Offer local safe dry run mode if QEMU isn't ready
        if Confirm.ask("\nWould you like to test the AutoLoader TUI in Safe Local Simulation Mode instead?"):
            console.print("[cyan]Launching AutoLoader in sandbox mode...[/cyan]")
            subprocess.run([sys.executable, "-m", "autoloader", "run"], cwd=str(PROJECT_ROOT))
        return

    profiles = load_profiles()
    if not profiles:
        console.print("[bold red]Error: No SMBIOS profiles found in smbios_profiles.json[/bold red]")
        return

    table = Table(title="Available ThinkPad Simulation Profiles", border_style="cyan")
    table.add_column("Option", style="bold yellow", justify="center")
    table.add_column("ThinkPad Model", style="bold white")
    table.add_column("Generation", style="cyan")
    table.add_column("BIOS Version Emulated", style="green")
    table.add_column("Flash Topology", style="dim")

    profile_keys = list(profiles.keys())
    for idx, key in enumerate(profile_keys, start=1):
        p = profiles[key]
        table.add_row(
            str(idx),
            p.get("name", key),
            p.get("generation", "Unknown"),
            p.get("bios_version", "Stock"),
            p.get("chips", "SOIC-8")
        )

    console.print(table)
    console.print("[dim]Enter profile number (1-%d), or 'q' to quit:[/dim]" % len(profile_keys))

    choice = Prompt.ask("Select profile", default="1")
    if choice.lower() == "q":
        return

    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(profile_keys)):
            console.print("[red]Invalid selection.[/red]")
            return
        selected_key = profile_keys[idx]
    except ValueError:
        console.print("[red]Invalid input.[/red]")
        return

    selected_profile = profiles[selected_key]
    console.print(Panel(
        f"[bold green]Starting Virtual Machine...[/bold green]\n"
        f"Simulating: [bold]{selected_profile['name']}[/bold]\n"
        f"Machine Type: [cyan]{selected_profile['machine_type']}[/cyan]\n"
        f"BIOS: [yellow]{selected_profile['bios_version']}[/yellow]\n"
        f"Memory: [white]2048 MB RAM[/white]",
        title="QEMU Boot Configuration",
        border_style="green"
    ))

    # Check for live ISO or fallback to direct kernel/disk
    iso_candidates = list((PROJECT_ROOT / "build_usb").glob("*.iso")) if (PROJECT_ROOT / "build_usb").exists() else []
    
    qemu_cmd = [
        qemu_bin,
        "-m", "2048",
        "-smp", "2",
        "-vga", "std",
        "-name", f"AutoLoader-Sim-{selected_key}",
    ]

    # Add SMBIOS injection arguments
    qemu_cmd.extend(selected_profile.get("smbios_args", []))

    if iso_candidates:
        iso_path = str(iso_candidates[0])
        console.print(f"[bold cyan]Attaching Live USB ISO:[/bold cyan] {iso_path}")
        qemu_cmd.extend(["-cdrom", iso_path, "-boot", "d"])
    else:
        console.print("[dim]No pre-built Archiso ISO found in build_usb/. Booting QEMU with SMBIOS simulator display...[/dim]")

    console.print("\n[bold]Launching QEMU window... Close the QEMU window when finished.[/bold]")
    try:
        subprocess.run(qemu_cmd)
    except Exception as e:
        console.print(f"[bold red]Failed to run QEMU:[/bold red] {e}")


if __name__ == "__main__":
    main()
