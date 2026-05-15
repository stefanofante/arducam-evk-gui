#!/usr/bin/env python
"""
Launcher for the GUIcamera Tkinter application.

This thin entry-point exists so the GUI can be started without first
installing the package or configuring ``PYTHONPATH`` manually. The
directory containing this file is prepended to :data:`sys.path` so the
sibling modules (``gui``, ``config_manager``, ``utils``) can be imported
with plain ``import`` statements regardless of the current working
directory.

Usage
-----

.. code-block:: console

    python launch_gui.py

Author:  Stefano Fante - STLINE srl
License: MIT (see ../LICENSE)
"""

import sys
from pathlib import Path

# Prepend the GUIcamera directory to ``sys.path`` so the sibling
# top-level modules resolve correctly when this script is executed
# directly (e.g. ``python launch_gui.py``) from any working directory.
gui_camera_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(gui_camera_dir))

from gui import main  # noqa: E402  (import after sys.path manipulation)

if __name__ == "__main__":
    main()
