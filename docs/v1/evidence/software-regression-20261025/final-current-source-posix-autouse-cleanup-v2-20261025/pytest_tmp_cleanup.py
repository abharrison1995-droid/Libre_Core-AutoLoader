import os
import shutil
from pathlib import Path
import pytest

@pytest.fixture(autouse=True)
def cleanup_test_tmp_path(tmp_path):
    base_value = os.environ.get("LC_PYTEST_BASE")
    if not base_value:
        raise RuntimeError("LC_PYTEST_BASE is required")
    base = Path(base_value).resolve()
    path = Path(tmp_path)
    if path.is_symlink() or not path.resolve().is_relative_to(base):
        raise RuntimeError("pytest tmp_path escaped basetemp")
    yield
    if path.exists():
        path.chmod(0o700)
        for current, dirs, files in os.walk(path, topdown=True, followlinks=False):
            parent = Path(current)
            parent.chmod(0o700)
            for name in list(dirs):
                child = parent / name
                if child.is_symlink():
                    dirs.remove(name)
                else:
                    child.chmod(0o700)
        shutil.rmtree(path)
