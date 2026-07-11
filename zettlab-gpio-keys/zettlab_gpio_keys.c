// SPDX-License-Identifier: GPL-2.0
/*
 * zettlab_gpio_keys - Chassis COPY / RESET keys for Zettlab D6/D8 Ultra
 *
 * Exposes the chassis buttons as a standard Linux input device so userspace
 * can bind custom actions under Ubuntu.
 *
 * Hardware:
 *   Intel Meteor Lake pinctrl community INTC1083 @ 0xE0D20000
 *   Pad CFG0 +0x6C0  -> KEY_1  (COPY, front, active-low RX bit1)
 *   Pad CFG0 +0x6B0  -> KEY_2  (RESET, rear, active-low RX bit1)
 *
 * Match the device by name "zettlab-gpio-keys" (eventN is not stable).
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/input.h>
#include <linux/timer.h>
#include <linux/slab.h>
#include <linux/platform_device.h>

#define DRV_NAME		"zettlab-gpio-keys"
#define MMIO_BASE		0xE0D20000UL
#define MMIO_SIZE		0xF000UL
#define OFF_COPY		0x6C0	/* KEY_1 */
#define OFF_RESET		0x6B0	/* KEY_2 */
#define POLL_MS			20

/* Intel pad CFG0 RX state is bit 1; pressed when that bit is clear (active-low) */
#define RX_PRESSED(v)		((((v) >> 1) & 1) == 0)

struct zettlab_keys {
	void __iomem *base;
	struct input_dev *input;
	struct timer_list timer;
	bool copy_down;
	bool reset_down;
};

static u32 pad_read(struct zettlab_keys *k, unsigned int off)
{
	return ioread32(k->base + off);
}

static void zettlab_keys_poll(struct timer_list *t)
{
	struct zettlab_keys *k = timer_container_of(k, t, timer);
	bool copy, reset;

	copy = RX_PRESSED(pad_read(k, OFF_COPY));
	reset = RX_PRESSED(pad_read(k, OFF_RESET));

	if (copy != k->copy_down) {
		k->copy_down = copy;
		input_report_key(k->input, KEY_1, copy);
		input_sync(k->input);
	}
	if (reset != k->reset_down) {
		k->reset_down = reset;
		input_report_key(k->input, KEY_2, reset);
		input_sync(k->input);
	}

	mod_timer(&k->timer, jiffies + msecs_to_jiffies(POLL_MS));
}

static int zettlab_keys_probe(struct platform_device *pdev)
{
	struct zettlab_keys *k;
	struct input_dev *input;
	int err;

	pr_info(DRV_NAME ": probe\n");

	k = devm_kzalloc(&pdev->dev, sizeof(*k), GFP_KERNEL);
	if (!k)
		return -ENOMEM;

	k->base = devm_ioremap(&pdev->dev, MMIO_BASE, MMIO_SIZE);
	if (!k->base) {
		pr_err(DRV_NAME ": ioremap 0x%lx failed\n", MMIO_BASE);
		return -ENOMEM;
	}

	input = devm_input_allocate_device(&pdev->dev);
	if (!input)
		return -ENOMEM;

	input->name = DRV_NAME;
	input->phys = DRV_NAME "/input0";
	input->id.bustype = BUS_HOST;
	input->id.vendor = 0x0001;
	input->id.product = 0x0001;
	input->id.version = 0x0100;

	__set_bit(EV_KEY, input->evbit);
	__set_bit(EV_REP, input->evbit);
	__set_bit(KEY_1, input->keybit);	/* COPY */
	__set_bit(KEY_2, input->keybit);	/* RESET */

	err = input_register_device(input);
	if (err) {
		pr_err(DRV_NAME ": failed to register input device\n");
		return err;
	}

	k->input = input;
	/* Seed state so the first poll does not synthesize a press edge */
	k->copy_down = RX_PRESSED(pad_read(k, OFF_COPY));
	k->reset_down = RX_PRESSED(pad_read(k, OFF_RESET));

	timer_setup(&k->timer, zettlab_keys_poll, 0);
	mod_timer(&k->timer, jiffies + msecs_to_jiffies(POLL_MS));

	platform_set_drvdata(pdev, k);
	pr_info(DRV_NAME ": registered (COPY=KEY_1, RESET=KEY_2)\n");
	return 0;
}

static void zettlab_keys_remove(struct platform_device *pdev)
{
	struct zettlab_keys *k = platform_get_drvdata(pdev);

	timer_delete_sync(&k->timer);
}

static struct platform_driver zettlab_keys_driver = {
	.probe = zettlab_keys_probe,
	.remove = zettlab_keys_remove,
	.driver = {
		.name = DRV_NAME,
	},
};

static struct platform_device *zettlab_keys_pdev;

static int __init zettlab_keys_init(void)
{
	int err;

	err = platform_driver_register(&zettlab_keys_driver);
	if (err)
		return err;

	zettlab_keys_pdev = platform_device_register_simple(DRV_NAME, -1, NULL, 0);
	if (IS_ERR(zettlab_keys_pdev)) {
		platform_driver_unregister(&zettlab_keys_driver);
		return PTR_ERR(zettlab_keys_pdev);
	}
	return 0;
}

static void __exit zettlab_keys_exit(void)
{
	platform_device_unregister(zettlab_keys_pdev);
	platform_driver_unregister(&zettlab_keys_driver);
}

module_init(zettlab_keys_init);
module_exit(zettlab_keys_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Zettlab Ubuntu community");
MODULE_DESCRIPTION("Zettlab D6/D8 Ultra chassis COPY/RESET keys");
MODULE_VERSION("1.0");
