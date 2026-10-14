import hashlib
import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db_session
from .models import Device

bearer = HTTPBearer(auto_error=False)


def hash_device_credential(credential: str) -> str:
    pepper = get_settings().device_credential_pepper.get_secret_value().encode()
    return hmac.new(pepper, credential.encode(), hashlib.sha256).hexdigest()


async def authenticate_device(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(bearer)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Device:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="device authentication required")
    device = await session.scalar(select(Device).where(Device.device_id == credentials.credentials.split(".", 1)[0]))
    if device is None or device.status != "active" or not hmac.compare_digest(
        device.credential_hash,
        hash_device_credential(credentials.credentials),
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid device credentials")
    return device
