from datetime import datetime, timezone

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
    # Debug logging for auth issues
    cookie_names = list(request.cookies.keys())
    auth_header = request.headers.get("authorization")
    print(f"[AUTH DEBUG] path={request.url.path} has_cookie={'access_token' in cookie_names} cookie_names={cookie_names} auth_header_present={auth_header is not None}", flush=True)
    
    token = request.cookies.get("access_token")

    if not token:
        print(f"[AUTH DEBUG] reject reason=missing_cookie path={request.url.path}", flush=True)
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Support both "Bearer <token>" and raw token values for backward compatibility.
    if isinstance(token, str) and token.lower().startswith("bearer "):
        token = token[7:].strip()

    payload = decode_access_token(token)
    if not payload:
        print(f"[AUTH DEBUG] reject reason=invalid_token path={request.url.path}", flush=True)
        raise HTTPException(status_code=401, detail="Invalid token")

    username = payload.get("sub")
    if not username:
        print(f"[AUTH DEBUG] reject reason=no_username_in_token path={request.url.path}", flush=True)
        raise HTTPException(status_code=401, detail="Invalid token payload")
    
    user = db.query(User).filter(User.username == username).first()

    if not user:
        print(f"[AUTH DEBUG] reject reason=user_not_found username={username} path={request.url.path}", flush=True)
        raise HTTPException(status_code=401, detail="User not found")

    print(f"[AUTH DEBUG] auth_success username={username} path={request.url.path}", flush=True)
    
    # Update last_active timestamp so admins can see who is online
    try:
        user.last_active = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()

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

