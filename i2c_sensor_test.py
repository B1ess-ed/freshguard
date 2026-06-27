#!/usr/bin/env python3
"""
I2C sensor smoke test for a sensor connected to I2C4_SDA_3V3/I2C4_SCL_3V3.

Run on the development board:
    python3 i2c_sensor_test.py

Common examples:
    python3 i2c_sensor_test.py --bus 4
    python3 i2c_sensor_test.py --bus auto
    python3 i2c_sensor_test.py --addr 0x68
    python3 i2c_sensor_test.py --addr 0x68 --reg 0x75 --length 1
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
from typing import Iterable


COMMON_ID_REGISTERS = (0x00, 0x0F, 0x75, 0xD0)


def parse_bus(value: str):
    if value.lower() == "auto":
        return "auto"
    bus = parse_int(value)
    if bus < 0:
        raise argparse.ArgumentTypeError("bus number must be >= 0")
    return bus


def parse_int(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid number: {value}") from exc


def hex_bytes(data: Iterable[int]) -> str:
    return " ".join(f"0x{byte:02X}" for byte in data)


def load_smbus():
    try:
        from smbus2 import SMBus

        return SMBus
    except ImportError:
        return None


class RawI2CBus:
    """Small fallback for simple reads when smbus2 is not installed."""

    I2C_SLAVE = 0x0703

    def __init__(self, bus: int):
        import fcntl

        self._fcntl = fcntl
        self._fd = os.open(f"/dev/i2c-{bus}", os.O_RDWR)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        os.close(self._fd)

    def _select(self, addr: int) -> None:
        self._fcntl.ioctl(self._fd, self.I2C_SLAVE, addr)

    def read_byte(self, addr: int) -> int:
        self._select(addr)
        return os.read(self._fd, 1)[0]

    def read_i2c_block_data(self, addr: int, reg: int, length: int) -> list[int]:
        self._select(addr)
        os.write(self._fd, bytes([reg]))
        return list(os.read(self._fd, length))


def open_bus(bus_no: int):
    SMBus = load_smbus()
    if SMBus is not None:
        return SMBus(bus_no), True
    return RawI2CBus(bus_no), False


def scan_bus(bus, use_smbus2: bool) -> list[int]:
    found: list[int] = []
    for addr in range(0x03, 0x78):
        try:
            if use_smbus2:
                try:
                    bus.write_quick(addr)
                except OSError:
                    bus.read_byte(addr)
            else:
                bus.read_byte(addr)
            found.append(addr)
        except OSError:
            pass
    return found


def read_register(bus, addr: int, reg: int, length: int) -> list[int]:
    return list(bus.read_i2c_block_data(addr, reg, length))


def detected_bus_numbers() -> list[int]:
    bus_numbers: list[int] = []
    for path in glob.glob("/dev/i2c-*"):
        try:
            bus_numbers.append(int(path.rsplit("-", 1)[1]))
        except (IndexError, ValueError):
            pass
    return sorted(bus_numbers)


def print_environment_hint(bus_no) -> None:
    devices = [f"/dev/i2c-{bus}" for bus in detected_bus_numbers()]
    if devices:
        print("Detected I2C device files: " + ", ".join(devices))
    else:
        print("No /dev/i2c-* device files were found.")
    print("Using bus: auto" if bus_no == "auto" else f"Using bus: /dev/i2c-{bus_no}")


def scan_one_bus(bus_no: int) -> tuple[list[int], bool]:
    try:
        bus_cm, use_smbus2 = open_bus(bus_no)
    except PermissionError:
        print()
        print(f"ERROR: permission denied opening /dev/i2c-{bus_no}.")
        print(f"Try: sudo python3 {os.path.basename(__file__)} --bus {bus_no}")
        return [], False
    except OSError as exc:
        print()
        print(f"ERROR: failed to open /dev/i2c-{bus_no}: {exc}")
        return [], False

    if not use_smbus2:
        print("Note: smbus2 is not installed, using a simple raw-I2C fallback.")
        print("For more reliable scanning, install it with: python3 -m pip install smbus2")

    with bus_cm as bus:
        found = scan_bus(bus, use_smbus2)
    return found, True


def choose_bus(args_bus) -> tuple[int | None, list[int]]:
    available = detected_bus_numbers()
    if args_bus != "auto":
        if os.path.exists(f"/dev/i2c-{args_bus}"):
            return args_bus, []

        print()
        print(f"WARNING: /dev/i2c-{args_bus} does not exist.")
        if not available:
            return None, []
        print("Scanning all detected I2C buses instead...")

    if not available:
        return None, []

    buses_with_devices: list[tuple[int, list[int]]] = []
    for bus_no in available:
        print()
        print(f"Scanning /dev/i2c-{bus_no}...")
        found, opened = scan_one_bus(bus_no)
        if not opened:
            continue
        if found:
            print("Found device address(es): " + ", ".join(f"0x{addr:02X}" for addr in found))
            buses_with_devices.append((bus_no, found))
        else:
            print("No devices responded on this bus.")

    if not buses_with_devices:
        return None, []

    bus_no, found = buses_with_devices[0]
    print()
    print(f"Selected /dev/i2c-{bus_no} for register reads.")
    return bus_no, found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan and read an I2C sensor connected to I2C4_SDA_3V3/I2C4_SCL_3V3."
    )
    parser.add_argument(
        "--bus",
        type=parse_bus,
        default="auto",
        help="I2C bus number or 'auto', default: auto",
    )
    parser.add_argument("--addr", type=parse_int, help="sensor I2C address, for example 0x68")
    parser.add_argument("--reg", type=parse_int, help="register address to read, for example 0x75")
    parser.add_argument("--length", type=int, default=1, help="number of bytes to read, default: 1")
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="only scan the bus and do not try register reads",
    )
    args = parser.parse_args()

    if args.length < 1:
        print("ERROR: --length must be >= 1", file=sys.stderr)
        return 2
    if args.addr is not None and not 0x03 <= args.addr <= 0x77:
        print("ERROR: --addr must be in the 7-bit I2C range 0x03..0x77", file=sys.stderr)
        return 2
    if args.reg is not None and not 0x00 <= args.reg <= 0xFF:
        print("ERROR: --reg must be in range 0x00..0xFF", file=sys.stderr)
        return 2

    print_environment_hint(args.bus)
    selected_bus, pre_scanned_found = choose_bus(args.bus)
    if selected_bus is None:
        print()
        print("ERROR: no usable I2C bus with responding devices was found.")
        print("Check VCC3V3, GND, SDA/SCL order, pull-up resistors, sensor power, and board pinmux.")
        return 1

    try:
        bus_cm, use_smbus2 = open_bus(selected_bus)
    except PermissionError:
        print()
        print(f"ERROR: permission denied opening /dev/i2c-{selected_bus}.")
        print(f"Try: sudo python3 {os.path.basename(__file__)} --bus {selected_bus}")
        return 1
    except OSError as exc:
        print()
        print(f"ERROR: failed to open /dev/i2c-{selected_bus}: {exc}")
        return 1

    if not use_smbus2:
        print("Note: smbus2 is not installed, using a simple raw-I2C fallback.")
        print("For more reliable scanning, install it with: python3 -m pip install smbus2")

    try:
        with bus_cm as bus:
            print()
            if pre_scanned_found:
                found = pre_scanned_found
            else:
                print(f"Scanning 7-bit I2C addresses on /dev/i2c-{selected_bus}...")
                found = scan_bus(bus, use_smbus2)
            if found:
                print("Found device address(es): " + ", ".join(f"0x{addr:02X}" for addr in found))
            else:
                print("No I2C devices responded.")
                print("Check VCC3V3, GND, SDA/SCL order, pull-up resistors, and sensor power.")
                return 1

            if args.scan_only:
                return 0

            addr = args.addr if args.addr is not None else found[0]
            if addr not in found:
                print()
                print(f"WARNING: requested address 0x{addr:02X} was not seen during scan.")

            print()
            if args.reg is not None:
                data = read_register(bus, addr, args.reg, args.length)
                print(f"Read addr=0x{addr:02X}, reg=0x{args.reg:02X}, length={args.length}: {hex_bytes(data)}")
                return 0

            print(f"No register was specified, trying common ID registers at addr=0x{addr:02X}...")
            any_read = False
            for reg in COMMON_ID_REGISTERS:
                try:
                    data = read_register(bus, addr, reg, 1)
                    print(f"reg 0x{reg:02X}: {hex_bytes(data)}")
                    any_read = True
                except OSError as exc:
                    print(f"reg 0x{reg:02X}: read failed ({exc})")

            if any_read:
                print()
                print("I2C communication looks OK. Use --addr/--reg/--length for sensor-specific reads.")
                return 0

            print()
            print("The device was detected, but register reads failed. This can be normal for some sensors.")
            print("Run again with the correct register from the sensor datasheet.")
            return 1
    except PermissionError:
        print()
        print(f"ERROR: permission denied accessing /dev/i2c-{selected_bus}.")
        print(f"Try: sudo python3 {os.path.basename(__file__)} --bus {selected_bus}")
        return 1
    except OSError as exc:
        print()
        print(f"ERROR: I2C operation failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
