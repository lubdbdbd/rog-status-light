"""Shared data location for the tray app, hooks and command-line launchers."""
import os
from pathlib import Path

DATA = Path(os.environ.get('ROG_STATUS_LIGHT_DATA', str(Path.home() / '.rog-status-light')))
