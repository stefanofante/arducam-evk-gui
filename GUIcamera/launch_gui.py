#!/usr/bin/env python
"""
Launcher for Arducam EVK Camera GUI

Sets up the Python path and launches the GUI application.
Usage: python launch_gui.py
"""

import sys
from pathlib import Path

# Add GUIcamera to Python path
gui_camera_dir = Path(__file__).parent
sys.path.insert(0, str(gui_camera_dir))

# Import and run
from gui import main

if __name__ == "__main__":
    main()
