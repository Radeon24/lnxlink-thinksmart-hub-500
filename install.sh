#!/usr/bin/env bash
# Installer for the ThinkSmart Hub 500 lnxlink custom modules.
# Installs lnxlink + dependencies, drops the custom modules, and grants the
# device access (udev + groups). lnxlink runs as a systemctl --user service, so
# it inherits the PipeWire session needed for the volume module.
#
# Run on the target machine (the Hub 500), with sudo available.
set -euo pipefail

USER_NAME="${SUDO_USER:-$USER}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LNX_DIR="/home/${USER_NAME}/.config/lnxlink"
MOD_DIR="${LNX_DIR}/modules"

echo "==> System dependencies (ddcutil, i2c-tools, pactl, python3/pip)"
if command -v apt-get >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y ddcutil i2c-tools python3-pip pulseaudio-utils
fi
sudo modprobe i2c-dev 2>/dev/null || true

echo "==> lnxlink (pipx) + pyusb injected for the LED module"
# lnxlink's official installer uses pipx; match that and inject pyusb into its venv.
python3 -m pip install --user --upgrade pipx >/dev/null 2>&1 || true
pipx install lnxlink || pipx upgrade lnxlink || true
pipx inject lnxlink pyusb || true

echo "==> Copying custom modules to ${MOD_DIR}"
mkdir -p "${MOD_DIR}"
cp "${SRC_DIR}/modules/"tsh500_*.py "${MOD_DIR}/"

echo "==> udev rules (USB LED + IIO PIR access without root)"
sudo cp "${SRC_DIR}/99-thinksmart-ha.rules" /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger

echo "==> Groups (i2c for DDC, plugdev for USB/IIO, audio for the volume sink)"
# 'audio' lets PipeWire/WirePlumber open the USB card even on a headless seat.
sudo usermod -aG i2c,plugdev,audio "${USER_NAME}"

cat <<EOF

================================================================
Custom modules installed in ${MOD_DIR}.

Remaining steps (lnxlink side):

  1. If you don't have a config yet, generate one by running lnxlink once
     (see https://bkbilly.gitbook.io/lnxlink/setup for broker setup).

  2. Merge the keys from lnxlink.config.snippet.yaml into
     ${LNX_DIR}/config.yaml  (custom_modules paths + settings).

  3. Log out/in once so the i2c/plugdev groups apply (or: newgrp i2c).

  4. Restart the lnxlink user service, e.g.:
       systemctl --user restart lnxlink
       journalctl --user -u lnxlink -f

The entities (LED Power/Color, Screen Power/Brightness, Volume, Presence)
appear in Home Assistant under this machine's lnxlink device.
================================================================
EOF
