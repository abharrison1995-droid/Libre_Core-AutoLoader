import re

ONEVY_REQUIRED_VERSIONS = {
    "x230": "2.60",
    "x230t": "2.58",
    "t430": "2.64",
    "t430s": "2.59",
    "t530": "2.60",
    "w530": "2.58",
}

# Known BIOS version history for XX30 models
BIOS_VERSION_HISTORY = {
    "x230": ["2.77", "2.76", "2.75", "2.74", "2.73", "2.72", "2.70", "2.69", "2.68", "2.67", "2.66", "2.65", "2.64", "2.63", "2.62", "2.61", "2.60", "2.59", "2.58", "2.56", "2.55", "2.54", "2.53", "2.52", "2.51", "2.50", "1.10"],
    "t430": ["2.82", "2.81", "2.80", "2.79", "2.78", "2.77", "2.76", "2.75", "2.74", "2.73", "2.72", "2.71", "2.70", "2.69", "2.68", "2.67", "2.66", "2.65", "2.64", "2.63", "2.62", "2.61", "2.60", "2.59", "2.58", "2.57", "2.56", "2.55", "2.54", "1.16"],
}

def is_1vyrain_compatible(model_id: str, current_version: str) -> bool:
    """Check if the current BIOS version is perfectly matched for 1vyrain.
    
    Args:
        model_id: The ThinkPad model ID (e.g., 'x230').
        current_version: The currently installed BIOS version string.
        
    Returns:
        True if the version matches the 1vyrain required version, False otherwise.
    """
    from autoloader.detection.bios import BIOSDetector, VersionComparison
    model_id = model_id.lower()
    if model_id not in ONEVY_REQUIRED_VERSIONS:
        return False
    
    req_ver = ONEVY_REQUIRED_VERSIONS[model_id]
    detector = BIOSDetector()
    return detector.compare_version(current_version, req_ver) == VersionComparison.MATCHES


def downgrade_needed(model_id: str, current_version: str) -> bool:
    """Determine if a BIOS downgrade is needed for 1vyrain compatibility.
    
    Args:
        model_id: The ThinkPad model ID.
        current_version: The currently installed BIOS version string.
        
    Returns:
        True if current version is newer than required or cannot be determined (fails closed), False if matching or older.
    """
    from autoloader.detection.bios import BIOSDetector, VersionComparison
    model_id = model_id.lower()
    if model_id not in ONEVY_REQUIRED_VERSIONS:
        return False
        
    required_version = ONEVY_REQUIRED_VERSIONS[model_id]
    detector = BIOSDetector()
    cmp = detector.compare_version(current_version, required_version)
    
    if cmp == VersionComparison.MATCHES:
        return False
    elif cmp == VersionComparison.OLDER:
        return False
    # Fail closed for NEWER and UNKNOWN
    return True


# Alias for backwards/cross-module compatibility
needs_bios_downgrade = downgrade_needed

def parse_bios_version_from_dmidecode(dmidecode_output: str) -> str:
    """Extract BIOS version from dmidecode output.
    
    Args:
        dmidecode_output: Raw output from `dmidecode -t bios`
        
    Returns:
        The extracted BIOS version string, or empty string if not found.
    """
    match = re.search(r"Version:\s*([^\n]+)", dmidecode_output)
    if match:
        # Example: Version: G2ETA6WW (2.66 ) -> Extract 2.66
        ver_str = match.group(1).strip()
        ver_match = re.search(r"\((\d+\.\d+)", ver_str)
        if ver_match:
            return ver_match.group(1)
    return ""
