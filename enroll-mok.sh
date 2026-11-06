#!/bin/bash
# Enroll the local module-signing key with the kernel's MOK list.
# Run once, then reboot and confirm in the blue MOK Manager screen.
set -euo pipefail

cd "$(dirname "$0")"

# mokutil --import requires DER, not PEM. Regenerate from the PEM cert so the
# script stays correct even if mok.der is missing.
if [ ! -f mok/mok.pub ]; then
	echo "mok/mok.pub missing" >&2
	exit 1
fi
openssl x509 -in mok/mok.pub -outform DER -out mok/mok.der

sudo mkdir -p /var/lib/dkms
sudo cp mok/mok.key mok/mok.pub /var/lib/dkms/
sudo chmod 600 /var/lib/dkms/mok.key

# mokutil asks for a one-time password; you re-enter it in MOK Manager after reboot.
sudo mokutil --import mok/mok.der

echo
echo "Key queued for enrollment. Reboot, then in MOK Manager:"
echo "  Enroll MOK -> Continue -> Yes -> <password> -> Reboot"
