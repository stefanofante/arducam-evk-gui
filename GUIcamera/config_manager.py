"""
Configuration discovery for the GUIcamera application.

The Arducam EVK SDK is sensor-agnostic: a single Camera object is
configured at run-time from a text ``.cfg`` file that describes the
sensor mode (resolution, bit depth, output format, I2C addressing,
...). To keep the GUI itself sensor-agnostic, this module pairs every
``.cfg`` file shipped under ``configs/`` with an optional sensor
profile (``sensors/*.json``) describing the register map that should
be exposed in the GUI.

At start-up the GUI calls :meth:`ConfigManager.discover_configurations`
and :meth:`ConfigManager.discover_sensor_profiles`. The user is then
presented with a sensor selector populated from the discovered
configurations and, for each sensor, the list of available operating
modes (configuration files).

The module can also be executed directly to print a summary of the
discovered configurations and profiles, which is convenient when
adding a new sensor::

    python -m config_manager

Author:  Stefano Fante - STLINE srl
License: MIT (see ../LICENSE)
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass


@dataclass
class SensorProfile:
    """In-memory representation of a ``sensors/*.json`` file.

    Attributes:
        name: Human-readable identifier (typically the sensor part
            number).
        description: Optional one-liner shown in the UI.
        match: List of case-insensitive aliases used to bind this
            profile to a ``.cfg`` file via its ``TYPE`` field.
        registers: Raw register descriptors (see ``_template.json`` for
            the supported schema).
        guide: Optional free-form Markdown/HTML text shown in the
            help/guide tab.
        tabs: Optional per-tab hint messages keyed by tab identifier.
    """

    name: str
    description: str
    match: List[str]
    registers: List[Dict]
    guide: Optional[str] = None
    tabs: Optional[Dict[str, str]] = None


@dataclass
class CameraConfig:
    """Metadata extracted from a single camera ``.cfg`` file.

    Only the fields needed by the GUI selector are stored. The SDK
    re-parses the file when the camera is actually opened, so this
    object is purely informational.

    Attributes:
        path: Absolute path of the ``.cfg`` file on disk.
        filename: ``path.name`` cached for convenience.
        sensor_type: Value of the ``TYPE`` field (sensor identifier).
        sensor_name: Optional display name resolved from the matching
            :class:`SensorProfile`.
        resolution: ``"WIDTHxHEIGHT"`` string parsed from ``SIZE``.
        bit_depth: Output bit depth parsed from ``BIT_WIDTH``.
        format_info: Raw ``FORMAT`` field; the meaning is sensor
            specific (typically ``image_format, color_mode``).
    """

    path: Path
    filename: str
    sensor_type: str
    sensor_name: Optional[str] = None
    resolution: Optional[str] = None
    bit_depth: Optional[int] = None
    format_info: Optional[str] = None

    @property
    def display_name(self) -> str:
        """User-friendly label used in combo-boxes.

        Falls back to the bare file name when the configuration does
        not declare a resolution, so the entry is never empty.
        """
        if self.resolution:
            depth_str = f"{self.bit_depth}b" if self.bit_depth else ""
            return f"{self.sensor_name or self.sensor_type} {self.resolution} {depth_str}".strip()
        return self.filename


class ConfigManager:
    """Discovers camera configurations and sensor profiles on disk.

    The manager scans two sibling directories under the GUIcamera
    package root:

    * ``configs/`` -- one ``.cfg`` file per supported sensor mode.
    * ``sensors/`` -- one ``.json`` profile per supported sensor.

    Configurations are grouped by their ``TYPE`` field so the GUI can
    present them in a sensor-then-mode picker. A configuration without
    a matching profile is still usable for streaming; only the
    register-level features are disabled.
    """

    def __init__(self, gui_camera_root: Path):
        """Initialise the manager but do not perform any I/O yet.

        Args:
            gui_camera_root: Path to the ``GUIcamera/`` directory. The
                ``configs`` and ``sensors`` sub-directories are
                resolved relative to this root.
        """
        self.root = Path(gui_camera_root)
        self.configs_dir = self.root / "configs"
        self.sensors_dir = self.root / "sensors"

        # Discovery results. Populated by :meth:`discover_*` and read
        # by the public getters; the GUI does not access them directly.
        self._configs: List[CameraConfig] = []
        self._profiles: Dict[str, SensorProfile] = {}
        self._sensor_groups: Dict[str, List[CameraConfig]] = {}

    def discover_configurations(self) -> Dict[str, List[CameraConfig]]:
        """
        Scan the configs/ directory and discover all .cfg files.

        Returns:
            Dictionary mapping sensor type -> list of CameraConfig objects.
            Example: {"Mira220": [cfg1, cfg2], "OV5640": [cfg3]}
        """
        self._configs.clear()
        self._sensor_groups.clear()

        if not self.configs_dir.exists():
            print(f"Warning: configs directory not found at {self.configs_dir}")
            return {}

        # Scan all .cfg files
        for cfg_path in sorted(self.configs_dir.glob("*.cfg")):
            try:
                config = self._parse_cfg_file(cfg_path)
                if config:
                    self._configs.append(config)
                    sensor_type = config.sensor_type
                    if sensor_type not in self._sensor_groups:
                        self._sensor_groups[sensor_type] = []
                    self._sensor_groups[sensor_type].append(config)
            except Exception as e:
                print(f"Error parsing {cfg_path}: {e}")

        return self._sensor_groups

    def discover_sensor_profiles(self) -> Dict[str, SensorProfile]:
        """
        Scan the sensors/ directory and load all .json profiles.

        Returns:
            Dictionary mapping profile name -> SensorProfile object.
            Example: {"Mira220": SensorProfile(...), "OV5640": SensorProfile(...)}
        """
        self._profiles.clear()

        if not self.sensors_dir.exists():
            print(f"Warning: sensors directory not found at {self.sensors_dir}")
            return {}

        # Scan all .json files (skip _template.json and hidden files)
        for json_path in sorted(self.sensors_dir.glob("*.json")):
            if json_path.name.startswith("_") or json_path.name.startswith("."):
                continue
            try:
                profile = self._load_sensor_profile(json_path)
                if profile:
                    self._profiles[profile.name] = profile
            except Exception as e:
                print(f"Error loading sensor profile {json_path}: {e}")

        return self._profiles

    def get_sensors_by_group(self) -> Dict[str, List[CameraConfig]]:
        """
        Get configurations grouped by sensor type.

        Returns:
            Dictionary mapping sensor type (from .cfg TYPE field) -> list of configs.
        """
        return self._sensor_groups

    def get_profile_for_sensor_type(self, sensor_type: str) -> Optional[SensorProfile]:
        """
        Find a matching sensor profile for the given TYPE field.

        Args:
            sensor_type: The TYPE field value from a .cfg file (e.g., "Mira220")

        Returns:
            SensorProfile if found (matches case-insensitively), else None.
        """
        sensor_type_lower = sensor_type.lower()
        for profile in self._profiles.values():
            if any(m.lower() == sensor_type_lower for m in profile.match):
                return profile
        return None

    def get_all_sensor_types(self) -> List[str]:
        """
        Return sorted list of unique sensor types discovered in .cfg files.

        Returns:
            List of sensor TYPE values.
        """
        return sorted(self._sensor_groups.keys())

    def get_configs_for_sensor(self, sensor_type: str) -> List[CameraConfig]:
        """
        Get all .cfg files for a specific sensor type.

        Args:
            sensor_type: The sensor TYPE value (e.g., "Mira220")

        Returns:
            List of CameraConfig objects for this sensor.
        """
        return self._sensor_groups.get(sensor_type, [])

    # ---- Utility methods ----

    def _parse_cfg_file(self, cfg_path: Path) -> Optional[CameraConfig]:
        """
        Parse a single .cfg file and extract metadata.

        Looks for [camera parameter] section with:
        - TYPE = sensor name
        - SIZE = width, height
        - BIT_WIDTH = bit depth
        - FORMAT = format code

        Args:
            cfg_path: Path to the .cfg file

        Returns:
            CameraConfig object with extracted metadata, or None if parsing fails.
        """
        config = CameraConfig(path=cfg_path, filename=cfg_path.name, sensor_type="Unknown")

        try:
            content = cfg_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"Cannot read {cfg_path}: {e}")
            return None

        # Find [camera parameter] section
        if "[camera parameter]" not in content:
            return config

        # Extract TYPE field (sensor identifier)
        type_match = re.search(r'TYPE\s*=\s*(\w+)', content, re.IGNORECASE)
        if type_match:
            config.sensor_type = type_match.group(1).strip()

        # Extract SIZE field (e.g., "640, 480")
        size_match = re.search(r'SIZE\s*=\s*([\d\s,]+)', content, re.IGNORECASE)
        if size_match:
            size_str = size_match.group(1).strip()
            config.resolution = size_str.replace(" ", "")

        # Extract BIT_WIDTH field
        bitwidth_match = re.search(r'BIT_WIDTH\s*=\s*(\d+)', content, re.IGNORECASE)
        if bitwidth_match:
            config.bit_depth = int(bitwidth_match.group(1))

        # Extract FORMAT field
        fmt_match = re.search(r'FORMAT\s*=\s*([\d\s,]+)', content, re.IGNORECASE)
        if fmt_match:
            config.format_info = fmt_match.group(1).strip()

        return config

    def _load_sensor_profile(self, json_path: Path) -> Optional[SensorProfile]:
        """
        Load a sensor profile from a JSON file.

        Expected JSON structure:
        {
          "name": "Mira220",
          "description": "...",
          "match": ["MIRA220", "MIRA-220"],
          "registers": [...],
          "guide": "...",
          "tabs": {...}
        }

        Args:
            json_path: Path to the .json profile file

        Returns:
            SensorProfile object, or None if loading fails.
        """
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Cannot load JSON {json_path}: {e}")
            return None

        profile = SensorProfile(
            name=data.get("name", json_path.stem),
            description=data.get("description", ""),
            match=data.get("match", []),
            registers=data.get("registers", []),
            guide=data.get("guide"),
            tabs=data.get("tabs"),
        )
        return profile


def print_configuration_summary(manager: ConfigManager) -> None:
    """Print a textual summary of the discovered assets.

    Intended for command-line debugging when adding a new sensor. The
    output is plain ASCII so it survives non-UTF-8 Windows consoles.
    """
    print("\n" + "=" * 70)
    print("Configuration Summary")
    print("=" * 70)

    sensors = manager.get_all_sensor_types()
    if not sensors:
        print("No sensor configurations found.")
    else:
        print(f"\nSensor types ({len(sensors)}):")
        for sensor in sensors:
            configs = manager.get_configs_for_sensor(sensor)
            print(f"  - {sensor}: {len(configs)} config(s)")
            for cfg in configs:
                print(f"      * {cfg.display_name} ({cfg.path.name})")

    profiles = manager._profiles
    if not profiles:
        print("\nNo sensor profiles found.")
    else:
        print(f"\nSensor profiles ({len(profiles)}):")
        for name, profile in profiles.items():
            desc = (profile.description or "").strip()
            if len(desc) > 60:
                desc = desc[:60] + "..."
            print(f"  - {name}: {desc}")
            if profile.match:
                print(f"      match: {profile.match}")


if __name__ == "__main__":
    # Stand-alone smoke test: useful when adding a new sensor to make
    # sure both the .cfg and the .json profile are picked up before
    # launching the full GUI.
    gui_root = Path(__file__).parent
    manager = ConfigManager(gui_root)
    manager.discover_configurations()
    manager.discover_sensor_profiles()
    print_configuration_summary(manager)
