"""Own short-lived JWTs, bcrypt and database-backed identity and login throttling."""

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.errors import RuleViolation
from app.persistence.models import LoginThrottle, User

ISSUER = "stocksmart-api"
AUDIENCE = "stocksmart"


def signing_key() -> str:
    value = os.getenv("STOCKSMART_JWT_SECRET", "")
    if len(value.encode()) < 32:
        raise RuntimeError("STOCKSMART_JWT_SECRET requires at least 32 random bytes")
    return value


def token_minutes() -> int:
    try:
        value = int(os.getenv("STOCKSMART_JWT_TTL_MINUTES", "30"))
    except ValueError:
        raise RuntimeError("STOCKSMART_JWT_TTL_MINUTES must be between 1 and 60") from None
    if not 1 <= value <= 60:
        raise RuntimeError("STOCKSMART_JWT_TTL_MINUTES must be between 1 and 60")
    return value


def hash_password(password: str) -> str:
    if not 12 <= len(password.encode("utf-8")) <= 72:
        raise RuleViolation("INVALID_PASSWORD", "Use between 12 and 72 UTF-8 bytes", 422)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


@lru_cache(maxsize=1)
def _dummy_hash() -> bytes:
    # Unknown users incur the same bcrypt cost; no usable default account is created.
    return bcrypt.hashpw(os.urandom(32).hex().encode(), bcrypt.gensalt(rounds=12))


def verify_password(password: str, stored: str | None) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        return False
    try:
        return bcrypt.checkpw(raw, stored.encode() if stored else _dummy_hash())
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Principal:
    id: int
    name: str
    email: str
    role: str


def principal(user: User) -> Principal:
    return Principal(user.id, user.name, user.email, user.role)


def issue_token(user: User, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user.id),
            "iat": now,
            "nbf": now,
            "exp": now + timedelta(minutes=token_minutes()),
            "iss": ISSUER,
            "aud": AUDIENCE,
        },
        signing_key(),
        algorithm="HS256",
    )


def authenticate_token(session: Session, token: str) -> Principal:
    try:
        claims = jwt.decode(
            token,
            signing_key(),
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["sub", "iat", "nbf", "exp", "iss", "aud"]},
        )
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject.isascii() or not subject.isdigit():
            raise ValueError("Invalid subject")
        user_id = int(subject)
        if not 0 < user_id <= 9_223_372_036_854_775_807:
            raise ValueError("Invalid subject")
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise RuleViolation("UNAUTHENTICATED", "Credenciales no válidas o vencidas", 401) from exc
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise RuleViolation("UNAUTHENTICATED", "Credenciales no válidas o vencidas", 401)
    # Never trust a role sent by the client or copied into an old JWT.
    return principal(user)


def login(session: Session, email: str, password: str) -> dict:
    now = datetime.now(UTC)
    email = email.strip().casefold()
    key = hashlib.sha256(email.encode()).hexdigest()
    throttled, authenticated = False, None
    with session.begin():
        session.execute(
            insert(LoginThrottle)
            .values(key=key, attempts=0, window_start=now)
            .on_conflict_do_nothing()
        )
        limit = session.scalar(
            select(LoginThrottle)
            .where(LoginThrottle.key == key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if now - limit.window_start >= timedelta(minutes=15):
            limit.attempts, limit.window_start = 0, now
        if limit.attempts >= 6:
            throttled = True
        else:
            user = session.scalar(select(User).where(User.email == email))
            valid = verify_password(password, user.password_hash if user else None)
            if valid and user and user.is_active:
                authenticated = user
                limit.attempts = 0
            else:
                limit.attempts += 1
        # Bounded housekeeping; no plaintext credentials or email in throttle records.
        session.query(LoginThrottle).filter(
            LoginThrottle.window_start < now - timedelta(days=1), LoginThrottle.key != key
        ).delete(synchronize_session=False)
    if throttled:
        raise RuleViolation("LOGIN_THROTTLED", "Demasiados intentos; espere 15 minutos", 429)
    if authenticated is None:
        raise RuleViolation("UNAUTHENTICATED", "Credenciales no válidas o vencidas", 401)
    return {
        "access_token": issue_token(authenticated),
        "token_type": "bearer",
        "expires_in": token_minutes() * 60,
    }
