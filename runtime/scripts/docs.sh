#!/bin/sh
# Changed by Assieme: renamed and adapted from herdr-core v0.1.19; see NOTICE.
# Render CLI help and check human examples using the binary built by Cargo.
set -eu
case "${1:---check}" in
  --write) export ASM_UPDATE_DOCS=1 ;;
  --check) unset ASM_UPDATE_DOCS ;;
  *) echo 'usage: sh scripts/docs.sh [--write|--check]' >&2; exit 2 ;;
esac
cargo test --locked -p assieme-core --test cli_docs
