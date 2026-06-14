"""Custom lnxlink module -- ThinkSmart Hub 500 PIR presence sensor.

The PIR sensor (17ef:60c0) is exposed via IIO (hid-sensor-prox). It reports MOTION
as short pulses (~0.5 s), which lnxlink's periodic get_info poll would miss. A
background thread therefore polls the IIO raw value at >=5 Hz and applies a
retention timer; get_info just returns the cached, debounced presence.

  - "Presence" : binary_sensor (occupancy)

sampling_frequency resets to 0 on reboot, so it is (re)armed when the sensor is
found. Discovery is lazy and retried in the background thread: at boot lnxlink can
start before hid-sensor-prox has enumerated the IIO device, so the module must not
fail hard then -- it keeps looking, arms the sensor once it appears, and re-discovers
it if it later goes away. Requires write access to the IIO sysfs attributes (see the
bundled udev rule).
"""
import glob
import logging
import threading
import time

logger = logging.getLogger("lnxlink")

IIO_GLOB = "/sys/bus/iio/devices/iio:device*"


class Addon:
    """Presence addon."""

    def __init__(self, lnxlink):
        """Setup addon."""
        self.name = "TSH500 Presence"
        self.lnxlink = lnxlink
        settings = lnxlink.config.get("settings", {}).get("tsh500_presence", {})
        self.sampling_hz = int(settings.get("sampling_hz", 5))
        self.poll_interval = float(settings.get("poll_interval", 0.2))
        self.retention = float(settings.get("retention", 180))

        # Discover lazily: at boot lnxlink may start before hid-sensor-prox has
        # enumerated the device. Don't fail hard (lnxlink would disable the module
        # for the whole session) -- the watch thread keeps looking and arms it.
        self.attr_raw = None
        self.attr_freq = None
        self.dev = self._find_sensor()
        if self.dev:
            self._arm()
        else:
            logger.warning("PIR: 'prox' sensor not present yet -- will keep looking "
                           "(hid-sensor-prox may not have enumerated at boot)")

        self._present = False
        self._stop = threading.Event()
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def exposed_controls(self):
        """Exposes to home assistant."""
        return {
            "Presence": {
                "type": "binary_sensor",
                "icon": "mdi:motion-sensor",
                "device_class": "occupancy",
            }
        }

    def get_info(self):
        """Gather information from the system."""
        return "ON" if self._present else "OFF"

    # --- internals ---
    def _find_sensor(self):
        for d in glob.glob(IIO_GLOB):
            try:
                if open(f"{d}/name").read().strip() == "prox":
                    self._resolve_attrs(d)
                    return d
            except OSError:
                continue
        return None

    def _resolve_attrs(self, dev):
        """Resolve the sysfs attribute paths. Kernels differ in how they name the
        IIO channel: older ones index it (in_proximity0_raw), newer ones don't
        (in_proximity_raw). Match both with a glob so the module is kernel-agnostic.
        """
        raw = sorted(glob.glob(f"{dev}/in_proximity*_raw"))
        freq = sorted(glob.glob(f"{dev}/in_proximity*_sampling_frequency"))
        self.attr_raw = raw[0] if raw else None
        self.attr_freq = freq[0] if freq else None

    def _arm(self):
        """Enable sampling (raw stays frozen otherwise). Best-effort."""
        if not self.attr_freq:
            logger.warning("PIR: no sampling_frequency attribute found -- cannot arm")
            return
        try:
            with open(self.attr_freq, "w") as f:
                f.write(str(self.sampling_hz))
        except OSError as err:
            logger.warning("PIR: cannot arm sensor (%s) -- sysfs permissions?", err)

    def _read_raw(self):
        if not self.attr_raw:
            return None
        try:
            with open(self.attr_raw) as f:
                return int(f.read().strip())
        except OSError:
            return None

    def _watch_loop(self):
        """Poll at >=5 Hz, apply retention -> stable cached presence.

        Also (re)discovers the sensor: if it was not ready at boot, or it goes away
        (re-enumeration), keep looking and re-arm it once it is back.
        """
        last_motion = 0.0
        while not self._stop.is_set():
            if not self.dev:
                self.dev = self._find_sensor()
                if self.dev:
                    logger.info("PIR: 'prox' sensor found -> %s", self.dev)
                    self._arm()
                else:
                    self._present = False
                    time.sleep(2.0)  # not ready yet, back off before retrying
                    continue
            raw = self._read_raw()
            if raw is None:           # sensor disappeared -> re-discover next loop
                self.dev = None
                time.sleep(self.poll_interval)  # avoid a tight re-discovery loop
                continue
            now = time.monotonic()
            if raw == 1:
                last_motion = now
            self._present = (raw == 1) or (now - last_motion < self.retention)
            time.sleep(self.poll_interval)
