#!/usr/bin/env bash
set -euo pipefail

systemctl --user stop alphawave-taskd
systemctl --user status alphawave-taskd --no-pager || true
