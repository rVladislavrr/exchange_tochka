from typing import Literal

from pydantic import BaseModel, Field, UUID4


ONLY_LETTERS_ONE_WORD = r'^[a-zA-ZА-Яа-я]+$'


class UserBase(BaseModel):
    name: str = Field("name", pattern=ONLY_LETTERS_ONE_WORD, examples=['string'], min_length=1, max_length=100)

class UserRequest(UserBase):
    uuid: UUID4
    role: Literal["admin", "user"]

class ProtectedRout(BaseModel):
    user: UserRequest