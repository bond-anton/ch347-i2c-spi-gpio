// SPDX-License-Identifier: GPL-2.0
/*
 * SPI interface for the CH347.
 *
 * Copyright 2022, Frank Zago
 * Copyright (c) 2017 Gunar Schorcht (gunar@schorcht.net)
 * Copyright (c) 2016 Tse Lun Bien
 * Copyright (c) 2014 Marco Gittler
 * Copyright (c) 2006-2007 Till Harbaum (Till@Harbaum.org)
 *
 * The CH347 protocol is documented by Nicholas Chin in flashrom
 * (programmers/ch347_spi.c, GPL-2.0-or-later).
 */

#include <linux/minmax.h>
#include <linux/string.h>

#include "ch347.h"

#define CH347_SPI_MAX_FREQ 60000000
#define CH347_SPI_MIN_FREQ 468750

/* Frequency the interface is configured with during probe. There are no
 * devices on the bus yet and CS is low, so the only concern is not to run
 * it too fast. */
#define CH347_SPI_DEFAULT_FREQ 1000000

/* BaudRatePrescaler divisors: index 0 is 60 MHz, each following one is
 * half as fast. The firmware multiplies the index by 8 (see
 * ch347_spi_build_cfg). */
static const u32 ch347_spi_speeds[] = {
	60000000, 30000000, 15000000, 7500000,
	3750000, 1875000, 937500, 468750,
};

/* Pick the fastest divisor that does not exceed the requested frequency.
 * Rounding up would silently run the bus above the limit of the attached
 * device. */
static unsigned int ch347_spi_divisor(unsigned int hz)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(ch347_spi_speeds) - 1; i++)
		if (ch347_spi_speeds[i] <= hz)
			break;

	return i;
}

/* Configuration fields are u16 little-endian, as the firmware expects. */
static void ch347_spi_cfg_put16(u8 *cfg, unsigned int off, u16 val)
{
	cfg[off] = val & 0xff;
	cfg[off + 1] = val >> 8;
}

/* The 26-byte payload of the 0xC0 command is StreamHwCfgS.SPI_InitTypeDef
 * from the vendor library. The firmware ORs all u16 fields into a single
 * SPI_CTLR1 register, so each field carries only its own bits. */
static void ch347_spi_build_cfg(u8 *cfg, unsigned int divisor, u8 bpw,
				u32 mode, bool lsb_first)
{
	memset(cfg, 0, 26);

	ch347_spi_cfg_put16(cfg, 0, 0);			/* 2-line full duplex */
	ch347_spi_cfg_put16(cfg, 2, 0x0104);		/* master */
	ch347_spi_cfg_put16(cfg, 4, bpw == 16 ? 0x0800 : 0);
	ch347_spi_cfg_put16(cfg, 6, (mode & SPI_CPOL) ? 0x0002 : 0);
	ch347_spi_cfg_put16(cfg, 8, (mode & SPI_CPHA) ? 0x0001 : 0);
	ch347_spi_cfg_put16(cfg, 10, 0x0200);		/* software CS */
	ch347_spi_cfg_put16(cfg, 12, divisor << 3);
	ch347_spi_cfg_put16(cfg, 14, lsb_first ? 0x0080 : 0);
	ch347_spi_cfg_put16(cfg, 16, 0x0007);		/* CRC polynomial */

	/* WriteReadInterval, OutDefaultData, OtherCfg and Reserved stay
	 * zero: CS polarity via OtherCfg is not verified, which is why the
	 * driver does not advertise SPI_CS_HIGH either. */
}

/* Builds a command with a 3-byte header and sends the data in one or more
 * bulk packets. Called with usb_lock held. */
static int ch347_usb_send(struct CH347_device *dev, u8 cmd,
			  const void *data, unsigned int len)
{
	const u8 *src = data;
	unsigned int off = 0;

	while (off < len) {
		unsigned int chunk = min(len - off, (unsigned int)CH347_MAX_DATA_LEN);
		int actual;
		int rc;

		dev->spi_tx_frame[0] = cmd;
		dev->spi_tx_frame[1] = chunk & 0xff;
		dev->spi_tx_frame[2] = chunk >> 8;
		if (src)
			memcpy(&dev->spi_tx_frame[3], src + off, chunk);
		else
			memset(&dev->spi_tx_frame[3], 0, chunk);

		dev_info(&dev->master->dev,
			 "TX cmd=0x%02x len=%u t=%lld\n", cmd, chunk,
			 ktime_get_ns());

		rc = usb_bulk_msg(dev->usb_dev,
				  usb_sndbulkpipe(dev->usb_dev, dev->ep_out),
				  dev->spi_tx_frame, chunk + 3, &actual,
				  DEFAULT_TIMEOUT);
		if (rc)
			return rc;
		if (actual != chunk + 3)
			return -EIO;

		off += chunk;
	}

	return 0;
}

/* Reads len bytes into the receive frame without keeping them anywhere.
 * Needed so the endpoint FIFO stays aligned to the start of the next
 * frame. */
static int ch347_usb_drain(struct CH347_device *dev, unsigned int len)
{
	while (len) {
		unsigned int want = min(len, (unsigned int)CH347_PACKET_SIZE);
		int actual;
		int rc;

		rc = usb_bulk_msg(dev->usb_dev,
				  usb_rcvbulkpipe(dev->usb_dev, dev->ep_in),
				  dev->spi_rx_frame, want, &actual,
				  DEFAULT_TIMEOUT);
		if (rc)
			return rc;
		if (!actual)
			return -EIO;

		len -= min(len, (unsigned int)actual);
	}

	return 0;
}

/* Receives a response: 3-byte header, then exactly LEN bytes of data.
 * The first bulk read asks for a whole frame because the firmware may hand
 * back the header together with the data; short reads are topped up in a
 * loop. */
static int ch347_usb_recv(struct CH347_device *dev, u8 cmd, u8 *data,
			  unsigned int cap, unsigned int *got)
{
	unsigned int total;
	unsigned int off;
	int actual;
	int rc;

	rc = usb_bulk_msg(dev->usb_dev, usb_rcvbulkpipe(dev->usb_dev, dev->ep_in),
			  dev->spi_rx_frame, CH347_PACKET_SIZE, &actual,
			  DEFAULT_TIMEOUT);
	if (rc)
		return rc;

	dev_info(&dev->master->dev,
		 "RX cmd=0x%02x got=%d hdr=%02x %02x %02x t=%lld\n", cmd,
		 actual, dev->spi_rx_frame[0], dev->spi_rx_frame[1],
		 dev->spi_rx_frame[2], ktime_get_ns());

	if (actual < 3)
		return -EIO;
	if (dev->spi_rx_frame[0] != cmd)
		return -EPROTO;

	total = (u16)dev->spi_rx_frame[1] | ((u16)dev->spi_rx_frame[2] << 8);
	if (got)
		*got = total;

	/* Anything received beyond the declared length is already out of the
	 * FIFO — just drop it. */
	off = min((unsigned int)(actual - 3), total);

	if (data && total <= cap) {
		if (off)
			memcpy(data, &dev->spi_rx_frame[3], off);

		while (off < total) {
			unsigned int want = min(total - off,
						(unsigned int)CH347_PACKET_SIZE);

			rc = usb_bulk_msg(dev->usb_dev,
					  usb_rcvbulkpipe(dev->usb_dev, dev->ep_in),
					  dev->spi_rx_frame, want, &actual,
					  DEFAULT_TIMEOUT);
			if (rc)
				return rc;
			if (!actual)
				return -EIO;

			actual = min((unsigned int)actual, want);
			memcpy(data + off, dev->spi_rx_frame, actual);
			off += actual;
		}

		return 0;
	}

	/* Either the data is not wanted or the caller's buffer is smaller
	 * than the frame: drain the remainder so the FIFO stays in sync. */
	if (total > off) {
		rc = ch347_usb_drain(dev, total - off);
		if (rc)
			return rc;
	}

	return (data && total > cap) ? -EMSGSIZE : 0;
}

/* Sends the configuration if it differs from the one already applied.
 * Returns with usb_lock held. */
static int ch347_spi_configure(struct CH347_device *dev, unsigned int hz,
			       u8 bpw, u32 mode, bool lsb_first)
{
	unsigned int divisor;
	u8 spi_mode = mode & (SPI_CPOL | SPI_CPHA);
	u8 cfg[26];
	int actual;
	int rc;

	if (bpw != 8 && bpw != 16)
		return -EINVAL;

	divisor = ch347_spi_divisor(hz);
	ch347_spi_build_cfg(cfg, divisor, bpw, mode, lsb_first);

	if (dev->spi_cfg_valid && !memcmp(cfg, dev->spi_cfg, sizeof(cfg)))
		return 0;

	rc = ch347_usb_send(dev, CH347_CMD_SPI_SET_CFG, cfg, sizeof(cfg));
	if (rc)
		return rc;

	/* Read more than needed: some firmware echoes the whole config back,
	 * and bytes left in the FIFO would break the next command. */
	rc = usb_bulk_msg(dev->usb_dev, usb_rcvbulkpipe(dev->usb_dev, dev->ep_in),
			  dev->spi_rx_frame, CH347_PACKET_SIZE, &actual,
			  DEFAULT_TIMEOUT);
	if (rc)
		return rc;

	if (actual < 4 || dev->spi_rx_frame[0] != CH347_CMD_SPI_SET_CFG ||
	    dev->spi_rx_frame[3] != 0) {
		u8 ack_cmd = actual > 0 ? dev->spi_rx_frame[0] : 0;
		u8 ack_status = actual > 3 ? dev->spi_rx_frame[3] : 0xff;

		dev_err(&dev->master->dev,
			"SPI config rejected: %d bytes, cmd 0x%02x, status 0x%02x\n",
			actual, ack_cmd, ack_status);
		return -EIO;
	}

	memcpy(dev->spi_cfg, cfg, sizeof(cfg));
	dev->spi_cfg_valid = true;

	dev_info(&dev->master->dev,
		 "SPI config ACK: %u Hz (divisor %u), %u bit, mode %u%s\n",
		 ch347_spi_speeds[divisor], divisor, bpw, spi_mode,
		 lsb_first ? ", LSB first" : "");

	return 0;
}

/* CS lives in a separate 0xC1 command: byte 3 of the packet is SCS0,
 * byte 8 is SCS1, that is payload[0] and payload[5]. The command has no
 * response. */
static int ch347_spi_cs(struct CH347_device *dev, unsigned int cs, bool assert)
{
	u8 payload[10] = { 0 };
	u8 value = (assert ? CH347_CS_ASSERT : CH347_CS_DEASSERT) |
		   CH347_CS_CHANGE;

	if (cs >= CH347_SPI_MAX_NUM_DEVICES)
		return -EINVAL;

	if (cs == 0)
		payload[0] = value;
	else
		payload[5] = value;

	return ch347_usb_send(dev, CH347_CMD_SPI_CS_CTRL, payload,
			      sizeof(payload));
}

/* Send a message */
static int ch347_spi_transfer_one_message(struct spi_controller *ctlr,
					  struct spi_message *m)
{
	struct CH347_device *dev = spi_controller_get_devdata(ctlr);
	struct spi_device *spi = m->spi;
	struct spi_transfer *xfer;
	unsigned int cs = spi_get_chipselect(spi, 0);
	bool lsb_first = spi->mode & SPI_LSB_FIRST;
	bool cs_asserted = false;
	int status;

	m->actual_length = 0;
	mutex_lock(&dev->usb_lock);

	/* Configure from the first transfer and only then lower CS: another
	 * 0xC0 after CS low resets the firmware state, and the data is
	 * clocked out after CS high. */
	xfer = list_first_entry_or_null(&m->transfers, struct spi_transfer,
					transfer_list);
	if (xfer) {
		u8 bpw = xfer->bits_per_word ? : spi->bits_per_word;
		u32 speed = xfer->speed_hz ? : spi->max_speed_hz;

		status = ch347_spi_configure(dev, speed, bpw, spi->mode,
					     lsb_first);
	} else {
		status = ch347_spi_configure(dev, spi->max_speed_hz,
					     spi->bits_per_word, spi->mode,
					     lsb_first);
	}
	if (!status) {
		status = ch347_spi_cs(dev, cs, true);
		cs_asserted = !status;
	}

	/* 0xC0 after CS low resets the firmware's SPI state: the data goes
	 * out after CS high. The configuration is therefore set only before
	 * CS low, and all transfers of the message run with the parameters
	 * of the first one. */
	list_for_each_entry(xfer, &m->transfers, transfer_list) {
		unsigned int chunk_max;
		unsigned int done = 0;
		u8 bpw;
		u32 speed;

		if (status)
			break;

		bpw = xfer->bits_per_word ? : spi->bits_per_word;
		speed = xfer->speed_hz ? : spi->max_speed_hz;

		/* Half of a 16-bit word must not land in the neighbouring
		 * packet: otherwise the firmware assembles the word from
		 * foreign bytes. */
		chunk_max = bpw == 16 ? CH347_MAX_DATA_LEN & ~1U
				      : CH347_MAX_DATA_LEN;

		while (done < xfer->len) {
			unsigned int chunk = min(xfer->len - done, chunk_max);
			unsigned int got;

			status = ch347_usb_send(dev, CH347_CMD_SPI_OUT_IN,
						xfer->tx_buf ?
							(const u8 *)xfer->tx_buf + done :
							NULL,
						chunk);
			if (status)
				break;

			status = ch347_usb_recv(dev, CH347_CMD_SPI_OUT_IN,
						xfer->rx_buf ?
							(u8 *)xfer->rx_buf + done :
							NULL,
						chunk, &got);
			if (status)
				break;

			if (got != chunk) {
				status = -EIO;
				break;
			}

			done += chunk;
		}

		if (status)
			break;

		m->actual_length += xfer->len;
	}

	/* Always release CS, including after an error: otherwise the bus
	 * stays busy for every other device on the controller. */
	if (cs_asserted) {
		int rc = ch347_spi_cs(dev, cs, false);

		if (!status)
			status = rc;
	}

	mutex_unlock(&dev->usb_lock);

	m->status = status;
	spi_finalize_current_message(ctlr);

	return 0;
}

/* Add a new device, defined by the board_info. */
static int add_slave(struct CH347_device *dev, struct spi_board_info *board_info)
{
	unsigned int cs = board_info->chip_select;
	struct spi_client *client;
	int rc;

	/* Sanity check */
	if (cs >= dev->master->num_chipselect)
		return -EINVAL;

	board_info->bus_num = dev->master->bus_num;

	mutex_lock(&dev->spi_lock);

	if (dev->cs_allocated & BIT(cs)) {
		rc = -EADDRINUSE;
		goto unlock;
	}

	client = &dev->spi_clients[cs];
	client->slave = spi_new_device(dev->master, board_info);
	if (!client->slave) {
		rc = -ENOMEM;
		goto unlock;
	}

	dev->cs_allocated |= BIT(cs);
	rc = 0;

unlock:
	mutex_unlock(&dev->spi_lock);

	return rc;
}

static int remove_slave(struct CH347_device *dev, unsigned int cs)
{
	struct spi_client *client;
	int rc;

	if (cs >= CH347_SPI_MAX_NUM_DEVICES)
		return -EINVAL;

	mutex_lock(&dev->spi_lock);

	client = &dev->spi_clients[cs];
	if (!(dev->cs_allocated & BIT(cs))) {
		rc = -ENODEV;
		goto unlock;
	}

	dev->cs_allocated &= ~BIT(cs);
	spi_unregister_device(client->slave);
	client->slave = NULL;
	rc = 0;

unlock:
	mutex_unlock(&dev->spi_lock);

	return rc;
}

/* sysfs entry to add a new device. It takes a string with 2
 * parameters: the modalias and the chip select number. For instance
 * "spi-nor 0" or "spidev 1".
 */
static ssize_t new_device_store(struct device *mdev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	struct spi_controller *master = container_of(mdev, struct spi_controller, dev);
	struct CH347_device *dev = spi_controller_get_devdata(master);
	struct spi_board_info board_info = {
		.mode = SPI_MODE_0,
		.max_speed_hz = CH347_SPI_MAX_FREQ,
	};
	char *req_org;
	char *req;
	char *str;
	int rc;

	req_org = kstrdup(buf, GFP_KERNEL);
	if (!req_org)
		return -ENOMEM;

	req = req_org;

	str = strsep(&req, " ");
	if (str == NULL) {
		rc = -EINVAL;
		goto free_req;
	}

	rc = strscpy(board_info.modalias, str, sizeof(board_info.modalias));
	if (rc < 0)
		goto free_req;

	str = strsep(&req, " ");
	if (str == NULL) {
		rc = -EINVAL;
		goto free_req;
	}
	rc = kstrtou16(str, 0, &board_info.chip_select);
	if (rc)
		goto free_req;

	rc = add_slave(dev, &board_info);
	if (rc)
		goto free_req;

	kfree(req_org);

	return count;

free_req:
	kfree(req_org);

	return rc;
}

/* sysfs entry to remove an existing device at a given chip select. It
 * takes a string with a single number. For instance "2".
 */
static ssize_t delete_device_store(struct device *mdev,
				   struct device_attribute *attr,
				   const char *buf, size_t count)
{
	struct spi_controller *master = container_of(mdev, struct spi_controller, dev);
	struct CH347_device *dev = spi_controller_get_devdata(master);
	int cs;
	int rc;

	rc = kstrtouint(buf, 0, &cs);
	if (rc)
		return rc;

	rc = remove_slave(dev, cs);
	if (rc)
		return rc;

	return count;
}

static DEVICE_ATTR_WO(new_device);
static DEVICE_ATTR_WO(delete_device);

void CH347_spi_remove(struct CH347_device *dev)
{
	int cs;

	if (dev->master == NULL)
		return;

	device_remove_file(&dev->master->dev, &dev_attr_new_device);
	device_remove_file(&dev->master->dev, &dev_attr_delete_device);

	for (cs = 0; cs < ARRAY_SIZE(dev->spi_clients); cs++)
		remove_slave(dev, cs);

	spi_unregister_controller(dev->master);
}

int CH347_spi_init(struct CH347_device *dev)
{
	struct spi_controller *master;
	int rc;

	dev->master = spi_alloc_host(&dev->iface->dev, 0);
	if (!dev->master)
		return -ENOMEM;

	master = dev->master;
	spi_controller_set_devdata(master, dev);
	mutex_init(&dev->spi_lock);

	master->bus_num = -1;
	master->num_chipselect = CH347_SPI_MAX_NUM_DEVICES;
	master->mode_bits = SPI_MODE_0 | SPI_MODE_1 | SPI_MODE_2 |
			    SPI_MODE_3 | SPI_LSB_FIRST;
	master->flags = SPI_CONTROLLER_MUST_RX | SPI_CONTROLLER_MUST_TX;
	master->bits_per_word_mask = SPI_BPW_MASK(8) | SPI_BPW_MASK(16);
	master->transfer_one_message = ch347_spi_transfer_one_message;
	master->max_speed_hz = CH347_SPI_MAX_FREQ;
	master->min_speed_hz = CH347_SPI_MIN_FREQ;

	/* Until 0xC0 is acknowledged the chip answers no SPI command at all,
	 * so check the link right away rather than on the first transfer. */
	mutex_lock(&dev->usb_lock);
	rc = ch347_spi_configure(dev, CH347_SPI_DEFAULT_FREQ, 8, SPI_MODE_0,
				 false);
	mutex_unlock(&dev->usb_lock);
	if (rc) {
		dev_err(&master->dev, "SPI interface setup failed: %d\n", rc);
		goto put_master;
	}

	rc = spi_register_controller(master);
	if (rc)
		goto put_master;

	rc = device_create_file(&master->dev, &dev_attr_new_device);
	if (rc) {
		dev_err(&master->dev, "Cannot create new_device file\n");
		goto unreg_master;
	}

	rc = device_create_file(&master->dev, &dev_attr_delete_device);
	if (rc) {
		dev_err(&master->dev, "Cannot create delete_device file\n");
		goto del_new_device;
	}

	return 0;

del_new_device:
	device_remove_file(&dev->master->dev, &dev_attr_new_device);

unreg_master:
	spi_unregister_controller(dev->master);
	dev->master = NULL;

	return rc;

put_master:
	spi_controller_put(master);
	dev->master = NULL;

	return rc;
}
