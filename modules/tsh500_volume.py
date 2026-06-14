"""Custom lnxlink module -- ThinkSmart Hub 500 audio volume.

The built-in USB card (17ef:a017) DSP ignores UAC volume requests, so volume is a
software attenuation handled by PipeWire/PulseAudio. This module drives the volume
of that specific sink via pactl. Exposes:

  - "Volume" : number 0-100 %

Sink resolution is by USB **vendor:product id** (default 17ef:a017), read from the
sink properties (device.vendor.id / device.product.id). This is immune to other
generic "USB Audio" devices (docks, DACs, headsets) that would otherwise collide
with a name-substring match. A `sink_match` name substring is kept only as an
optional fallback for non-standard setups. Both are overridable via settings.
"""
import logging
import re

from lnxlink.modules.scripts.helpers import syscommand

logger = logging.getLogger("lnxlink")


class Addon:
    """Volume addon."""

    def __init__(self, lnxlink):
        """Setup addon."""
        self.name = "TSH500 Volume"
        self.lnxlink = lnxlink
        settings = lnxlink.config.get("settings", {}).get("tsh500_volume", {})
        # Primary, conflict-proof match: the card's USB vendor:product id.
        self.vendor_id = str(settings.get("vendor_id", "17ef")).lower().removeprefix("0x")
        self.product_id = str(settings.get("product_id", "a017")).lower().removeprefix("0x")
        # Optional fallback: a substring of the PipeWire sink NAME. Disabled by
        # default ("") so a generic "USB Audio" dock/DAC can never be picked by
        # mistake -- set it only if the id match fails on your setup.
        self.sink_match = settings.get("sink_match", "")
        self.sink = None
        _, _, returncode = syscommand("pactl info", ignore_errors=True)
        if returncode != 0:
            raise SystemError("pactl is not available (no PipeWire/PulseAudio session)")

    def exposed_controls(self):
        """Exposes to home assistant."""
        return {
            "Volume": {
                "type": "number",
                "icon": "mdi:volume-high",
                "min": 0,
                "max": 100,
                "step": 1,
                "unit_of_measurement": "%",
            }
        }

    def get_info(self):
        """Gather information from the system."""
        if not self._resolve_sink():
            return None
        stdout, _, _ = syscommand(f"LC_ALL=C pactl get-sink-volume {self.sink}")
        pcts = re.findall(r"(\d+)%", stdout)  # "front-left: 39322 /  60% / -13.42 dB"
        if not pcts:
            return None
        return min(100, max(int(p) for p in pcts))

    def start_control(self, topic, data):
        """Control system."""
        if not self._resolve_sink():
            return
        pct = max(0, min(100, int(float(data))))
        syscommand(f"pactl set-sink-volume {self.sink} {pct}%")

    def _resolve_sink(self):
        """Locate the USB sink by vendor:product id, name substring as fallback (cached)."""
        if self.sink:
            return self.sink
        sinks = list(self._iter_sinks())
        # 1) USB id match -- unique to the Hub card, immune to other USB audio devices.
        for name, props in sinks:
            vid = props.get("device.vendor.id", "").lower().removeprefix("0x")
            pid = props.get("device.product.id", "").lower().removeprefix("0x")
            if vid == self.vendor_id and pid == self.product_id:
                self.sink = name
                logger.info("TSH500 Volume: sink resolved by id %s:%s -> %s",
                            self.vendor_id, self.product_id, name)
                return self.sink
        # 2) optional fallback: name substring (only if explicitly configured).
        if self.sink_match:
            needle = self.sink_match.lower()
            for name, _ in sinks:
                if needle in name.lower():
                    self.sink = name
                    logger.info("TSH500 Volume: sink resolved by name '%s' -> %s",
                                self.sink_match, name)
                    return self.sink
        logger.warning("TSH500 Volume: no sink for %s:%s (sink_match=%r)",
                       self.vendor_id, self.product_id, self.sink_match)
        return None

    @staticmethod
    def _iter_sinks():
        """Yield (node_name, properties) for each sink from `pactl list sinks`.

        Locale-independent: pactl translates the section labels ("Sink #", "Name:"),
        so we never key off them. Blocks are split on indentation (a sink header is
        the only non-indented line), the sink name is read from the `node.name`
        property, and LC_ALL=C is forced for good measure.
        """
        stdout, _, _ = syscommand("LC_ALL=C pactl list sinks", ignore_errors=True)
        blocks, cur = [], None
        for raw in stdout.splitlines():
            if raw and not raw[0].isspace():        # top-level header -> new sink block
                cur = {}
                blocks.append(cur)
            elif cur is not None and " = " in raw:  # "key = value" property line
                key, val = raw.split(" = ", 1)
                cur[key.strip()] = val.strip().strip('"')
        for props in blocks:
            name = props.get("node.name")
            if name:
                yield name, props
