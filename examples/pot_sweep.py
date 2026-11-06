#!/usr/bin/env python3
"""Sweep the MCP4251 wiper and print the resulting ADC codes.

Both wipers are swept in turn. Wiper 0 (pin 9) feeds MCP3208 CH0, wiper 1
(pin 6) feeds CH1, so each sweep is read back on its own channel.

The output should be linear from about 18 codes at wiper 0 to about 3013
at wiper 192, then flat. The plateau is the LM358 buffer running out of
headroom at 5 V, not an ADC fault. Wiper 256 wraps to 0.

Usage:
    sudo python3 pot_sweep.py
    sudo python3 pot_sweep.py --step 8
"""

import argparse
import os
import sys
import time

import ch347_spi as spi


def sweep(pot_fd, adc_fd, wiper, channel, step):
    print(f"wiper {wiper} -> CH{channel}")
    print(f"{'wiper':>7} {'code':>6} {'voltage':>10}")
    for value in range(0, spi.POT_MAX + 1, step):
        spi.pot_write(pot_fd, wiper, value)
        time.sleep(0.02)
        code = spi.adc_read(adc_fd, channel)
        print(f"{value:7d} {code:6d} {spi.to_volts(code):8.3f} V")
    print()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--step", type=int, default=16, help="wiper increment (16 by default)"
    )
    args = parser.parse_args()

    for path in (spi.POT_DEVICE, spi.ADC_DEVICE):
        if not os.path.exists(path):
            sys.exit(f"{path} not found — load the driver and create spidev")

    pot_fd = spi.open_spi(spi.POT_DEVICE)
    adc_fd = spi.open_spi(spi.ADC_DEVICE)
    try:
        sweep(pot_fd, adc_fd, spi.MCP4251_WIPER0, 0, args.step)
        sweep(pot_fd, adc_fd, spi.MCP4251_WIPER1, 1, args.step)
    finally:
        spi.pot_write(pot_fd, spi.MCP4251_WIPER0, 128)
        spi.pot_write(pot_fd, spi.MCP4251_WIPER1, 128)
        os.close(pot_fd)
        os.close(adc_fd)


if __name__ == "__main__":
    main()
