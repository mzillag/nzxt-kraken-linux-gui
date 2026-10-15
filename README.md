# Kraken Control

Linux control application for the **NZXT Kraken Elite (2023, 640×640 LCD, USB `1e71:300c`)**
and its siblings (Kraken 2023 `300e`, Kraken 2024 Elite RGB `3012`, Kraken 2024 Plus `3014`).
It reproduces the parts of NZXT CAM that matter for the cooler:

* **Cooling** – Fixed / Silent / Performance / Custom presets for the pump and the fan with
  the exact preset tables of CAM 4.76 (Silent fan 30 % to 35 °C then 35/42/50/57/63/69/74/78/84/90/100,
  Performance fan 50 % to 35 °C then 60/70/80/90/100, Silent pump 60 % to 35 °C then 70/80/90/100,
  Performance pump 70 % to 35 °C then 80/85/90/95/100; Custom starts from Performance; Fixed
  defaults 40 % fan / 100 % pump), a draggable 17-node curve editor (20–100 °C), and a
  temperature source per channel (Liquid, CPU, GPU). Liquid-based curves are stored in the
  cooler firmware, so they keep working when the app is not running; CPU/GPU-based curves are
  driven by the daemon with CAM's 5 s ramp-down hysteresis.
* **LCD** – Image/GIF, Single/Dual/Triple Infographic (also "+ GIF"), Clockface, Carousel and the
  firmware's own liquid-temperature screen, with brightness and the 30°-step "Rotate Display"
  dial. Frames are rendered on the PC and streamed through the cooler's fast image path, the
  same mechanism CAM uses, so GIFs play smoothly and readings update live.
* **PC Monitoring / System Specs / Settings** pages in the CAM layout and palette (dark and
  light theme, °C/°F, launch at login).

Not affiliated with NZXT. "NZXT", "Kraken" and "CAM" are trademarks of NZXT Inc.

## Requirements

* Linux with the `nzxt_kraken3` kernel module (mainline since 6.x; it provides the hwmon
  sensors and is left untouched by this app).
* Python 3.10+, `python3-gi` with GTK 3 and WebKit2GTK 4.1 for the desktop window
  (Ubuntu: `python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1`). Without them the UI opens in Chrome
  or your default browser.
* Rust toolchain (optional) to build the native q565 encoder; a pure Python fallback exists.

## Install

```sh
cd ~/Projects/kraken-control
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .          # makes `python -m krakencontrol` work from any directory
(cd native/q565 && cargo build --release)      # optional, faster LCD frames
sudo sh packaging/install-udev.sh               # one time: USB access without root
```

Run it:

```sh
.venv/bin/python -m krakencontrol               # service + window
.venv/bin/python -m krakencontrol daemon        # service only (what the systemd unit runs)
.venv/bin/python -m krakencontrol tray          # tray icon + window attached to the service
.venv/bin/python -m krakencontrol status        # quick check of sensors and the device
.venv/bin/python -m krakencontrol --mock        # UI without touching the hardware
```

"Launch at login" in Settings installs a systemd user unit (`~/.config/systemd/user/kraken-control.service`)
that runs the service headless from login. For the tray icon and window at login, like CAM on
Windows, copy `packaging/org.krakencontrol.App-autostart.desktop` to `~/.config/autostart/`: it
runs `krakencontrol tray`, which waits for the service, shows a panel icon (GNOME needs the
AppIndicator extension, Ubuntu ships it enabled) and opens the window; closing the window only
hides it and the tray menu or the app menu entry (`packaging/org.krakencontrol.App.desktop`,
copy to `~/.local/share/applications/`) brings it back. Add `--minimized` to start with the
window hidden. The window is a single-instance GTK application, so launching it again just
raises the existing one; a second `daemon` on a busy port exits at once.
A desktop entry is in `packaging/kraken-control.desktop` (copy to `~/.local/share/applications/`).

Settings live in `~/.config/kraken-control/config.json`; uploaded images in
`~/.config/kraken-control/assets/`.

## How it talks to the cooler

* **HID interface (interface 1, `/dev/hidrawN`)** – 64-byte reports. Status `0x74 0x01`,
  firmware `0x10 0x01`, LCD info `0x30 0x01`, brightness/orientation `0x30 0x02`, speed curves
  `0x72 <channel> + 40 duties for 20..59 °C liquid`, LCD mode `0x38 0x01 <mode> <bucket>`.
  Replies are matched by prefix after draining the unsolicited status broadcasts.
* **Bulk interface (interface 0, endpoint 0x02)** – image data. Each frame is announced with
  `0x36 0x01 0x00 0x01 <type>` and sent only after the firmware acknowledges (`0x37 0x01`), then a
  20-byte header (`12 FA 01 E8 AB CD EF 98 76 54 32 10`, type, little-endian length) and the
  payload. Type `0x08` is the q565 codec (QOI-style RGB565, the `q565` Rust crate), type `0x09`
  is raw BGR888, which is what CAM streams. Both go to the "fast memory", not to the fragile
  storage buckets that have wedged units into the bootloader (`1e71:3011`).
* GIFs can alternatively be uploaded into bucket 0 and played by the firmware
  ("Stream from app" unchecked); a keep-alive re-selects the bucket every 10 s because the
  firmware otherwise reverts to the liquid screen after ~30 s.

Protocol knowledge comes from [liquidctl](https://github.com/liquidctl/liquidctl), the
`nzxt-kraken3` kernel driver, [AIOLCDUnchained](https://github.com/brokenmass/AIOLCDUnchained),
[kraken-elite-screen-manager](https://github.com/KhalloufHassan/kraken-elite-screen-manager),
[nzxt-ctl](https://github.com/VLDG2712/nzxt-ctl) and
[kraken-unleashed](https://github.com/ssjrocks/kraken-unleashed). The CAM defaults (curves,
colours, layouts) were taken from NZXT's support articles and CAM screenshots.

## Layout

```
krakencontrol/
  protocol.py      USB protocol (hidraw + libusb bulk), curves, LCD commands
  sensors.py       CPU/GPU/RAM readers and the cooler's hwmon sensors
  cooling.py       preset/custom curves, firmware or software evaluation
  lcd/renderer.py  640x640 frame rendering (infographics, clock, images, GIFs)
  lcd/controller.py streaming thread, carousel, device-side GIF, brightness/rotation
  lcd/q565.py      q565 codec (ctypes wrapper around native/q565, numpy fallback)
  daemon.py        service wiring, state for the UI
  server.py        HTTP API + Server-Sent Events + static files
  gui.py           GTK/WebKit window
  web/             the CAM-style UI (vanilla JS)
native/q565/       Rust cdylib exposing the q565 encoder
packaging/         udev rule, systemd unit, desktop entry, install script
```

## License

MIT. Fonts: Inter (OFL) and Montserrat (OFL).
