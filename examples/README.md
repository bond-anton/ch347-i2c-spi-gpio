# Examples

Python scripts driving an MCP4251 digital pot and an MCP3208 ADC through
the CH347 spidev nodes. They use raw `ioctl` calls, so no extra packages
are needed beyond the standard library.

## Wiring

| CH347 | Signal | MCP4251 | MCP3208 |
|---|---|---|---|
| CS0 | `/dev/spidev0.0` | — | pin 10 (CS/SHDN) |
| CS1 | `/dev/spidev0.1` | pin 1 (CS) | — |
| SCK | | pin 2 | pin 13 |
| MOSI (SDO) | | pin 3 (SDI) | pin 11 (Din) |
| MISO (SDI) | | pin 13 (SDO) via 100 Ω | pin 12 (Dout) |

The pot outputs feed the ADC inputs:

- MCP4251 pin 9 (P0W) → MCP3208 pin 1 (CH0)
- MCP4251 pin 6 (P1W) → MCP3208 pin 2 (CH1)

The Waveshare header names signals from the controller's perspective, so
**SDI is MISO** (controller input) and **SDO is MOSI** (controller output).

## Scripts

| Script | Purpose |
|---|---|
| `ch347_spi.py` | Shared helpers: open, transfer, pot write, ADC read |
| `pot_sweep.py` | Sweep both wipers, print the ADC codes |
| `adc_read.py` | Read all eight MCP3208 channels once |
| `pot_adc_loop.py` | Closed loop: set the pot, read the ADC, check linearity |
| `bench_pot_sweep.py` | Write-only pot sweep, unbatched vs batched |
| `bench_roundtrip.py` | Set/read round trip, unbatched vs batched |

Run any of them as root, from this directory:

```bash
sudo python3 pot_sweep.py
sudo python3 pot_adc_loop.py --step 4 --channel 1
sudo python3 bench_roundtrip.py
```

## Expected results

With Vref at 5 V and the wiper feeding an LM358 buffer, a sweep looks like
this:

| wiper | CH0 |
|---|---|
| 0 | 18 |
| 64 | 1030 |
| 128 | 2042 |
| 176 | 2803 |
| 192 | 3013 |
| 256 | 18 |

The plateau at about 3013 codes is the LM358 running out of headroom at
5 V, not an ADC fault. Wiper 256 wraps to 0 because the wiper register is
8-bit.

## Protocol notes

These are the details the scripts depend on. They were all confirmed on
hardware.

- **The CH347 returns `0xff` as the first byte of every `0xC2` response.**
  Real MISO data starts at byte 1.
- **The MCP3208 converts once per CS-low window.** A second read in the
  same window returns 0, so ADC reads cannot be batched.
- **The MCP4251 wiper is 8-bit with 257 steps (0..256).** Writing 256 wraps
  to 0. A 10-bit value wraps every 256 steps and looks like a broken pot.
- **The MCP4251 address mapping is inverted from the datasheet:** address
  `0x0` drives wiper 0 (pin 9), address `0x1` drives wiper 1 (pin 6).
- **16-bit SPI mode is broken in this firmware.** Responses derive from the
  TX data rather than MISO. Use 8-bit mode.
- **`SPI_IOC_MESSAGE(N)` encodes N in the ioctl number.** The size field is
  `N * 32`, so a fixed number silently sends only the first transfer.

## Benchmark results

Measured on a 480 Mb/s USB link at 468.75 kHz, 257 steps:

| Workload | Per sweep | Per step |
|---|---|---|
| Pot sweep, unbatched | 41.4 ms | 161.2 µs |
| Pot sweep, batched | 21.1 ms | 82.0 µs |
| Set/read loop, unbatched | 83.6 ms | 325.2 µs |
| Set/read loop, pot batched | 63.8 ms | 248.2 µs |

The SPI clock has no measurable effect — 468.75 kHz through 8 MHz all give
the same numbers. Each operation is three USB round-trips (CS assert, data
out/in, CS deassert) at 50-70 µs each, so the link latency dominates.

Batching the pot writes removes the CS pair from all but the first and last
transfer. The ADC read cannot be batched, which is why the closed loop
stops at about 248 µs per step.
