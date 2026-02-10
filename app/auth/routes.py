from fastapi import APIRouter, Depends, HTTPException, Form
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.auth.models import User
from app.core.security import hash_password, verify_password, create_access_token
from app.core.config import MAIN_ADMIN_USER_ID

router = APIRouter(prefix="/auth", tags=["auth"])


# =========================
# SIGNUP
# =========================
@router.post("/signup")
def signup(
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    user = User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        is_verified=True,  # TEMP
        role="user",  # All signups are normal users by default
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return {"message": "Signup successful"}


# =========================
# LOGIN
# =========================
@router.post("/login")
def login(
    email_or_username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    print("[AUTH] /auth/login called", flush=True)
    # Try to find user by email first, then by username
    user = db.query(User).filter(User.email == email_or_username).first()
    
    if not user:
        # If not found by email, try username
        user = db.query(User).filter(User.username == email_or_username).first()

    if not user or not verify_password(password, user.password_hash):
        print("[AUTH] Invalid credentials for:", email_or_username, flush=True)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": user.username})
    print("[AUTH] Login successful for:", user.username, flush=True)
    return {"access_token": token}


# ------------------------------------------------------------------
# Safe redirects for accidental browser hits on API endpoints
# ------------------------------------------------------------------

@router.get("/login", include_in_schema=False)
def login_get_redirect():
    """
    If a browser is accidentally sent to GET /auth/login,
    redirect it to the real HTML login page instead of 405.
    """
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/login", status_code=303)


@router.get("/signup", include_in_schema=False)
def signup_get_redirect():
    """
    If a browser is accidentally sent to GET /auth/signup,
    redirect it to the real HTML signup page instead of 405.
    """
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/signup", status_code=303)
