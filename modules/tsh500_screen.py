"""Custom lnxlink module -- ThinkSmart Hub 500 integrated LCD screen.

Controls the built-in AUO panel through DDC/CI (ddcutil on /dev/i2c-5). Exposes:

  - "Screen Power"      : switch (VCP 0xD6 -- off is the write-only value 5)
  - "Screen Brightness" : number 0-100 (VCP 0x10)

Requires ddcutil and i2c access (the user must be in the i2c group).
"""
import logging
import re

from lnxlink.modules.scripts.helpers import syscommand

logger = logging.getLogger("lnxlink")


class Addon:
    """Screen addon."""

    def __init__(self, lnxlink):
        """Setup addon."""
        self.name = "TSH500 Screen"
        self.lnxlink = lnxlink
        self.bus = str(
            lnxlink.config.get("settings", {})
            .get("tsh500_screen", {})
            .get("i2c_bus", "5")
        )
        # Fail fast (lnxlink disables the module) if ddcutil cannot reach the panel.
        _, _, returncode = syscommand(
            f"ddcutil --bus {self.bus} --terse getvcp 10", ignore_errors=True
        )
        if returncode != 0:
            raise SystemError(f"ddcutil cannot reach the panel on bus {self.bus}")

    def exposed_controls(self):
        """Exposes to home assistant."""
        return {
            "Screen Power": {
                "type": "switch",
                "icon": "mdi:monitor",
                "value_template": "{{ value_json.power }}",
            },
            "Screen Brightness": {
                "type": "number",
                "icon": "mdi:brightness-6",
                "min": 0,
                "max": 100,
                "step": 1,
                "unit_of_measurement": "%",
                "value_template": "{{ value_json.brightness }}",
            },
        }

    def get_info(self):
        """Gather information from the system."""
        return {"power": self._read_power(), "brightness": self._read_brightness()}

    def start_control(self, topic, data):
        """Control system."""
        if topic[1] == "screen_power":
            # off = write-only value 5 (4 is rejected); on = 1
            value = "1" if str(data).upper() == "ON" else "5"
            syscommand(f"ddcutil --bus {self.bus} --noverify setvcp D6 {value}")
        elif topic[1] == "screen_brightness":
            pct = max(0, min(100, int(float(data))))
            syscommand(f"ddcutil --bus {self.bus} setvcp 10 {pct}")

    def _read_brightness(self):
        stdout, _, _ = syscommand(f"ddcutil --bus {self.bus} --terse getvcp 10")
        m = re.search(r"VCP\s+10\s+C\s+(\d+)", stdout)  # "VCP 10 C <current> <max>"
        return int(m.group(1)) if m else None

    def _read_power(self):
        stdout, _, _ = syscommand(f"ddcutil --bus {self.bus} --terse getvcp D6")
        m = re.search(r"VCP\s+D6\s+\S+\s+x?0*([0-9a-fA-F]+)", stdout)  # "VCP D6 SNC x01"
        if not m:
            return "ON"
        return "ON" if int(m.group(1), 16) == 0x01 else "OFF"
