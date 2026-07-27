#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--file" ]]; then
  tail -f logs/alphawave-taskd.log
else
  journalctl --user -u alphawave-taskd -f
fi
