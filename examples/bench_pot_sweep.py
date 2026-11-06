#!/usr/bin/env python3
"""Benchmark a write-only pot sweep, unbatched against batched.

Each pot write costs three USB round-trips: CS assert, data out/in, CS
deassert. Batching several writes into one spi_message removes the CS pair
from all but the first and last transfer, which roughly halves the cost.

The SPI clock makes no measurable difference: a 2-byte transfer is about
40 ns of wire time at 468.75 kHz, while each USB round-trip is 50-70 us.

Usage:
    sudo python3 bench_pot_sweep.py
"""

import os
import sys
import time

import ch347_spi as spi

REPEATS = 20


def bench(label, fn, repeats, steps_per_call):
    for _ in range(5):
        fn()
    start = time.perf_counter()
    for _ in range(repeats):
        fn()
    elapsed = time.perf_counter() - start
    per_call = elapsed / repeats * 1e6
    per_step = per_call / steps_per_call
    print(
        f"  {label:34s} {per_call:9.1f} us/sweep  "
        f"{per_step:7.1f} us/step  {1e6 / per_step:7.0f} steps/s"
    )


def main():
    if not os.path.exists(spi.POT_DEVICE):
        sys.exit(f"{spi.POT_DEVICE} not found — load the driver and create spidev")

    fd = spi.open_spi(spi.POT_DEVICE)
    try:
        print(
            f"write-only pot sweep, {spi.POT_MAX + 1} steps, "
            f"{spi.DEFAULT_SPEED_HZ} Hz\n"
        )

        def unbatched():
            for value in range(spi.POT_MAX + 1):
                spi.pot_write(fd, spi.MCP4251_WIPER0, value)

        bench("unbatched (1 ioctl/step)", unbatched, REPEATS, spi.POT_MAX + 1)

        for chunk in (8, 32, 64, 128, spi.POT_MAX + 1):

            def batched(chunk=chunk):
                for base in range(0, spi.POT_MAX + 1, chunk):
                    values = list(range(base, min(base + chunk, spi.POT_MAX + 1)))
                    spi.pot_batch(fd, spi.MCP4251_WIPER0, values)

            bench(f"batched, {chunk} steps/message", batched, REPEATS, spi.POT_MAX + 1)

        print()
        bench(
            "single pot write",
            lambda: spi.pot_write(fd, spi.MCP4251_WIPER0, 128),
            2000,
            1,
        )
    finally:
        spi.pot_write(fd, spi.MCP4251_WIPER0, 128)
        spi.pot_write(fd, spi.MCP4251_WIPER1, 128)
        os.close(fd)


if __name__ == "__main__":
    main()
