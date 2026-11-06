# ch347-linux-driver

Out-of-tree Linux kernel driver for the **WCH CH347** USB-to-SPI/I2C/GPIO
adapter, ported to work on modern kernels.

**Fork of [zetta-shao/ch347-i2c-spi-gpio](https://github.com/zetta-shao/ch347-i2c-spi-gpio).**
The upstream tree does not compile on kernels ≥ 6.0 and ships with SPI
disabled; this fork fixes both and adds the SPI controller work described in
[Port notes](#port-notes--what-changed-from-upstream).

## Build environment

| | |
|---|---|
| Kernel | **7.2.15-300.fc45.x86_64** |
| Distribution | Fedora 45 |
| Architecture | x86_64 |
| Secure Boot | enabled — the module must be signed |

The Makefile takes the kernel from `uname -r`, so any kernel with matching
`kernel-devel` headers should work. The version above is what this tree is
built and tested against; older kernels are untested.

## License

GPL-2.0. Every source file carries an `SPDX-License-Identifier: GPL-2.0` tag
and `ch347-core.c` declares `MODULE_LICENSE("GPL v2")`. Full text in
[LICENSE](LICENSE).

This is a derivative work of the upstream driver, which is itself GPL-2.0.
Copyright remains with the original authors — see the headers in each source
file.

## Target hardware

Waveshare USB-to-UART/I2C/SPI/JTAG adapter, `1a86:55db` — CH347T in **Mode 1**
(UART + SPI + I2C, no JTAG).

| Interface | Function | Driver |
|---|---|---|
| 0 | UART | `cdc_acm` → `/dev/ttyACM0` |
| 2 | SPI + I2C + GPIO | **this driver** |

The driver binds interface 2 only, so `cdc_acm` keeps interface 0 and the
serial port keeps working.

Mode 3 (`1a86:55dd`, JTAG) is also in the ID table but is not used here.
Switching modes requires a one-way EEPROM reconfiguration with WCH's Windows
tool.

## What this driver gives you

- `/dev/spidevX.Y` — real kernel `spi_controller`, usable with `spidev`,
  `flashrom`, `spi-tools`, etc.
- `/dev/i2c-N` — I2C adapter
- `gpiochipN` — 8 GPIO lines

SPI is the primary use case. Only **SPI mode 0** is supported
(`mode_bits = SPI_MODE_0 | SPI_LSB_FIRST`, 8 bits per word).

## Build

Requires `kernel-devel` matching the running kernel:

```sh
sudo dnf install kernel-devel kernel-headers
```

Then:

```sh
./build.sh
```

`build.sh` runs `make` and then **signs** the module with the local MOK key.
Signing must follow every build — `make` overwrites `ch347-buses.ko` and the
signature is lost.

Plain `make` works too, but produces an unsigned module that Secure Boot will
refuse to load.

## Secure Boot

Secure Boot is enabled on this machine, so an unsigned out-of-tree module will
not load. The module is signed with a locally generated key that must be
enrolled in the kernel's MOK (Machine Owner Key) list.

### One-time setup

```sh
./enroll-mok.sh
```

This copies the key to `/var/lib/dkms/` and runs `mokutil --import`. It asks
for a **one-time password** — remember it, you need it after reboot.

Then reboot. In the blue **MOK Manager** screen:

```
Enroll MOK -> Continue -> Yes -> <one-time password> -> Reboot
```

Verify after boot:

```sh
mokutil --list-enrolled | grep -A1 "Local Module Signing Key"
```

### Current state on this machine

The key is **queued but not yet enrolled** — `mokutil --list-new` shows it,
`mokutil --list-enrolled` does not. Until the reboot + MOK Manager step is
done, loading fails with:

```
Loading of module with unavailable key is rejected
```

That message means the signature is present and well-formed but the signing
cert is not in the kernel's trusted keyring yet. It is not a build problem.

### Key files

| File | Purpose |
|---|---|
| `mok/mok.key` | Private signing key (`chmod 600`) |
| `mok/mok.pub` | PEM certificate |
| `mok/mok.der` | DER certificate — **this** is what `mokutil --import` needs |

`mokutil --import` rejects PEM with
`is not a valid x509 certificate in DER format`. `enroll-mok.sh` regenerates
the DER from the PEM on every run, so this cannot recur.

The key pair is valid until 2126. If you regenerate it, every previously
signed module must be re-signed and the new cert re-enrolled.

## Load and create the spidev node

```sh
sudo modprobe ch347-buses
ls /sys/class/spi_master/
echo "spidev 0" | sudo tee /sys/class/spi_master/spi0/new_device
```

**`spidev` will not bind automatically.** The device is created with modalias
`spi:spidev`, but `spidev`'s probe rejects it unless `driver_override` is set
first. Without this step you get `spi0.0` in `/sys/bus/spi/devices/` but no
`/dev/spidev0.0`, and a manual `bind` fails with `ENODEV` and no kernel
message:

```sh
echo spidev | sudo tee /sys/bus/spi/devices/spi0.0/driver_override
echo spi0.0 | sudo tee /sys/bus/spi/drivers/spidev/bind
ls -l /dev/spidev0.0
```

Verify the configuration:

```sh
sudo python3 -c "
import fcntl, struct, os
fd = os.open('/dev/spidev0.0', os.O_RDWR)
print('mode      =', struct.unpack('B', fcntl.ioctl(fd, 0x80016b01, b'\x00'))[0])
print('bits/word =', struct.unpack('B', fcntl.ioctl(fd, 0x80016b03, b'\x00'))[0])
print('max_speed =', struct.unpack('I', fcntl.ioctl(fd, 0x80046b04, b'\x00'*4))[0])
os.close(fd)
"
```

Expected: `mode = 0`, `bits/word = 8`, `max_speed = 1500000`.

The `new_device` sysfs entry takes `<modalias> <chip_select>`. Other useful
aliases:

```sh
echo "spi-nor 0" | sudo tee /sys/class/spi_master/spi0/new_device
echo "0" | sudo tee /sys/class/spi_master/spi0/delete_device
```

### Persist the spidev nodes across replug and reboot

The `new_device` / `driver_override` / `bind` writes above are manual sysfs
work, so the nodes disappear on a USB replug and do not come back at boot. A
udev rule recreates them whenever the `spi0` master appears:

`/etc/udev/rules.d/99-ch347-spidev.rules`

```
ACTION=="add", SUBSYSTEM=="spi_master", KERNEL=="spi0", \
  RUN+="/bin/sh -c 'for n in 0 1; do echo spidev $n > /sys/class/spi_master/spi0/new_device 2>/dev/null; echo spidev > /sys/bus/spi/devices/spi0.$n/driver_override 2>/dev/null; echo spi0.$n > /sys/bus/spi/drivers/spidev/bind 2>/dev/null; done; true'"
```

Install it with:

```sh
sudo tee /etc/udev/rules.d/99-ch347-spidev.rules >/dev/null <<'EOF'
ACTION=="add", SUBSYSTEM=="spi_master", KERNEL=="spi0", \
  RUN+="/bin/sh -c 'for n in 0 1; do echo spidev $n > /sys/class/spi_master/spi0/new_device 2>/dev/null; echo spidev > /sys/bus/spi/devices/spi0.$n/driver_override 2>/dev/null; echo spi0.$n > /sys/bus/spi/drivers/spidev/bind 2>/dev/null; done; true'"
EOF
sudo udevadm control --reload-rules
```

Notes:

- **`new_device` alone is not enough.** It creates the `spi_device` but does not
  bind `spidev`, so each child also needs `driver_override` plus an explicit
  `bind`. Doing only `new_device` leaves `spi0.0` present but driverless.
- **Survives reboot.** The module carries a USB alias
  (`usb:v1A86p55DBd*...`), so udev autoloads it on enumeration; the rule then
  fires on the `spi_master` add event. No `modules-load.d` entry is needed.
- **Adapter unplugged at boot** → module not loaded, no nodes. Plugging it in
  later runs the same chain.
- The rule covers both chip selects. `setup-spidev.sh` only creates `spi0.0`.

Verify after a replug:

```sh
ls -la /dev/spidev*
for n in 0 1; do echo -n "spi0.$n: "; readlink /sys/bus/spi/devices/spi0.$n/driver || echo unbound; done
```

Both must resolve to `.../bus/spi/drivers/spidev`.

### Transfer timeout with nothing connected

A transfer with no device wired to SCK/MOSI/MISO blocks in the USB bulk read
and returns `ETIMEDOUT` (errno 110). This is expected — the controller is
working, there is simply no slave driving MISO. The `statistics/` counters
stay at zero because the message never reaches the controller.

## Port notes — what changed from upstream

Upstream was written for kernel 5.19 and **never compiled `ch347-spi.c`**
(it is commented out of the Makefile and its struct fields are inside `#if 0`).
Several symbols it references were never defined at all.

### Build system

| File | Problem | Fix |
|---|---|---|
| `Makefile` | `ch347-spi.o` on its own line, not part of `ch347-buses-objs` | merged into the object list |
| `ch347.h` | SPI struct fields and prototypes wrapped in `#if 0` | guards removed |
| `ch347-core.c` | `CH347_spi_init`/`CH347_spi_remove` calls wrapped in `#if 0` — SPI never initialised at probe | guards removed |

### Naming

| Problem | Fix |
|---|---|
| `#include "ch341.h"` — file does not exist | `#include "ch347.h"` |
| `struct ch341_device` — actual name is `CH347_device` | renamed throughout |
| `ch341_spi_init` / `ch341_spi_remove` — core calls `CH347_spi_*` | renamed |
| `CH341_SPI_MAX_NUM_DEVICES`, `CH341_SPI_NSEGS` | `CH347_*` |
| `spi_gpio_cs[CH347_SPI_MAX_NUM_DEVICES]` held 4 entries in a 2-element array | unsized array |

### Kernel API changes

**SPI controller API** (renamed in kernel 6.0):

| Old | New |
|---|---|
| `struct spi_master` | `struct spi_controller` |
| `spi_alloc_master()` | `spi_alloc_host()` |
| `spi_master_set_devdata()` | `spi_controller_set_devdata()` |
| `spi_master_get_devdata()` | `spi_controller_get_devdata()` |
| `spi_register_master()` | `spi_register_controller()` |
| `spi_unregister_master()` | `spi_unregister_controller()` |
| `spi_master_put()` | `spi_controller_put()` |
| `SPI_MASTER_MUST_RX` / `SPI_MASTER_MUST_TX` | `SPI_CONTROLLER_MUST_RX` / `SPI_CONTROLLER_MUST_TX` |

**Multi-chip-select** (kernel 6.13): `spi_device.chip_select` changed from
`u16` to `u8 chip_select[4]`. Direct indexing no longer compiles:

```c
/* before */
&dev->spi_clients[spi->chip_select]
/* after */
&dev->spi_clients[spi_get_chipselect(spi, 0)]
```

**GPIO callbacks** (kernel 6.1): `gpio_chip.set` and `.set_multiple` must
return `int`, not `void`. `ch347_gpio_set` now returns `-EINVAL` for an
out-of-range offset instead of silently returning.

`ch347-core.c` and `ch347-i2c.c` compiled unmodified.

## GPIO pin usage

The GPIO chip exposes 8 lines. SPI does **not** claim them — there is no
`gpiochip_request_own_desc` call in this tree, so the GPIO interface and SPI
can both be used, and nothing returns `-EBUSY`.

The lines are shared with the SPI signals at the hardware level:

| Line | Signal |
|---|---|
| 0, 1, 2, 4 | CS0–CS3 |
| 3 | SCK |
| 5 | MOSI |
| 7 | MISO |

Driving these lines through the GPIO interface while SPI traffic is running
will corrupt the bus. The driver does not prevent it.

## Files

| File | Purpose |
|---|---|
| `ch347-core.c` | USB probe/disconnect, ID table |
| `ch347-spi.c` | SPI controller |
| `ch347-i2c.c` | I2C adapter |
| `ch347-gpio.c` | GPIO chip |
| `ch347.h` | Shared device struct and prototypes |
| `build.sh` | Build + sign |
| `enroll-mok.sh` | One-time MOK enrollment |
| `dkms.conf` | DKMS packaging (`PACKAGE_NAME=ch347-linux-driver`) |
| `ch341-i2c-dissector.lua` | Wireshark dissector for the USB protocol |
| `examples/` | MCP4251 pot and MCP3208 ADC scripts, plus benchmarks |

## Examples

`examples/` holds working Python scripts for an MCP4251 digital pot on CS1
and an MCP3208 ADC on CS0, driven through `/dev/spidev0.1` and
`/dev/spidev0.0`. They use raw `ioctl` calls, so only the standard library
is needed.

| Script | Purpose |
|---|---|
| `ch347_spi.py` | Shared helpers: open, transfer, pot write, ADC read |
| `pot_sweep.py` | Sweep both wipers, print the ADC codes |
| `adc_read.py` | Read all eight MCP3208 channels once |
| `pot_adc_loop.py` | Closed loop: set the pot, read the ADC, check linearity |
| `bench_pot_sweep.py` | Write-only pot sweep, unbatched vs batched |
| `bench_roundtrip.py` | Set/read round trip, unbatched vs batched |

See [examples/README.md](examples/README.md) for wiring, expected results
and the protocol details the scripts depend on.

## Alternatives considered

- **WCH `ch34x_pis.ko` + `libch347.so`** (from `CH341PAR_LINUX_ZIP` /
  `YTEC-info/CH347-Softwares`) — userspace API (`CH347SPI_Init`,
  `CH347SPI_WriteRead`) over `/dev/ch34x_pis*`. Does **not** create
  `/dev/spidevX`, so it cannot satisfy a spidev requirement. Fallback only.
- **`ch343ser_linux`** — UART only, no SPI.
- **WCH `ch347` OpenOCD driver** — JTAG/SWD only on Linux.
- **Mainline kernel** — no driver for `1a86:55db` SPI; `ch341.ko` aliases do
  not cover it.
