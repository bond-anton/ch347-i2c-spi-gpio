#!/usr/bin/env python3
"""Benchmark the closed loop: set the pot, read the ADC.

The ADC read cannot be batched — the MCP3208 converts once per CS-low
window and returns 0 for any further read in the same window. So every
step needs its own ADC ioctl, and only the pot write can be batched.

That makes the ADC the floor: about 161 us per step no matter what the pot
side does. Batching the pot writes still removes their CS pair, which is
worth roughly 25 percent.

Usage:
    sudo python3 bench_roundtrip.py
"""

import os
import sys
import time

import ch347_spi as spi

REPEATS = 10


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
        f"  {label:38s} {per_call:9.1f} us/sweep  "
        f"{per_step:7.1f} us/step  {1e6 / per_step:7.0f} steps/s"
    )


def main():
    for path in (spi.POT_DEVICE, spi.ADC_DEVICE):
        if not os.path.exists(path):
            sys.exit(f"{path} not found — load the driver and create spidev")

    pot_fd = spi.open_spi(spi.POT_DEVICE)
    adc_fd = spi.open_spi(spi.ADC_DEVICE)
    try:
        print(
            f"closed loop set/read, {spi.POT_MAX + 1} steps, "
            f"{spi.DEFAULT_SPEED_HZ} Hz\n"
        )

        def unbatched():
            for value in range(spi.POT_MAX + 1):
                spi.pot_write(pot_fd, spi.MCP4251_WIPER0, value)
                spi.adc_read(adc_fd, 0)

        bench("unbatched (2 ioctls/step)", unbatched, REPEATS, spi.POT_MAX + 1)

        for chunk in (8, 32, 64, 128, spi.POT_MAX + 1):

            def pot_batched(chunk=chunk):
                for base in range(0, spi.POT_MAX + 1, chunk):
                    values = list(range(base, min(base + chunk, spi.POT_MAX + 1)))
                    spi.pot_batch(pot_fd, spi.MCP4251_WIPER0, values)
                    for _ in values:
                        spi.adc_read(adc_fd, 0)

            bench(
                f"pot batched {chunk}/msg, ADC 1/step",
                pot_batched,
                REPEATS,
                spi.POT_MAX + 1,
            )

        print()
        bench(
            "single set + read",
            lambda: (
                spi.pot_write(pot_fd, spi.MCP4251_WIPER0, 128),
                spi.adc_read(adc_fd, 0),
            ),
            2000,
            1,
        )
    finally:
        spi.pot_write(pot_fd, spi.MCP4251_WIPER0, 128)
        spi.pot_write(pot_fd, spi.MCP4251_WIPER1, 128)
        os.close(pot_fd)
        os.close(adc_fd)


if __name__ == "__main__":
    main()
