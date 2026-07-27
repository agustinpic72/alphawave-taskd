#!/usr/bin/env bash
set -euo pipefail

systemctl --user restart alphawave-taskd
systemctl --user status alphawave-taskd --no-pager
