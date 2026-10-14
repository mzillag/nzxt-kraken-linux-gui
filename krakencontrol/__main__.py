"""Command line entry point.

  python -m krakencontrol            start the service and open the window
  python -m krakencontrol daemon     service only (for systemd)
  python -m krakencontrol gui        window only, connecting to a running service
  python -m krakencontrol tray       tray icon + window (closing the window hides it)
  python -m krakencontrol status     print sensor and device status
Options: --mock (fake device), --port N, --browser (use a browser instead of GTK),
         --minimized (tray: start without showing the window), -v
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
import urllib.request

from . import __version__
from .config import Config


def _port_in_use(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kraken-control", description="NZXT Kraken Elite control for Linux")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "daemon", "gui", "tray", "status"])
    parser.add_argument("--mock", action="store_true", help="use a fake device (no hardware access)")
    parser.add_argument("--port", type=int, default=None, help="API port (default from config, 30100)")
    parser.add_argument("--browser", action="store_true", help="open the UI in a browser instead of a GTK window")
    parser.add_argument("--minimized", action="store_true", help="tray: start with the window hidden")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"kraken-control {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mock = args.mock or os.environ.get("KRAKEN_MOCK") == "1"
    config = Config()
    port = args.port or int(config.section("general").get("api_port", 30100))
    url = f"http://127.0.0.1:{port}/"

    if args.command == "status":
        return _status(mock)

    if args.command in ("gui", "tray"):
        # Window only.  At login the window may start before the service is up, so
        # wait for it a little instead of showing a page that cannot load.
        for _ in range(60):
            if _port_in_use(port):
                break
            time.sleep(1)
        else:
            print(f"no service on port {port}; start it with 'python -m krakencontrol daemon'", file=sys.stderr)
    if args.command in ("gui", "tray") or (args.command == "run" and _port_in_use(port)):
        # A service is already running: just show the window (and the tray icon).
        # If a tray instance is already running, GTK activates it instead and we exit.
        from .gui import open_in_browser, run_window
        tray = args.command == "tray"
        if args.browser or not run_window(url, tray=tray, show_window=not args.minimized):
            open_in_browser(url)
        return 0

    if args.command == "daemon" and _port_in_use(port):
        # Another instance (for example one started by hand) already serves this port.
        # Exit cleanly so systemd does not restart us in a loop.
        print(f"another instance already serves port {port}; nothing to do", file=sys.stderr)
        return 0

    from .daemon import Daemon
    from .server import ApiServer

    daemon = Daemon(config, mock=mock)
    server = ApiServer(daemon, port=port)
    try:
        server.start()
    except OSError as exc:
        print(f"cannot listen on port {port}: {exc}", file=sys.stderr)
        return 1

    worker = threading.Thread(target=daemon.run, name="daemon", daemon=True)
    worker.start()

    def shutdown(*_args):
        daemon.stop()
        server.stop()

    signal.signal(signal.SIGTERM, lambda *_: shutdown())
    signal.signal(signal.SIGINT, lambda *_: shutdown())

    if args.command == "daemon":
        try:
            worker.join()
        except KeyboardInterrupt:
            shutdown()
            worker.join(timeout=5)
        return 0

    from .gui import open_in_browser, run_window
    server.on_quit = shutdown
    if args.browser or not run_window(url, on_close=shutdown):
        open_in_browser(url)
        try:
            worker.join()
        except KeyboardInterrupt:
            shutdown()
    shutdown()
    worker.join(timeout=5)
    return 0


def _status(mock: bool) -> int:
    from . import protocol
    from .sensors import SensorHub
    hub = SensorHub()
    snap = hub.poll()
    print(f"CPU: {snap.cpu.name}  temp={snap.cpu.temperature}°C load={snap.cpu.load and round(snap.cpu.load)}%")
    if snap.gpu.available:
        print(f"GPU: {snap.gpu.name}  temp={snap.gpu.temperature}°C load={snap.gpu.load}%")
    if snap.cooler.available:
        c = snap.cooler
        print(f"Kraken (hwmon): liquid={c.liquid_temperature}°C pump={c.pump_rpm} rpm ({c.pump_duty}%) "
              f"fan={c.fan_rpm} rpm ({c.fan_duty}%)")
    devices = protocol.discover()
    if not devices:
        print("No Kraken found on USB.")
        return 1
    for info in devices:
        print(f"USB: {info.name} pid={info.product_id:04x} serial={info.serial} hid={info.hidraw_path} "
              f"bus={info.usb_bus} addr={info.usb_address}")
        if mock:
            continue
        dev = protocol.KrakenDevice(info)
        try:
            dev.open()
            dev.initialize()
            print(f"  firmware {'.'.join(map(str, dev.firmware))}, LCD {dev.lcd.width}x{dev.lcd.height}, "
                  f"brightness {dev.lcd.brightness}%, orientation {dev.lcd.orientation * 90}°")
            st = dev.get_status()
            print(f"  status: liquid={st.liquid_temperature}°C pump={st.pump_rpm} rpm/{st.pump_duty}% "
                  f"fan={st.fan_rpm} rpm/{st.fan_duty}%")
        except protocol.DevicePermissionError as exc:
            print(f"  {exc}")
            print("  run: sudo sh packaging/install-udev.sh")
        except protocol.ProtocolError as exc:
            print(f"  error: {exc}")
        finally:
            dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
