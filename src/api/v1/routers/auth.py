import json
from fastapi import APIRouter, status, Depends, Request, Security, HTTPException
from fastapi.security import APIKeyHeader

from src.schemas.user import ProtectedRout

router = APIRouter(tags=["Auth"])
api_key_header = APIKeyHeader(name="Authorization", auto_error=False, description=r"Форма записи TOKEN \<token\>")

async def for_documentation(api_key: str = Security(api_key_header)):
    pass


@router.get("/protected_rout", status_code=status.HTTP_200_OK, dependencies=[Depends(for_documentation)])
async def protected_rout(request: Request) -> ProtectedRout:
    user = getattr(request.state, "user", None)
    if user:
        return ProtectedRout(**{"user": json.loads(user)})
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

