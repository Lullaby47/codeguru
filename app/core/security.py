from datetime import datetime, timedelta
from typing import Optional
import hashlib
import hmac
import os
from jose import jwt, JWTError

# ======================
# PASSWORD HASHING (PBKDF2)
# ======================

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        100_000,
    )
    return salt.hex() + ":" + pwd_hash.hex()


def verify_password(password: str, stored: str) -> bool:
    salt_hex, hash_hex = stored.split(":")
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)

    pwd_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        100_000,
    )
    return hmac.compare_digest(pwd_hash, expected)


# ======================
# JWT
# ======================

SECRET_KEY = os.getenv("SECRET_KEY", os.getenv("JWT_SECRET_KEY", ""))
if not SECRET_KEY:
    # In production, require a secret; for local dev, use a default (UNSAFE for prod)
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("ENVIRONMENT", "").lower() == "production":
        raise RuntimeError("SECRET_KEY or JWT_SECRET_KEY env var is required in production")
    SECRET_KEY = "dev-secret-key-CHANGE-IN-PRODUCTION-12345678901234567890"
    print("[AUTH] WARNING: Using default SECRET_KEY for development. DO NOT USE IN PRODUCTION!", flush=True)
else:
    print(f"[AUTH] SECRET_KEY present: True (length={len(SECRET_KEY)})", flush=True)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        print("[AUTH DEBUG] Token expired", flush=True)
        return None
    except JWTError as e:
        print(f"[AUTH DEBUG] JWT decode error: {type(e).__name__}", flush=True)
        return None
