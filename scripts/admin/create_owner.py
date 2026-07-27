#!/usr/bin/env python3
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.services import auth as auth_service  # noqa: E402


def main() -> int:
    init_db()
    email = os.getenv("ALPHAWAVE_OWNER_EMAIL") or input("Owner email: ").strip()
    password = os.getenv("ALPHAWAVE_OWNER_PASSWORD")
    if password is None:
        password = getpass.getpass("Owner password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords do not match.", file=sys.stderr)
            return 2
    display_name = os.getenv("ALPHAWAVE_OWNER_DISPLAY_NAME") or None
    allow_existing = os.getenv("ALPHAWAVE_OWNER_ALLOW_EXISTING") == "1"
    with SessionLocal() as db:
        try:
            user = auth_service.create_owner(
                db,
                email=email,
                password=password,
                display_name=display_name,
                allow_existing=allow_existing,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    print(f"Owner created: {user.email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
