from pydantic import BaseModel, Field


class AuthUser(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    role: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    user: AuthUser
    csrf_token: str


class SessionResponse(BaseModel):
    auth_enabled: bool
    authenticated: bool
    owner_exists: bool
    user: AuthUser | None = None
    csrf_token: str | None = None


class CsrfResponse(BaseModel):
    csrf_token: str | None = None
