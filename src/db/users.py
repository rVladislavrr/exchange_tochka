from .base import BaseManager
from src.models import Users


class UsersManager(BaseManager):
    model = Users


usersManager = UsersManager()
