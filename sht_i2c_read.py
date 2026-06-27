#!/usr/bin/env python3
"""
Read temperature and humidity from an SHT3x/SHT4x sensor on I2C.

Default wiring for the current board setup:
    bus:  /dev/i2c-4
    addr: 0x44

Run on the development board:
    sudo python3 sht_i2c_read.py
    sudo python3 sht_i2c_read.py --bus 4 --addr 0x44 --type sht4x
    sudo python3 sht_i2c_read.py --loop 1
"""

from __future__ import annotations

import argparse
import os
import sys
import time


I2C_SLAVE = 0x0703


def parse_int(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid number: {value}") from exc


def crc8_sensirion(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


class I2CBus:
    def __init__(self, bus_no: int, addr: int):
        self.path = f"/dev/i2c-{bus_no}"
        self.addr = addr
        self.fd: int | None = None

    def __enter__(self):
        import fcntl

        self.fd = os.open(self.path, os.O_RDWR)
        fcntl.ioctl(self.fd, I2C_SLAVE, self.addr)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.fd is not None:
            os.close(self.fd)

    def write(self, data: bytes) -> None:
        assert self.fd is not None
        os.write(self.fd, data)

    def read(self, length: int) -> bytes:
        assert self.fd is not None
        return os.read(self.fd, length)


def decode_sht3x(raw: bytes) -> tuple[float, float]:
    if len(raw) != 6:
        raise ValueError(f"expected 6 bytes, got {len(raw)}")
    if crc8_sensirion(raw[0:2]) != raw[2]:
        raise ValueError("temperature CRC check failed")
    if crc8_sensirion(raw[3:5]) != raw[5]:
        raise ValueError("humidity CRC check failed")

    raw_temp = int.from_bytes(raw[0:2], "big")
    raw_humi = int.from_bytes(raw[3:5], "big")
    temperature_c = -45.0 + 175.0 * raw_temp / 65535.0
    humidity_rh = 100.0 * raw_humi / 65535.0
    return temperature_c, humidity_rh


def decode_sht4x(raw: bytes) -> tuple[float, float]:
    if len(raw) != 6:
        raise ValueError(f"expected 6 bytes, got {len(raw)}")
    if crc8_sensirion(raw[0:2]) != raw[2]:
        raise ValueError("temperature CRC check failed")
    if crc8_sensirion(raw[3:5]) != raw[5]:
        raise ValueError("humidity CRC check failed")

    raw_temp = int.from_bytes(raw[0:2], "big")
    raw_humi = int.from_bytes(raw[3:5], "big")
    temperature_c = -45.0 + 175.0 * raw_temp / 65535.0
    humidity_rh = -6.0 + 125.0 * raw_humi / 65535.0
    humidity_rh = max(0.0, min(100.0, humidity_rh))
    return temperature_c, humidity_rh


def read_sht4x(bus: I2CBus) -> tuple[float, float, bytes]:
    bus.write(bytes([0xFD]))
    time.sleep(0.02)
    raw = bus.read(6)
    temp_c, humi = decode_sht4x(raw)
    return temp_c, humi, raw


def read_sht3x(bus: I2CBus) -> tuple[float, float, bytes]:
    bus.write(bytes([0x24, 0x00]))
    time.sleep(0.02)
    raw = bus.read(6)
    temp_c, humi = decode_sht3x(raw)
    return temp_c, humi, raw


def read_sensor(bus: I2CBus, sensor_type: str) -> tuple[str, float, float, bytes]:
    if sensor_type == "sht4x":
        temp_c, humi, raw = read_sht4x(bus)
        return "SHT4x", temp_c, humi, raw
    if sensor_type == "sht3x":
        temp_c, humi, raw = read_sht3x(bus)
        return "SHT3x", temp_c, humi, raw

    errors: list[str] = []
    for name, reader in (("SHT4x", read_sht4x), ("SHT3x", read_sht3x)):
        try:
            temp_c, humi, raw = reader(bus)
            return name, temp_c, humi, raw
        except OSError as exc:
            errors.append(f"{name}: I2C error: {exc}")
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
        time.sleep(0.05)

    raise RuntimeError("; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read an SHT3x/SHT4x temperature and humidity sensor.")
    parser.add_argument("--bus", type=int, default=4, help="I2C bus number, default: 4")
    parser.add_argument("--addr", type=parse_int, default=0x44, help="I2C address, default: 0x44")
    parser.add_argument(
        "--type",
        choices=("auto", "sht3x", "sht4x"),
        default="auto",
        help="sensor type, default: auto",
    )
    parser.add_argument(
        "--loop",
        type=float,
        default=0,
        help="read repeatedly every N seconds; 0 means read once",
    )
    args = parser.parse_args()

    if not os.path.exists(f"/dev/i2c-{args.bus}"):
        print(f"ERROR: /dev/i2c-{args.bus} does not exist", file=sys.stderr)
        return 1

    try:
        with I2CBus(args.bus, args.addr) as bus:
            while True:
                sensor_name, temp_c, humi, raw = read_sensor(bus, args.type)
                temp_f = temp_c * 9.0 / 5.0 + 32.0
                raw_hex = " ".join(f"{byte:02X}" for byte in raw)
                print(
                    f"{sensor_name} addr=0x{args.addr:02X} bus={args.bus} "
                    f"temperature={temp_c:.2f} C / {temp_f:.2f} F "
                    f"humidity={humi:.2f} %RH raw=[{raw_hex}]"
                )
                if args.loop <= 0:
                    break
                time.sleep(args.loop)
    except PermissionError:
        print(f"ERROR: permission denied. Try: sudo python3 {os.path.basename(__file__)}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"ERROR: I2C communication failed: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: could not read sensor as SHT3x or SHT4x: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 130

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
