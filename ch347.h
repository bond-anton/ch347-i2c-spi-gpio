/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Definitions for CH347 driver
 */

#include <linux/usb.h>
#include <linux/i2c.h>
#include <linux/gpio.h>
#include <linux/gpio/driver.h>
#include <linux/spi/spi.h>

#define DEFAULT_TIMEOUT 1000	/* 1s USB requests timeout */

#define SEG_SIZE 512

/* Number of SPI devices the device can control (SCS0 and SCS1) */
#define CH347_SPI_MAX_NUM_DEVICES 2

/* CH347 frame: command (1 byte) + data length (2 bytes LE) + the data
 * itself. The firmware accepts no more than 510 bytes per bulk packet, so
 * 507 bytes are left for the payload. */
#define CH347_PACKET_SIZE 510
#define CH347_MAX_DATA_LEN (CH347_PACKET_SIZE - 3)

/* CH347 SPI interface commands. */
#define CH347_CMD_SPI_SET_CFG 0xC0	/* write configuration, read ACK */
#define CH347_CMD_SPI_CS_CTRL 0xC1	/* control SCS0/SCS1, no response */
#define CH347_CMD_SPI_OUT_IN 0xC2	/* full duplex: N bytes out and back */
#define CH347_CMD_SPI_IN 0xC3		/* read: 4-byte length, then data */
#define CH347_CMD_SPI_OUT 0xC4		/* write: N bytes, 4-byte response */
#define CH347_CMD_SPI_GET_CFG 0xCA	/* read configuration */

/* Chip select byte values in the 0xC1 command. CH347_CS_IGNORE and
 * CH347_CS_ASSERT are both zero: an unselected CS simply does not change. */
#define CH347_CS_ASSERT 0x00
#define CH347_CS_DEASSERT 0x40
#define CH347_CS_CHANGE 0x80
#define CH347_CS_IGNORE 0x00

struct CH347_device {
	struct usb_device *usb_dev;
	struct usb_interface *iface;
	struct mutex usb_lock;

    int ep_in;
    int ep_out;

    u8 ibuf[SEG_SIZE];
    u8 obuf[SEG_SIZE];

	struct gpio_chip gpio;
	u8 gpio_ibuf[3 + 8];
	u8 gpio_obuf[3 + 8];

    /* I2C */
	struct i2c_adapter adapter;
	bool i2c_init;

	/* SPI */
	struct spi_controller *master;
	struct mutex spi_lock;
	u8 cs_allocated;	/* bitmask of allocated CS for SPI */
	struct spi_client {
		struct spi_device *slave;
	} spi_clients[CH347_SPI_MAX_NUM_DEVICES];

	/* Command frames live in the struct, not on the stack: usb_bulk_msg
	 * puts the pointer straight into the URB, and kernel stack memory is
	 * not suitable for DMA. */
	u8 spi_tx_frame[CH347_PACKET_SIZE];
	u8 spi_rx_frame[CH347_PACKET_SIZE];

	u8 spi_cfg[26];		/* last applied 0xC0 configuration */
	bool spi_cfg_valid;
};

void CH347_i2c_remove(struct CH347_device *dev);
int CH347_i2c_init(struct CH347_device *dev);
void ch347_gpio_remove(struct CH347_device *dev);
int ch347_gpio_init(struct CH347_device *dev);
void CH347_spi_remove(struct CH347_device *dev);
int CH347_spi_init(struct CH347_device *dev);
