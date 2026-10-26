"""
General utility functions for Libre_Core AutoLoader.
"""
import os
import ctypes
import logging

logger = logging.getLogger(__name__)


def is_admin() -> bool:
    """Check if current process has elevated Administrator/root privileges."""
    if os.name == 'nt':
        try:
            windows_dll = getattr(ctypes, "windll", None)
            if windows_dll is None:
                return False
            return windows_dll.shell32.IsUserAnAdmin() != 0
        except Exception as e:
            logger.debug("Failed to check Windows admin privileges: %s", e)
            return False
    get_effective_uid = getattr(os, "geteuid", None)
    if not callable(get_effective_uid):
        return False
    return get_effective_uid() == 0
