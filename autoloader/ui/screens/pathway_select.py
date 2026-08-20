"""Pathway Selection Screen module."""
import logging
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static, Button, Header, Footer, Checkbox, Rule
from textual.containers import Container, Vertical, Horizontal, VerticalScroll

logger = logging.getLogger(__name__)

HARDWARE_FLASH_CAVEAT = (
    "[bold red]⚠️ CRITICAL HARDWARE SAFETY NOTICE & DISCLAIMER:[/bold red]\n"
    "[yellow]External hardware flashing involves physical disassembly, attaching SOIC clip probes, "
    "and supplying direct 3.3V power to the motherboard SPI flash chips.\n\n"
    "• Ensure 3.3V data line voltage (avoid 5V CH341A black board hazards).\n"
    "• Always verify double-read SHA256 backups match before writing.\n"
    "• Improper connections can permanently BRICK your ThinkPad.\n"
    "The user takes full responsibility for modifying physical hardware.[/yellow]"
)

class PathwaySelectScreen(Screen[None]):
    """Screen for selecting between Hardware Flashing and Software Firmware pathways."""
    
    def compose(self) -> ComposeResult:
        """Compose the dual-category pathway selection screen."""
        yield Header()
        with Container(classes="container"):
            yield Static("Firmware Modification Pathways", classes="header-text")
            
            with VerticalScroll(classes="pathways-scroll"):
                # Section 1: Hardware Flashing Menu
                yield Static("⚡ OPTION 1: HARDWARE FLASHING (External SPI Programmer)", classes="section-title")
                yield Static(HARDWARE_FLASH_CAVEAT, classes="warning-panel")
                yield Checkbox("I understand the risks and accept full responsibility for hardware flashing.", id="cb-hardware-ack")
                with Horizontal(classes="button-row"):
                    yield Button("Launch Hardware Flashing Wizard", variant="error", id="btn-hardware-flash", disabled=True)
                    yield Button("Backup & Split Dual-Chip Dumps", id="btn-hardware-backup")
                
                yield Rule()
                
                # Section 2: Software-Based Modifications Menu
                yield Static("💾 OPTION 2: SOFTWARE MODIFICATIONS & CUSTOM FIRMWARE", classes="section-title")
                with Vertical(classes="software-pathways"):
                    yield Button("1vyrain (Software Jailbreak & Downgrade - xx30 Models)", id="path-1vyrain")
                    yield Button("Coreboot ROM Builder (SeaBIOS / GRUB / TianoCore)", id="path-coreboot")
                    yield Button("Libreboot Automated Builder (100% Free Open Source)", id="path-libreboot")
                    yield Button("Canoeboot (Blob-free FSF-compliant Firmware)", id="path-canoeboot")
                    yield Button("Middleton BIOS (SATA-II & Whitelist Unlock for Penryn/GM45)", id="path-middleton")
                    yield Button("EC Modifications (Classic 7-Row Keyboard & Battery Whitelist)", id="path-ec")
                    yield Button("🧪 Practice / Dry-Run Mode (Safe QEMU VM Simulator)", variant="success", id="path-dryrun")
                
            with Horizontal(classes="button-container"):
                yield Button("Back", id="btn-back")
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Enable hardware flashing button when acknowledgment is checked."""
        if event.checkbox.id == "cb-hardware-ack":
            btn = self.query_one("#btn-hardware-flash", Button)
            btn.disabled = not event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press events."""
        btn_id = event.button.id
        if btn_id == "btn-back":
            self.app.pop_screen()
        elif btn_id == "btn-hardware-flash":
            self.app.push_screen("flash_guide")
        elif btn_id == "btn-hardware-backup":
            self.app.push_screen("backup_screen")
        elif btn_id == "path-1vyrain":
            self.app.notify("Selected 1vyrain pathway. Preparing reversion and soft-flashing workflow.", title="1vyrain Pathway")
            self.app.push_screen("flash_guide")
        elif btn_id in ["path-coreboot", "path-libreboot", "path-canoeboot"]:
            self.app.push_screen("rom_config")
        elif btn_id == "path-middleton":
            self.app.notify("Middleton BIOS provides SATA-II and Whitelist unlocks for T400/T500/X200.", title="Middleton BIOS")
            self.app.push_screen("rom_config")
        elif btn_id == "path-ec":
            self.app.push_screen("ec_config")
        elif btn_id == "path-dryrun":
            self.app.notify("Launching Safe VM Dry-Run mode...", title="Sandbox Mode")
            self.app.push_screen("model_select")
