"""Custom lnxlink module -- Lenovo ThinkSmart Hub 500 LED ring.

Drives the external LED controller through vendor ep0 transfers (a replica of the
firmware's serial-bus engine). Volatile, no flash, audio preserved (no USB interface
is detached). Exposes:

  - "LED Power" : switch  (on/off)
  - "LED Color" : select  (white / green / red -- no effects, no brightness)

State model: the module reports the state it last commanded. The controller's
fef7 register is NOT read back, because while the LED is driven by the firmware
(e.g. the white idle animation right after boot) the latched usage is ambiguous and
does not map cleanly to the visible color. The ring is assumed OFF at startup
(typically a boot service turns it off) and only reports a color once commanded
from Home Assistant.

The controller must receive one HID Feature Report ("priming") before it accepts
volatile vendor writes; this also lights the ring white, so it is done lazily on the
first command rather than at startup (which would re-light the ring after boot).

Requires usbfs access to 17ef:a017 (see the bundled udev rule) and pyusb.
"""
import logging
import threading

from lnxlink.modules.scripts.helpers import import_install_package

logger = logging.getLogger("lnxlink")

LED_COLORS = ["white", "green", "red"]


class Addon:
    """LED ring addon."""

    VID, PID = 0x17EF, 0xA017
    R_FE96, R_FEF7, R_FEF8, R_FEF9, R_FEFA, R_FEEE, R_FEEF = (
        0xFE96, 0xFEF7, 0xFEF8, 0xFEF9, 0xFEFA, 0xFEEE, 0xFEEF)
    MOTOR_BIT, DONE_BIT = 0x08, 0x40

    # (usage fef7, state fef8, mode feee) -- presets validated by webcam capture
    PRESETS = {
        "off":   (0x4F, 0x00, 0x02),
        "red":   (0x09, 0x00, 0x02),
        "green": (0x17, 0x01, 0x02),
        "white": (0x3D, 0x00, 0x02),
    }
    # HID priming (Feature Report ID 2) -- required at boot before a volatile OFF.
    HID_INTERFACE, HID_REPORT_ID = 5, 2
    _HIDIOCSFEATURE = (3 << 30) | (64 << 16) | (ord("H") << 8) | 0x06

    def __init__(self, lnxlink):
        """Setup addon."""
        self.name = "TSH500 LED"
        self.lnxlink = lnxlink
        self._lock = threading.Lock()
        self.last_color = "white"
        self.power = False     # assumed off at startup (led-off.service turns it off)
        self._primed = False   # HID prime is done lazily on the first command

        if import_install_package("pyusb", ">=1.2.1", "usb") is None:
            raise SystemError("pyusb is required for the LED module")
        import usb.core  # the helper returns the top-level package, so import the submodule
        self.dev = usb.core.find(idVendor=self.VID, idProduct=self.PID)
        if self.dev is None:
            raise SystemError("LED device 17ef:a017 not found")
        # Do NOT prime or drive here: that would re-light the ring after boot.

    def exposed_controls(self):
        """Exposes to home assistant."""
        return {
            "LED Power": {
                "type": "switch",
                "icon": "mdi:led-strip-variant",
                "value_template": "{{ value_json.power }}",
            },
            "LED Color": {
                "type": "select",
                "icon": "mdi:palette",
                "options": LED_COLORS,
                "value_template": "{{ value_json.color }}",
            },
        }

    def get_info(self):
        """Report the last commanded state (fef7 readback is ambiguous, see module doc)."""
        return {"power": "ON" if self.power else "OFF", "color": self.last_color}

    def start_control(self, topic, data):
        """Control system."""
        if topic[1] == "led_power":
            self._set(str(data).upper() == "ON", None)
        elif topic[1] == "led_color":
            self._set(True, str(data))  # selecting a color turns the ring on

    # --- low-level register access (serial-bus engine) ---
    def _rd(self, a):
        return self.dev.ctrl_transfer(0xC0, 0x25, 0, a, 1, timeout=2000)[0]

    def _wr(self, a, v):
        self.dev.ctrl_transfer(0x40, 0x24, 0, a, bytes([v & 0xFF]), timeout=2000)

    def _drive(self, usage, state, mode):
        self._wr(self.R_FE96, self._rd(self.R_FE96) | self.MOTOR_BIT)
        self._wr(self.R_FEF7, usage)
        self._wr(self.R_FEF8, state)
        self._wr(self.R_FEF9, 0x00)
        self._wr(self.R_FEFA, 0x00)
        self._wr(self.R_FEEE, mode)
        self._wr(self.R_FEEF, 0x80)
        for _ in range(200):
            if self._rd(self.R_FEEF) & self.DONE_BIT:
                break
        self._wr(self.R_FE96, self._rd(self.R_FE96) & ~self.MOTOR_BIT)

    def _set(self, on, color):
        with self._lock:
            self._prime_once()
            if not on:
                self._drive(*self.PRESETS["off"])
                self.power = False
                return
            color = color if color in LED_COLORS else self.last_color
            self.last_color = color
            self.power = True
            self._drive(*self.PRESETS[color])

    def _prime_once(self):
        """Send the HID priming report once per process, before the first vendor write."""
        if not self._primed:
            self._hid_prime()
            self._primed = True

    def _find_hidraw(self):
        import glob
        import os
        for entry in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            name = os.path.basename(entry)
            try:
                uevent = open(os.path.join(entry, "device", "uevent")).read().upper()
            except OSError:
                continue
            if f"{self.VID:04X}" not in uevent or f"{self.PID:04X}" not in uevent:
                continue
            real = os.path.realpath(os.path.join(entry, "device"))
            usb_if = os.path.basename(os.path.dirname(real))
            if "." in usb_if:
                try:
                    if int(usb_if.rsplit(".", 1)[-1]) != self.HID_INTERFACE:
                        continue
                except ValueError:
                    pass
            dev = f"/dev/{name}"
            if os.path.exists(dev):
                return dev
        return None

    def _hid_prime(self):
        import fcntl
        import os
        path = self._find_hidraw()
        if not path:
            return
        try:
            fd = os.open(path, os.O_RDWR)
            try:
                pkt = bytearray(64)
                pkt[0] = self.HID_REPORT_ID
                fcntl.ioctl(fd, self._HIDIOCSFEATURE, bytes(pkt))
            finally:
                os.close(fd)
        except OSError as err:
            logger.debug("LED: HID priming skipped (%s)", err)
