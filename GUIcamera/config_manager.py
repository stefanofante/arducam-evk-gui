"""
Configuration manager.

Scans .cfg files in configs/ and .json profiles in sensors/,
groups them by sensor TYPE, and exposes lookup helpers.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class SensorProfile:
    """Represents a sensor hardware profile."""
    name: str
    description: str
    match: List[str]  # TYPE field match patterns
    registers: List[Dict]
    guide: Optional[str] = None
    tabs: Optional[Dict[str, str]] = None


@dataclass
class CameraConfig:
    """Represents a single camera configuration file."""
    path: Path
    filename: str
    sensor_type: str  # TYPE field from .cfg
    sensor_name: Optional[str] = None  # Display name (from profile match)
    resolution: Optional[str] = None  # SIZE field (e.g., "640x480")
    bit_depth: Optional[int] = None  # BIT_WIDTH field
    format_info: Optional[str] = None  # FORMAT field description

    @property
    def display_name(self) -> str:
        """User-friendly config name."""
        if self.resolution:
            depth_str = f"{self.bit_depth}b" if self.bit_depth else ""
            return f"{self.sensor_name or self.sensor_type} {self.resolution} {depth_str}".strip()
        return self.filename


class ConfigManager:
    """Manages sensor configurations and profiles."""

    def __init__(self, gui_camera_root: Path):
        """
        Initialize the configuration manager.

        Args:
            gui_camera_root: Path to the GUIcamera directory root
        """
        self.root = Path(gui_camera_root)
        self.configs_dir = self.root / "configs"
        self.sensors_dir = self.root / "sensors"

        # State
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
    """
    Print a summary of discovered configurations and profiles.

    Useful for debugging and validation.
    """
    print("\n" + "=" * 70)
    print("Configuration Summary")
    print("=" * 70)

    sensors = manager.get_all_sensor_types()
    if not sensors:
        print("❌ No sensor configurations found.")
    else:
        print(f"\n📦 Sensor Types ({len(sensors)}):")
        for sensor in sensors:
            configs = manager.get_configs_for_sensor(sensor)
            print(f"  • {sensor}: {len(configs)} config(s)")
            for cfg in configs:
                print(f"      - {cfg.display_name} ({cfg.path.name})")

    profiles = manager._profiles
    if not profiles:
        print("\n❌ No sensor profiles found.")
    else:
        print(f"\n🎨 Sensor Profiles ({len(profiles)}):")
        for name, profile in profiles.items():
            print(f"  • {name}: {profile.description[:60]}...")
            if profile.match:
                print(f"      Match: {profile.match}")


if __name__ == "__main__":
    # Test / validation
    gui_root = Path(__file__).parent
    manager = ConfigManager(gui_root)
    manager.discover_configurations()
    manager.discover_sensor_profiles()
    print_configuration_summary(manager)
