#!/bin/sh
# LaunchAgent entrypoint: run the publisher poll loop with the right PATH + env.
# This file is machine-specific and gitignored.
cd ~/sm-th/zulip-publisher || exit 1
export PATH="~/.nix-profile/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin:/usr/sbin:/sbin"
exec nix develop "path:$PWD" -c \
  secretspec run -- python -m zulip_publisher run
