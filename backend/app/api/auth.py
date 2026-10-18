"""Authentication dependencies use a separate read session from business transactions."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.schemas import InputModel
from app.domain.errors import RuleViolation
from app.persistence.database import get_session, session_factory
from app.services.auth import Principal, authenticate_token, login

bearer = HTTPBearer(auto_error=False)


def get_auth_session():
    with session_factory()() as session:
        yield session


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: Annotated[Session, Depends(get_auth_session)],
) -> Principal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise RuleViolation("UNAUTHENTICATED", "Se requiere un token Bearer válido", 401)
    try:
        return authenticate_token(session, credentials.credentials)
    finally:
        # Principal is detached data. Release this connection before a business use case
        # acquires its session, avoiding pool starvation at concurrent request startup.
        session.close()


def administrator(user: Annotated[Principal, Depends(current_user)]) -> Principal:
    if user.role != "ADMIN":
        raise RuleViolation("FORBIDDEN", "Esta operación requiere Administrador(a)", 403)
    return user


Actor = Annotated[Principal, Depends(current_user)]
Admin = Annotated[Principal, Depends(administrator)]
router = APIRouter(prefix="/auth", tags=["auth"])


class LoginCreate(InputModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_in: int


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    role: Literal["ADMIN", "WAREHOUSE"]


@router.post("/login", response_model=TokenResponse)
def login_user(payload: LoginCreate, session: Annotated[Session, Depends(get_session)]):
    return login(session, payload.email, payload.password)


@router.get("/me", response_model=UserResponse)
def me(user: Actor):
    return user
