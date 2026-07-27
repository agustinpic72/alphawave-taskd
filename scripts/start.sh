#!/usr/bin/env bash
set -euo pipefail

systemctl --user start alphawave-taskd
systemctl --user status alphawave-taskd --no-pager
