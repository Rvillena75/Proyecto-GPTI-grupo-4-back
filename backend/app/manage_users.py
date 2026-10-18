"""Controlled operator CLI. Passwords are read with getpass, never command line arguments."""

import argparse
from datetime import UTC, datetime
from getpass import getpass

from sqlalchemy import select

from app.persistence.database import session_factory
from app.persistence.models import User
from app.services.auth import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=["ADMIN", "WAREHOUSE"], required=True)
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    email, name = args.email.strip().casefold(), args.name.strip()
    if "@" not in email or len(email) > 254 or not name or len(name) > 200:
        parser.error("Valid email and nonempty name required")
    password_hash = None
    if not args.disable:
        password = getpass("New password (12-72 UTF-8 bytes): ")
        if password != getpass("Repeat password: "):
            parser.error("Passwords do not match")
        password_hash = hash_password(password)
    with session_factory()() as session, session.begin():
        user = session.scalar(select(User).where(User.email == email).with_for_update())
        if user is None:
            if args.disable:
                parser.error("Unknown user")
            user = User(email=email, created_at=datetime.now(UTC))
            session.add(user)
        user.name, user.role, user.is_active = name, args.role, not args.disable
        if password_hash:
            user.password_hash = password_hash
    print("User configuration saved; no credentials printed.")


if __name__ == "__main__":
    main()
