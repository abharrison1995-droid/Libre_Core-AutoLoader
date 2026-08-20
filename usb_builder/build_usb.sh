#!/bin/bash
# Libre_Core AutoLoader USB Image Builder
# Builds a custom Arch Linux live USB image with all flashing tools pre-installed

set -e

# Default variables
OUTPUT_DIR="./out"
INCLUDE_ROMS=""
OFFLINE_BUILD=0
PROFILE_DIR="$(dirname "$0")/archiso_profile"
WORK_DIR=$(mktemp -d /tmp/archiso-autoloader.XXXXXXXXXX 2>/dev/null || mktemp -d "${TMPDIR:-/tmp}/archiso-autoloader.XXXXXXXXXX")
trap '[ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ] && echo "Cleaning up temporary directory $WORK_DIR..." && sudo rm -rf "$WORK_DIR"' EXIT INT TERM
SRC_APP_DIR="$(dirname "$0")/.."

# Parse arguments
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --output-dir) OUTPUT_DIR="$2"; shift ;;
        --include-roms) INCLUDE_ROMS="$2"; shift ;;
        --no-network) OFFLINE_BUILD=1 ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# Check for required tools
if ! command -v mkarchiso &> /dev/null; then
    echo "Error: mkarchiso is not installed."
    echo "Please install archiso package: sudo pacman -S archiso"
    exit 1
fi

echo "=== Libre_Core AutoLoader USB Builder ==="
echo "Output Directory: $OUTPUT_DIR"
echo "Work Directory: $WORK_DIR"
if [ -n "$INCLUDE_ROMS" ]; then
    echo "Including ROMs from: $INCLUDE_ROMS"
fi
if [ "$OFFLINE_BUILD" -eq 1 ]; then
    echo "Offline build mode enabled"
fi

# Create work directory
mkdir -p "$WORK_DIR/profile"
cp -r "$PROFILE_DIR/"* "$WORK_DIR/profile/"

# Copy AutoLoader application
APP_DEST="$WORK_DIR/profile/airootfs/opt/autoloader"
mkdir -p "$APP_DEST"
echo "Copying AutoLoader application..."
cp -r "$SRC_APP_DIR/autoloader" "$APP_DEST/"
cp "$SRC_APP_DIR/pyproject.toml" "$APP_DEST/"
cp "$SRC_APP_DIR/requirements.txt" "$APP_DEST/" 2>/dev/null || true

# Include custom ROMs if specified
if [ -n "$INCLUDE_ROMS" ] && [ -d "$INCLUDE_ROMS" ]; then
    echo "Copying pre-built ROMs..."
    mkdir -p "$APP_DEST/roms"
    cp -r "$INCLUDE_ROMS/"*.rom "$APP_DEST/roms/" 2>/dev/null || true
fi

# Ensure permissions
[ -f "$WORK_DIR/profile/airootfs/opt/autoloader/start.sh" ] && chmod +x "$WORK_DIR/profile/airootfs/opt/autoloader/start.sh"
[ -f "$WORK_DIR/profile/airootfs/etc/systemd/system/autoloader.service" ] && chmod 644 "$WORK_DIR/profile/airootfs/etc/systemd/system/autoloader.service"

# Build ISO
mkdir -p "$OUTPUT_DIR"
echo "Building ISO..."
if [ "$OFFLINE_BUILD" -eq 1 ]; then
    # In offline mode, you'd typically point pacman to a local mirror or cache
    echo "Warning: Offline build requires pacman cache to be populated."
fi

# Run mkarchiso
sudo mkarchiso -v -w "$WORK_DIR/work" -o "$OUTPUT_DIR" "$WORK_DIR/profile"

# Cleanup
echo "Cleaning up..."
sudo rm -rf "$WORK_DIR"

echo "=== Build Complete ==="
echo "ISO available in: $OUTPUT_DIR"
