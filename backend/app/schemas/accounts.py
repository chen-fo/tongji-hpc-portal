from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Username = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{2,31}$")]
Password = Annotated[str, StringConstraints(min_length=12, max_length=128)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=128)


class PasswordChange(StrictModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: Password


class UserCreate(StrictModel):
    username: Username
    password: Password
    official_account_id: int = Field(gt=0)


class UserPatch(StrictModel):
    status: Literal["active", "disabled"] | None = None
    password: Password | None = None


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: str
    official_account_id: int | None
    remote_home: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class AccountInput(StrictModel):
    alias: Identifier
    ssh_host: str = Field(min_length=1, max_length=253, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]*$")
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_username: Identifier
    ssh_key_reference: Identifier
    enabled: bool = True


class AccountView(AccountInput):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
