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
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception as e:
            logger.debug("Failed to check Windows admin privileges: %s", e)
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False
