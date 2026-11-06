#!/usr/bin/env python3
"""Test of the MCP4251 (digital potentiometer) + MCP3208 (12-bit ADC) pair
through /dev/spidev0.0 (CH347 driver).

Wiring: the MCP4251 output (PA0/PW0) feeds the CH0 and CH1 inputs of the
MCP3208. Vref = 5 V, the potentiometer range is also 0..5 V.

The script steps through the MCP4251 wiper position and checks that the ADC
code changes monotonically and stays within the expected range.

Usage (requires access to /dev/spidev0.0):
    sudo python3 mcp4251_mcp3208_test.py
    sudo python3 mcp4251_mcp3208_test.py --sweep   # full sweep 0..255
"""

import argparse
import ctypes
import fcntl
import os
import struct
import sys
import time

SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
SPI_IOC_MESSAGE_1 = 0x40206B00


class SpiIocTransfer(ctypes.Structure):
    """struct spi_ioc_transfer from linux/spi/spidev.h (32 bytes)."""

    _fields_ = [
        ("tx_buf", ctypes.c_uint64),
        ("rx_buf", ctypes.c_uint64),
        ("len", ctypes.c_uint32),
        ("speed_hz", ctypes.c_uint32),
        ("delay_usecs", ctypes.c_uint16),
        ("bits_per_word", ctypes.c_uint8),
        ("cs_change", ctypes.c_uint8),
        ("tx_nbits", ctypes.c_uint8),
        ("rx_nbits", ctypes.c_uint8),
        ("word_delay_usecs", ctypes.c_uint8),
        ("pad", ctypes.c_uint8),
    ]


SPI_MODE_0 = 0

DEVICE = "/dev/spidev0.0"
SPEED_HZ = 1_000_000

VREF = 5.0
ADC_MAX = 4095
POT_MAX = 255

# MCP4251: 16-bit command (AD3..AD0 | C1 C0 | D9 D8 | D7..D0).
# AD = 0x0 -> wiper 0, AD = 0x1 -> wiper 1; C1 C0 = 00 -> write.
MCP4251_WIPER0 = 0x00
MCP4251_WIPER1 = 0x10


def spi_transfer(fd, tx):
    """Full-duplex transfer: returns the received bytes.

    tx and rx are SEPARATE buffers. If they are put in one array, spidev
    copies tx over rx and returns what was sent instead of what was
    received.
    """
    tx = bytes(tx)
    n = len(tx)
    txbuf = ctypes.create_string_buffer(tx, n)
    rxbuf = ctypes.create_string_buffer(n)
    tr = SpiIocTransfer(
        tx_buf=ctypes.addressof(txbuf),
        rx_buf=ctypes.addressof(rxbuf),
        len=n,
        speed_hz=SPEED_HZ,
        delay_usecs=0,
        bits_per_word=8,
        cs_change=0,
        tx_nbits=0,
        rx_nbits=0,
        word_delay_usecs=0,
        pad=0,
    )
    fcntl.ioctl(fd, SPI_IOC_MESSAGE_1, tr)
    return rxbuf.raw[:n]


def mcp4251_set_wiper(fd, wiper_addr, value):
    """Write an 8-bit value (0..255) to an MCP4251 wiper."""
    if not 0 <= value <= POT_MAX:
        raise ValueError(f"wiper value outside 0..{POT_MAX}: {value}")
    word = (wiper_addr << 8) | value
    spi_transfer(fd, struct.pack(">H", word))


def mcp3208_read(fd, channel):
    """Read the 12-bit code from an MCP3208 channel (0..7)."""
    if not 0 <= channel <= 7:
        raise ValueError(f"channel outside 0..7: {channel}")
    tx = bytes([0x06 | (channel >> 2), (channel & 0x03) << 6, 0x00])
    rx = spi_transfer(fd, tx)
    return ((rx[1] & 0x0F) << 8) | rx[2]


def open_spi():
    fd = os.open(DEVICE, os.O_RDWR)
    fcntl.ioctl(fd, SPI_IOC_WR_MODE, struct.pack("B", SPI_MODE_0))
    fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", 8))
    fcntl.ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("I", SPEED_HZ))
    return fd


def to_volts(code):
    return code * VREF / ADC_MAX


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="step through all 256 wiper positions",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=16,
        help="number of steps in a normal run (16 by default)",
    )
    parser.add_argument(
        "--channel",
        type=int,
        default=0,
        help="MCP3208 channel for the main run (0 or 1)",
    )
    args = parser.parse_args()

    if not os.path.exists(DEVICE):
        sys.exit(f"{DEVICE} not found — load the driver and create spidev")

    fd = open_spi()
    try:
        print(f"SPI: {DEVICE}, mode 0, 8 bit, {SPEED_HZ} Hz")
        print(f"Vref = {VREF} V, 1 code = {VREF / ADC_MAX * 1000:.3f} mV\n")

        # 1. Both channels must see the same potentiometer output.
        mcp4251_set_wiper(fd, MCP4251_WIPER0, 128)
        time.sleep(0.01)
        ch0 = mcp3208_read(fd, 0)
        ch1 = mcp3208_read(fd, 1)
        print(
            f"Wiper = 128: CH0 = {ch0:4d} ({to_volts(ch0):.3f} V), "
            f"CH1 = {ch1:4d} ({to_volts(ch1):.3f} V)"
        )
        if abs(ch0 - ch1) > 64:
            print(
                "  WARNING: channels differ by more than 64 codes — "
                "check that both inputs are tied to the potentiometer output"
            )

        # 2. Step through the wiper positions.
        steps = (
            range(0, POT_MAX + 1)
            if args.sweep
            else [round(i * POT_MAX / (args.steps - 1)) for i in range(args.steps)]
        )

        print(f"\nWiper sweep, channel {args.channel}:")
        print(f"{'wiper':>7} {'code':>6} {'voltage':>12} {'expected':>10}")
        results = []
        for pos in steps:
            mcp4251_set_wiper(fd, MCP4251_WIPER0, pos)
            time.sleep(0.005)
            code = mcp3208_read(fd, args.channel)
            expected = pos * VREF / POT_MAX
            results.append((pos, code))
            print(f"{pos:7d} {code:6d} {to_volts(code):10.3f} V {expected:8.3f} V")

        # 3. Evaluate the result.
        codes = [c for _, c in results]
        monotonic = all(b >= a for a, b in zip(codes, codes[1:]))
        span = codes[-1] - codes[0]
        expected_span = ADC_MAX

        print()
        if not monotonic:
            print("ERROR: ADC code is not monotonic as the wiper advances")
        if span < expected_span * 0.8:
            print(
                f"ERROR: span {span} codes, expected about "
                f"{expected_span} — check Vref and the wiring"
            )
        if monotonic and span >= expected_span * 0.8:
            print(
                f"OK: code changes monotonically, span {span} of "
                f"{expected_span} codes ({span / expected_span * 100:.1f} %)"
            )
            return 0
        return 1
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
