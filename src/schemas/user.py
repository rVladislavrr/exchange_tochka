from typing import Literal

from pydantic import BaseModel, Field, UUID4, field_validator

ONLY_LETTERS_ONE_WORD = r'^[a-zA-ZА-Яа-я]+$'


class UserBase(BaseModel):
    name: str = Field("name", pattern=ONLY_LETTERS_ONE_WORD, examples=['string'], min_length=3, max_length=100)


class UserRequest(UserBase):
    id: UUID4 = Field(..., alias="id", validation_alias="uuid", serialization_alias='id')
    role: Literal["ADMIN", "USER"]

    @field_validator("role", mode="before")
    @classmethod
    def convert_enum_to_str(cls, value):
        if not isinstance(value, str):
            return value.value
        return value

    class Config:
        from_attributes = True
        populate_by_name = True


class ProtectedRout(BaseModel):
    user: UserRequest


class UserRegister(UserRequest):
    api_key: str


class UserRedis(UserRequest):
    is_active: bool
