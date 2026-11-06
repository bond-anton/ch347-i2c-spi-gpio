#!/usr/bin/env python3
"""Closed loop: set the pot, read the ADC, check the response is linear.

This replaces the old mcp4251_mcp3208_test.py, which built the wiper word
with the wrong shift and never moved the wiper.

The ADC is read once per wiper step. Reads cannot be batched: the MCP3208
converts once per CS-low window and returns 0 for any further read in the
same window.

Usage:
    sudo python3 pot_adc_loop.py
    sudo python3 pot_adc_loop.py --step 4 --channel 1
"""

import argparse
import os
import sys
import time

import ch347_spi as spi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step", type=int, default=16, help="wiper increment (16 by default)"
    )
    parser.add_argument(
        "--channel", type=int, default=0, help="MCP3208 channel to read (0 by default)"
    )
    parser.add_argument(
        "--wiper",
        type=int,
        default=spi.MCP4251_WIPER0,
        help="MCP4251 wiper (0 by default)",
    )
    args = parser.parse_args()

    for path in (spi.POT_DEVICE, spi.ADC_DEVICE):
        if not os.path.exists(path):
            sys.exit(f"{path} not found — load the driver and create spidev")

    pot_fd = spi.open_spi(spi.POT_DEVICE)
    adc_fd = spi.open_spi(spi.ADC_DEVICE)
    try:
        print(f"wiper {args.wiper} -> CH{args.channel}")
        print(f"{'wiper':>7} {'code':>6} {'voltage':>10} {'expected':>10}")

        results = []
        for value in range(0, spi.POT_MAX + 1, args.step):
            spi.pot_write(pot_fd, args.wiper, value)
            time.sleep(0.02)
            code = spi.adc_read(adc_fd, args.channel)
            expected = value * spi.VREF / spi.POT_MAX
            results.append((value, code))
            print(f"{value:7d} {code:6d} {spi.to_volts(code):8.3f} V {expected:8.3f} V")

        # Wiper 256 wraps to 0, so the last sample is excluded from the
        # monotonic check and from the span.
        codes = [code for value, code in results if value < spi.POT_MAX]
        monotonic = all(b >= a for a, b in zip(codes, codes[1:]))
        span = codes[-1] - codes[0]

        print()
        if not monotonic:
            print("ERROR: ADC code is not monotonic as the wiper advances")
            return 1
        if span < spi.ADC_MAX * 0.6:
            print(
                f"ERROR: span {span} codes, expected about {spi.ADC_MAX} — "
                "check Vref and the wiring"
            )
            return 1
        print(f"OK: monotonic, span {span} of {spi.ADC_MAX} codes")
        return 0
    finally:
        spi.pot_write(pot_fd, spi.MCP4251_WIPER0, 128)
        spi.pot_write(pot_fd, spi.MCP4251_WIPER1, 128)
        os.close(pot_fd)
        os.close(adc_fd)


if __name__ == "__main__":
    sys.exit(main())
