from uuid import UUID

from src.models.users import RoleEnum


def custom_serializer_json(obj):
    if isinstance(obj, RoleEnum):
        return obj.value
    elif isinstance(obj, UUID):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")