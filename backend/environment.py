from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque


I2C_SLAVE = 0x0703


@dataclass(frozen=True)
class EnvironmentSample:
    timestamp: datetime
    sensor_model: str
    temperature_c: float
    humidity: float


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


def decode_sht30(raw: bytes) -> tuple[float, float]:
    if len(raw) != 6:
        raise ValueError(f"expected 6 bytes, got {len(raw)}")
    if crc8_sensirion(raw[0:2]) != raw[2]:
        raise ValueError("temperature CRC check failed")
    if crc8_sensirion(raw[3:5]) != raw[5]:
        raise ValueError("humidity CRC check failed")

    raw_temp = int.from_bytes(raw[0:2], "big")
    raw_humi = int.from_bytes(raw[3:5], "big")
    temperature_c = -45.0 + 175.0 * raw_temp / 65535.0
    humidity = 100.0 * raw_humi / 65535.0
    return temperature_c, humidity


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
    humidity = -6.0 + 125.0 * raw_humi / 65535.0
    humidity = max(0.0, min(100.0, humidity))
    return temperature_c, humidity


class SHT30Reader:
    def __init__(self, bus_no: int = 4, addr: int = 0x44, sensor_type: str = "auto"):
        self.bus_no = bus_no
        self.addr = addr
        self.sensor_type = sensor_type
        self.path = f"/dev/i2c-{bus_no}"

    def read(self) -> EnvironmentSample:
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"{self.path} does not exist")

        import fcntl

        fd = os.open(self.path, os.O_RDWR)
        try:
            fcntl.ioctl(fd, I2C_SLAVE, self.addr)
            sensor_model, temperature_c, humidity = self._read_selected(fd)
        finally:
            os.close(fd)

        return EnvironmentSample(
            timestamp=datetime.now(timezone.utc),
            sensor_model=sensor_model,
            temperature_c=temperature_c,
            humidity=humidity,
        )

    def _read_selected(self, fd: int) -> tuple[str, float, float]:
        if self.sensor_type == "sht4x":
            temperature_c, humidity = self._read_sht4x(fd)
            return "SHT4x", temperature_c, humidity
        if self.sensor_type == "sht30":
            temperature_c, humidity = self._read_sht30(fd)
            return "SHT30", temperature_c, humidity

        errors: list[str] = []
        for sensor_model, reader in (("SHT4x", self._read_sht4x), ("SHT30", self._read_sht30)):
            try:
                temperature_c, humidity = reader(fd)
                return sensor_model, temperature_c, humidity
            except OSError as exc:
                errors.append(f"{sensor_model}: I2C error: {exc}")
            except ValueError as exc:
                errors.append(f"{sensor_model}: {exc}")
            time.sleep(0.05)

        raise RuntimeError("; ".join(errors))

    def _read_sht30(self, fd: int) -> tuple[float, float]:
        os.write(fd, bytes([0x24, 0x00]))
        time.sleep(0.02)
        return decode_sht30(os.read(fd, 6))

    def _read_sht4x(self, fd: int) -> tuple[float, float]:
        os.write(fd, bytes([0xFD]))
        time.sleep(0.02)
        return decode_sht4x(os.read(fd, 6))


class EnvironmentMonitor:
    def __init__(
        self,
        reader: SHT30Reader,
        interval_seconds: float = 10.0,
        history_seconds: int = 2 * 60 * 60,
    ):
        self.reader = reader
        self.interval_seconds = max(1.0, interval_seconds)
        self.history_seconds = max(60, history_seconds)
        max_samples = max(8, int(self.history_seconds / self.interval_seconds) + 4)
        self._samples: Deque[EnvironmentSample] = deque(maxlen=max_samples)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    @classmethod
    def from_env(cls) -> "EnvironmentMonitor":
        bus_no = int(os.getenv("SHT30_I2C_BUS", "4"))
        addr = int(os.getenv("SHT30_I2C_ADDR", "0x44"), 0)
        sensor_type = os.getenv("SHT30_SENSOR_TYPE", "auto").strip().lower()
        if sensor_type in {"sht3x", "sht30"}:
            sensor_type = "sht30"
        elif sensor_type not in {"auto", "sht4x"}:
            sensor_type = "auto"
        interval = float(os.getenv("SHT30_READ_INTERVAL_SECONDS", "10"))
        history_seconds = int(os.getenv("SHT30_HISTORY_SECONDS", str(2 * 60 * 60)))
        return cls(SHT30Reader(bus_no=bus_no, addr=addr, sensor_type=sensor_type), interval, history_seconds)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="sht30-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def read_once(self) -> None:
        try:
            sample = self.reader.read()
        except Exception as exc:  # Hardware access must not take down the API.
            with self._lock:
                self._last_error = str(exc)
            return

        cutoff = sample.timestamp.timestamp() - self.history_seconds
        with self._lock:
            self._samples.append(sample)
            while self._samples and self._samples[0].timestamp.timestamp() < cutoff:
                self._samples.popleft()
            self._last_error = None

    def snapshot(self) -> dict:
        with self._lock:
            samples = list(self._samples)
            last_error = self._last_error

        latest = samples[-1] if samples else None
        return {
            "online": latest is not None and last_error is None,
            "sensor": {
                "model": latest.sensor_model if latest else "SHT30",
                "configured_type": self.reader.sensor_type,
                "bus": self.reader.bus_no,
                "address": f"0x{self.reader.addr:02X}",
                "interval_seconds": self.interval_seconds,
            },
            "latest": sample_to_dict(latest) if latest else None,
            "history": [sample_to_dict(sample) for sample in samples],
            "last_error": last_error,
        }

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.read_once()
            self._stop_event.wait(self.interval_seconds)


def sample_to_dict(sample: EnvironmentSample) -> dict:
    return {
        "timestamp": sample.timestamp.isoformat(),
        "sensor_model": sample.sensor_model,
        "temperature_c": round(sample.temperature_c, 2),
        "humidity": round(sample.humidity, 2),
    }
