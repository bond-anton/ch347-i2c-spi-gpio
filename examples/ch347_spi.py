#!/usr/bin/env python3
"""Shared helpers for the CH347 spidev examples.

The CH347 exposes two chip selects as separate spidev nodes:

    /dev/spidev0.0   CS0   MCP3208 ADC
    /dev/spidev0.1   CS1   MCP4251 digital pot

Both are opened independently; a single spi_message can only target one
spi_device, so a pot write and an ADC read are always two separate ioctls.

Protocol notes that the code below depends on:

- The CH347 always returns 0xff as the first byte of a 0xC2 response, so
  real MISO data starts at byte 1.
- The MCP3208 converts once per CS-low window. A second read inside the
  same window returns 0, so ADC reads cannot be batched.
- The MCP4251 wiper register is 8-bit with 257 steps (0..256). Writing 256
  wraps to 0.
- The MCP4251 address mapping is inverted from the datasheet: address 0x0
  drives wiper 0 (pin 9), address 0x1 drives wiper 1 (pin 6).
"""

import ctypes
import fcntl
import os
import struct

SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_BITS_PER_WORD = 0x40016B03
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04

SPI_MODE_0 = 0
DEFAULT_SPEED_HZ = 468_750

POT_DEVICE = "/dev/spidev0.1"
ADC_DEVICE = "/dev/spidev0.0"

POT_MAX = 256
ADC_MAX = 4095
VREF = 5.0

MCP4251_WIPER0 = 0x0
MCP4251_WIPER1 = 0x1
MCP4251_CMD_WRITE = 0x0


def msg_ioctl(n):
    """SPI_IOC_MESSAGE(N) = _IOW(SPI_IOC_MAGIC, 0, char[N * 32]).

    The size field encodes N, so the ioctl number differs for every message
    length. Using a fixed number silently sends only the first transfer.
    """
    return 0x40000000 | ((n * 32) << 16) | (0x6B << 8)


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


def open_spi(path, speed_hz=DEFAULT_SPEED_HZ):
    """Open a spidev node in mode 0, 8 bits per word."""
    fd = os.open(path, os.O_RDWR)
    fcntl.ioctl(fd, SPI_IOC_WR_MODE, struct.pack("B", SPI_MODE_0))
    fcntl.ioctl(fd, SPI_IOC_WR_BITS_PER_WORD, struct.pack("B", 8))
    fcntl.ioctl(fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("I", speed_hz))
    return fd


def xfer(fd, tx, speed_hz=DEFAULT_SPEED_HZ):
    """Full-duplex transfer of one buffer; returns the received bytes.

    tx and rx must be separate buffers. Sharing one buffer makes spidev
    return the transmitted data instead of what came back on MISO.
    """
    tx = bytes(tx)
    n = len(tx)
    txbuf = ctypes.create_string_buffer(tx, n)
    rxbuf = ctypes.create_string_buffer(n)
    tr = SpiIocTransfer(
        tx_buf=ctypes.addressof(txbuf),
        rx_buf=ctypes.addressof(rxbuf),
        len=n,
        speed_hz=speed_hz,
        delay_usecs=0,
        bits_per_word=8,
        cs_change=0,
        tx_nbits=0,
        rx_nbits=0,
        word_delay_usecs=0,
        pad=0,
    )
    fcntl.ioctl(fd, msg_ioctl(1), tr)
    return rxbuf.raw[:n]


def pot_write(fd, wiper, value, speed_hz=DEFAULT_SPEED_HZ):
    """Write an 8-bit wiper value (0..256) to the MCP4251.

    Frame: (addr << 12) | (cmd << 10) | data8, sent as two bytes MSB first.
    """
    if not 0 <= value <= POT_MAX:
        raise ValueError(f"wiper value outside 0..{POT_MAX}: {value}")
    word = (wiper << 12) | (MCP4251_CMD_WRITE << 10) | (value & 0xFF)
    xfer(fd, word.to_bytes(2, "big"), speed_hz)


def pot_batch(fd, wiper, values, speed_hz=DEFAULT_SPEED_HZ):
    """Write several wiper values in one spi_message.

    One CS assert covers all transfers, which removes the per-step CS pair.
    The wiper ends at the last value in the list.
    """
    bufs = []
    arr = (SpiIocTransfer * len(values))()
    for i, value in enumerate(values):
        if not 0 <= value <= POT_MAX:
            raise ValueError(f"wiper value outside 0..{POT_MAX}: {value}")
        word = (wiper << 12) | (MCP4251_CMD_WRITE << 10) | (value & 0xFF)
        txbuf = ctypes.create_string_buffer(word.to_bytes(2, "big"), 2)
        rxbuf = ctypes.create_string_buffer(2)
        bufs.append((txbuf, rxbuf))
        arr[i] = SpiIocTransfer(
            tx_buf=ctypes.addressof(txbuf),
            rx_buf=ctypes.addressof(rxbuf),
            len=2,
            speed_hz=speed_hz,
            delay_usecs=0,
            bits_per_word=8,
            cs_change=0,
            tx_nbits=0,
            rx_nbits=0,
            word_delay_usecs=0,
            pad=0,
        )
    fcntl.ioctl(fd, msg_ioctl(len(values)), arr)


def adc_read(fd, channel, speed_hz=DEFAULT_SPEED_HZ):
    """Read the 12-bit code from an MCP3208 channel (0..7).

    Frame: start bit, single-ended, channel select, then two don't-care
    bytes. Byte 0 of the response is the CH347 0xff artifact; the code is
    in bytes 1 and 2.
    """
    if not 0 <= channel <= 7:
        raise ValueError(f"channel outside 0..7: {channel}")
    tx = ((0b10000 | (1 << 3) | channel) << 14).to_bytes(3, "big")
    rx = xfer(fd, tx, speed_hz)
    return ((rx[1] << 8) | rx[2]) & 0xFFF


def to_volts(code):
    return code * VREF / ADC_MAX
