#!/usr/bin/env python3
"""Read every MCP3208 channel once and print the code and voltage.

With the pot at mid-scale, CH0 and CH1 should both read about 2042 codes
(2.49 V). The other channels float unless something is wired to them.

Usage:
    sudo python3 adc_read.py
    sudo python3 adc_read.py --repeat 10
"""

import argparse
import os
import sys
import time

import ch347_spi as spi


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repeat", type=int, default=1, help="number of full passes (1 by default)"
    )
    parser.add_argument(
        "--delay", type=float, default=0.5, help="seconds between passes"
    )
    args = parser.parse_args()

    if not os.path.exists(spi.ADC_DEVICE):
        sys.exit(f"{spi.ADC_DEVICE} not found — load the driver and create spidev")

    fd = spi.open_spi(spi.ADC_DEVICE)
    try:
        for pass_no in range(args.repeat):
            if pass_no:
                time.sleep(args.delay)
            print(f"pass {pass_no + 1}")
            for channel in range(8):
                code = spi.adc_read(fd, channel)
                print(f"  CH{channel}  {code:4d}  {spi.to_volts(code):6.3f} V")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
