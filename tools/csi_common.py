#!/usr/bin/env python3
"""Shared CSI line parsing, serial reading, replay, and demo-signal helpers.

Used by plot_csi_serial.py, plot_csi_smoothed_TODO.py, and
csi_presence_detect.py so the tagged, variable-length CSI serial format
is only parsed in one place.
"""

from __future__ import annotations

import dataclasses
import queue
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np


CSI_LINE_RE = re.compile(
    r"<timestamp>(\d+)</timestamp><rssi>(-?\d+)</rssi><address>([0-9A-Fa-f:]+)</address>(.+)"
)

EXPECTED_SUBCARRIERS = 64

_warned_length_mismatch = False


@dataclasses.dataclass
class CsiSample:
    timestamp_cycles: int
    rssi: int
    address: str
    iq_values: list[int]


def parse_csi_line(line: str) -> Optional[CsiSample]:
    """Parse one sniffer.c serial line into a CsiSample, or None if it doesn't match."""
    match = CSI_LINE_RE.search(line)
    if not match:
        return None

    timestamp_str, rssi_str, address, values_str = match.groups()
    tokens = values_str.split()
    if not tokens:
        return None

    try:
        iq_values = [int(token) for token in tokens]
    except ValueError:
        return None

    return CsiSample(
        timestamp_cycles=int(timestamp_str),
        rssi=int(rssi_str),
        address=address,
        iq_values=iq_values,
    )


def iq_to_amplitude(iq_values: list[int]) -> np.ndarray:
    """Convert a flat (imag, real) x N int list into an N-length amplitude array."""
    global _warned_length_mismatch

    values = np.asarray(iq_values, dtype=np.float32)
    if values.size % 2 != 0:
        values = values[:-1]

    num_subcarriers = values.size // 2
    if num_subcarriers != EXPECTED_SUBCARRIERS and not _warned_length_mismatch:
        print(
            f"Warning: expected {EXPECTED_SUBCARRIERS} subcarriers "
            f"({EXPECTED_SUBCARRIERS * 2} values) but got {num_subcarriers} "
            f"({values.size} values). Continuing with the actual size."
        )
        _warned_length_mismatch = True

    imag = values[0::2]
    real = values[1::2]
    return np.sqrt(imag ** 2 + real ** 2)


def format_log_line(elapsed_seconds: float, raw_line: str) -> str:
    """Format a raw serial line for the replay log, prefixed with its arrival time."""
    return f"t={elapsed_seconds:.6f} {raw_line}"


LOG_LINE_RE = re.compile(r"t=([0-9.]+) (.*)")


def parse_log_line(line: str) -> Optional[tuple[float, str]]:
    """Parse a replay log line back into (elapsed_seconds, raw_line)."""
    match = LOG_LINE_RE.match(line)
    if not match:
        return None
    return float(match.group(1)), match.group(2)


class SerialCsiReader:
    """Reads CSI samples from a live serial port on a background thread.

    Exposes the same (queue, start, stop) interface as ReplayCsiReader and
    DemoCsiReader, so tools can swap sources without branching their update
    loop.
    """

    def __init__(
        self,
        port: str,
        baud: int,
        log_path: Optional[str] = None,
        on_unparsed: Optional[Callable[[str], None]] = None,
    ):
        self.port = port
        self.baud = baud
        self.log_path = Path(log_path) if log_path else None
        self.on_unparsed = on_unparsed
        self.queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def _run(self) -> None:
        import serial  # imported here so importing csi_common never requires a serial port

        start_time = time.monotonic()
        log_file = self.log_path.open("a", encoding="utf-8") if self.log_path else None
        try:
            with serial.Serial(self.port, baudrate=self.baud, timeout=0.1) as ser:
                ser.reset_input_buffer()
                while not self._stop_event.is_set():
                    raw = ser.readline()
                    if not raw:
                        continue

                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue

                    sample = parse_csi_line(line)
                    if sample is None:
                        if self.on_unparsed is not None:
                            self.on_unparsed(line)
                        continue

                    elapsed = time.monotonic() - start_time
                    if log_file is not None:
                        log_file.write(format_log_line(elapsed, line) + "\n")
                        log_file.flush()

                    self.queue.put(("sample", (elapsed, sample)))
        except serial.SerialException as exc:
            self.queue.put(("error", str(exc)))
        finally:
            if log_file is not None:
                log_file.close()
