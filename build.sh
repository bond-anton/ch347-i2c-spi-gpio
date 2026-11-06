#!/bin/bash
# Build the module and sign it with the local MOK key.
# Signing must happen after every build: `make` overwrites ch347-buses.ko.
set -euo pipefail

cd "$(dirname "$0")"

make

SIGN_FILE="/usr/src/kernels/$(uname -r)/scripts/sign-file"
if [ ! -x "$SIGN_FILE" ]; then
	echo "sign-file not found at $SIGN_FILE (install kernel-devel)" >&2
	exit 1
fi

"$SIGN_FILE" sha256 mok/mok.key mok/mok.pub ch347-buses.ko
modinfo ch347-buses.ko | grep -E '^(signer|sig_key):'
