# Home Assistant integration — lnxlink custom modules

Custom [lnxlink](https://github.com/bkbilly/lnxlink) modules that expose the Lenovo
ThinkSmart Hub 500 peripherals to Home Assistant over MQTT. lnxlink provides the
MQTT discovery, reconnection, configuration and a large set of system modules; these
four modules add the Hub-specific hardware on top.

## Exposed entities

| lnxlink control | Type | Backing logic | Local-change feedback |
|-----------------|------|---------------|-----------------------|
| **LED Power** | `switch` | external LED controller (vendor ep0) | reports last commanded state |
| **LED Color** | `select` (white/green/red) | same | reports last commanded state |
| **Screen Power** | `switch` | DDC/CI `D6` (off = write-only 5) | DDC poll |
| **Screen Brightness** | `number` 0-100 | DDC/CI VCP `0x10` | DDC poll |
| **Volume** | `number` 0-100 % | PipeWire softvol on USB sink `17ef:a017` | pactl poll |
| **Presence** | `binary_sensor` (occupancy) | PIR via IIO + 5 Hz background thread | continuous |

> The LED has **no** brightness channel (hardware), hence only three colors + off.
> The LED module reports the **last commanded** state rather than reading the
> controller back: while the firmware drives the ring (e.g. the white idle animation
> at boot) the latched register is ambiguous and would not map to the visible color.
> Volume is purely **software** (the DSP ignores UAC). The PIR reports *motion* as
> short pulses; a background thread polls it at 5 Hz and applies a retention timer so
> lnxlink's slower `get_info` poll still reports stable *presence*.

## Why lnxlink modules

`get_info()` is polled by lnxlink, which naturally covers state feedback for changes
made locally on the machine. lnxlink already runs as a `systemctl --user` service, so
it inherits the PipeWire session the volume module needs. Each module fails fast if
its device/dependency is missing, so lnxlink just disables that one module.

## Files

```
modules/tsh500_led.py        LED ring   (switch + select)
modules/tsh500_screen.py     LCD screen (switch + number)
modules/tsh500_volume.py     volume     (number)
modules/tsh500_presence.py   PIR        (binary_sensor)
99-thinksmart-ha.rules       udev: USB (LED) + IIO (PIR) access without root
lnxlink.config.snippet.yaml  config keys to merge (custom_modules + settings)
install.sh                   installs lnxlink + deps, modules, udev, groups
```

## Install (on the Hub 500)

```bash
cd ha_integration   # the directory containing this README
./install.sh
```

Then:

1. Set up lnxlink and its MQTT broker (see the
   [lnxlink setup docs](https://bkbilly.gitbook.io/lnxlink/setup)).
2. Merge `lnxlink.config.snippet.yaml` into `~/.config/lnxlink/config.yaml`
   (the `custom_modules` paths and the `settings` block).
3. Log out/in once (for the `i2c`/`plugdev` groups), then restart lnxlink:
   `systemctl --user restart lnxlink`.

## Requirements

- Dependencies: `ddcutil`, `i2c-tools`, `pulseaudio-utils` (pactl), `pyusb`
  (installed by `install.sh`).
- Device access without root: the udev rule grants the session user access to the
  USB LED controller (`17ef:a017`) and the PIR IIO attributes; the user must be in
  the `i2c` group for DDC.

## Per-module settings

Optional, under `settings:` in the lnxlink config (defaults shown in the snippet):
`tsh500_screen.i2c_bus`, `tsh500_volume.{vendor_id,product_id,sink_match}`,
`tsh500_presence.{sampling_hz,poll_interval,retention}`.

## Deployment notes / gotchas

- **Service creation is interactive.** Running lnxlink's official `curl | bash`
  installer cannot answer the "Install as a user service?" prompt (stdin is the
  pipe), so the `lnxlink.service` user unit is never written. Either run
  `lnxlink -c <config>` once from an interactive shell, or drop the unit manually in
  `~/.config/systemd/user/lnxlink.service` (template `SERVICEUSER` in lnxlink's
  `consts.py`), then `systemctl --user enable --now lnxlink`.
- **pipx isolation:** pyusb must be injected into lnxlink's venv
  (`pipx inject lnxlink pyusb`), `pip install --user` is not visible to it.
- **`pactl` must be installed** (`pulseaudio-utils`) for the volume module.
- **Volume needs an active audio session.** PipeWire only sees the USB card when the
  user owns the seat (graphical session) or is in the `audio` group; otherwise the
  only sink is `auto_null` and no Hub sink exists. Add the user to `audio` for
  headless operation.
- **Sink resolution is by USB id, not by name.** The module finds the sink whose
  `device.vendor.id`/`device.product.id` properties match `17ef:a017`, so a generic
  "USB Audio" dock/DAC/headset can never be picked by mistake. Inspect properties
  with `pactl list sinks`. The optional `sink_match` (sink *name* substring) is a
  fallback only and is empty by default.
- Group changes (`i2c`/`plugdev`/`audio`) only apply to processes started after a new
  login of the user systemd manager — reboot (or `loginctl terminate-user`) to make
  them effective for the running lnxlink service. udev `uaccess`/ACL covers USB and
  IIO for the active seat immediately.

## Status

Tested on a Hub 500: LED (switch+select), screen (switch+number) and presence
(binary_sensor) verified live, publishing real state over MQTT. The volume module
loads and is wired correctly; it resolves its sink once an audio session is active.

## License

[MIT](LICENSE).

This project is not affiliated with or endorsed by Lenovo or the lnxlink project;
"ThinkSmart" is a trademark of Lenovo. The hardware control here is the result of
independent reverse engineering and is provided as-is (see the warranty disclaimer
in the license).
