from fastapi import Request, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.models import User
from app.core.security import decode_access_token
from app.core.config import MAIN_ADMIN_USER_ID


def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    username = payload.get("sub")
    user = db.query(User).filter(User.username == username).first()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def get_main_admin(
    user: User = Depends(get_current_user)
) -> User:
    """Dependency to ensure the user is the main admin (by ID constant)."""
    # Main admin is identified ONLY by user ID matching the constant
    if user.id != MAIN_ADMIN_USER_ID:
        raise HTTPException(status_code=403, detail="Only main admin can perform this action")
    
    return user


def get_admin(
    user: User = Depends(get_current_user)
) -> User:
    """Dependency to ensure the user is either main admin or co-admin."""
    # Main admin check: user ID matches constant
    is_main_admin = user.id == MAIN_ADMIN_USER_ID
    
    # Co-admin check: role is "coadmin"
    is_co_admin = user.role == "coadmin"
    
    if not (is_main_admin or is_co_admin):
        raise HTTPException(status_code=403, detail="Admin access required")
    
    return user

