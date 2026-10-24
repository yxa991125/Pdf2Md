"""Host facts shared by configuration, doctor and nested sandboxes."""

import os
from pathlib import Path
import platform
import shutil


def is_wsl():
    return platform.system() == 'Linux' and 'microsoft' in platform.release().lower()


def bwrap_path(config):
    value = config.get('bubblewrap_path') or shutil.which('bwrap')
    if not value or not Path(value).is_file() or not os.access(value, os.X_OK):
        raise ValueError('Executable bubblewrap required; use --bwrap or PDF2MD_BWRAP')
    return os.path.abspath(value)


def driver_environment():
    if not is_wsl():
        return {}
    library = Path('/usr/lib/wsl/lib/libcuda.so.1')
    if not library.is_file():
        raise ValueError('WSL NVIDIA driver library missing: ' + str(library))
    return {'LD_LIBRARY_PATH': '/usr/lib/wsl/lib'}
