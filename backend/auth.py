"""
Role-based authentication for the Sentinel AI backend.

Three roles:
  developer  — full access: license management, user management, plus everything below.
  tech       — school tech team: zones, alert rules, school hours, holiday calendar, video ingest.
  principal  — view access: live cameras, notifications, alerts, snapshots, reports, search.

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes in the shared
events database (users table). Login issues an opaque bearer token kept in
server memory (tokens expire after TOKEN_TTL_SECONDS or on server restart).
"""

import hashlib
import os
import secrets
import time
from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from db_schema import adapt_query, connect_db, get_db_type

ROLES = ("developer", "tech", "principal")
TOKEN_TTL_SECONDS = 12 * 3600
_PBKDF2_ITERATIONS = 200_000

# token -> {"username", "role", "expires"}
_active_tokens: Dict[str, Dict] = {}

_failed_attempts: Dict[str, Dict] = {}
LOCKOUT_DURATION = 300
MAX_ATTEMPTS = 5

_bearer_scheme = HTTPBearer(auto_error=False)


def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    expected = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ITERATIONS
    )
    return secrets.compare_digest(expected.hex(), digest_hex)


def init_users_db() -> None:
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        pk = (
            "id SERIAL PRIMARY KEY"
            if get_db_type() == "postgres"
            else "id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS users (
                {pk},
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS active_tokens (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                expires REAL NOT NULL
            )
            """
        )
        conn.commit()

        cursor.execute("SELECT COUNT(*) FROM users")
        if cursor.fetchone()[0] == 0:
            # Seed default accounts; override via env vars, change before any real deployment.
            defaults = [
                ("developer", os.environ.get("SENTINEL_DEV_PASSWORD", "dev@sentinel"), "developer"),
                ("techteam", os.environ.get("SENTINEL_TECH_PASSWORD", "tech@sentinel"), "tech"),
                ("principal", os.environ.get("SENTINEL_PRINCIPAL_PASSWORD", "principal@sentinel"), "principal"),
            ]
            for username, password, role in defaults:
                cursor.execute(
                    adapt_query(
                        "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)"
                    ),
                    (username, _hash_password(password), role, time.strftime("%Y-%m-%dT%H:%M:%S")),
                )
            conn.commit()
            print(
                "[AUTH] Seeded default users: developer / techteam / principal "
                "(passwords from SENTINEL_*_PASSWORD env vars or built-in defaults — change them!)"
            )


def authenticate(username: str, password: str) -> Optional[Dict]:
    now = time.time()
    
    # Prune expired lockouts to prevent memory leak from random usernames
    expired_keys = [k for k, v in _failed_attempts.items() if v.get("lockout_until", 0) <= now]
    for k in expired_keys:
        del _failed_attempts[k]
        
    record = _failed_attempts.get(username)
    if record and record.get("lockout_until", 0) > now:
        return None
        
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            adapt_query("SELECT username, password_hash, role FROM users WHERE username = ?"),
            (username,),
        )
        row = cursor.fetchone()

    if row is None or not _verify_password(password, row[1]):
        if not record or record.get("lockout_until", 0) <= now:
            record = {"attempts": 0, "lockout_until": 0}
            _failed_attempts[username] = record
        record["attempts"] += 1
        if record["attempts"] >= MAX_ATTEMPTS:
            record["lockout_until"] = now + LOCKOUT_DURATION
        return None
        
    if username in _failed_attempts:
        del _failed_attempts[username]
        
    return {"username": row[0], "role": row[2]}


def create_token(username: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.time() + TOKEN_TTL_SECONDS
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            adapt_query("INSERT INTO active_tokens (token, username, role, expires) VALUES (?, ?, ?, ?)"),
            (token, username, role, expires),
        )
        conn.commit()
    return token


def revoke_token(token: str) -> None:
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            adapt_query("DELETE FROM active_tokens WHERE token = ?"),
            (token,),
        )
        conn.commit()


def _resolve_token(token: str) -> Optional[Dict]:
    with connect_db(validate_schema=False) as conn:
        cursor = conn.cursor()
        cursor.execute(
            adapt_query("SELECT username, role, expires FROM active_tokens WHERE token = ?"),
            (token,),
        )
        row = cursor.fetchone()
        
        if row is None:
            return None
        
        expires = row[2]
        if time.time() > expires:
            cursor.execute(
                adapt_query("DELETE FROM active_tokens WHERE token = ?"),
                (token,),
            )
            conn.commit()
            return None
            
        return {
            "username": row[0],
            "role": row[1],
            "expires": expires,
        }


def current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Dict:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. POST /api/login for a token.",
        )
    entry = _resolve_token(credentials.credentials)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token. Log in again.",
        )
    return entry


def require_roles(*allowed_roles: str):
    """Dependency factory: allows the listed roles. Developers always pass."""

    def dependency(user: Dict = Depends(current_user)) -> Dict:
        if user["role"] == "developer" or user["role"] in allowed_roles:
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user['role']}' is not permitted for this action.",
        )

    return dependency


def list_users() -> List[Dict]:
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    cursor.execute("SELECT username, role, created_at FROM users ORDER BY username")
    rows = cursor.fetchall()
    conn.close()
    return [{"username": r[0], "role": r[1], "created_at": r[2]} for r in rows]


def upsert_user(username: str, password: str, role: str) -> None:
    if role not in ROLES:
        raise ValueError(f"Unknown role '{role}'. Valid roles: {', '.join(ROLES)}")
    conn = connect_db(validate_schema=False)
    cursor = conn.cursor()
    cursor.execute(adapt_query("SELECT id FROM users WHERE username = ?"), (username,))
    exists = cursor.fetchone()
    if exists:
        cursor.execute(
            adapt_query("UPDATE users SET password_hash = ?, role = ? WHERE username = ?"),
            (_hash_password(password), role, username),
        )
    else:
        cursor.execute(
            adapt_query(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)"
            ),
            (username, _hash_password(password), role, time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
    conn.commit()
    conn.close()
