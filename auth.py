"""Staff login for the Report EC dashboard.

Accounts are kept in staff_accounts.json in the project folder, not in the
database. Create and manage them with add_staff.py. Passwords are stored as
scrypt hashes, never as plain text, and the file must never go on GitHub.
"""

import hashlib
import hmac
import json
import os
import secrets
import time

ACCOUNTS_FILE = os.getenv("STAFF_ACCOUNTS_FILE", "staff_accounts.json")

# After this many wrong passwords, the email is locked for LOCK_MINUTES
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 10

_failed_attempts = {}  # email -> list of times of recent failures


class LoginError(Exception):
    """Raised when an email and password can't be used to sign in."""


# ---------- Passwords ----------

def hash_password(password: str, salt: bytes = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def _password_matches(password: str, stored: str) -> bool:
    try:
        method, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if method != "scrypt":
        return False
    expected = hash_password(password, bytes.fromhex(salt_hex)).split("$")[2]
    return hmac.compare_digest(expected, digest_hex)


# A stored hash used when the email doesn't exist, so a wrong email takes as
# long to check as a wrong password and doesn't reveal which emails are real
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ---------- The accounts file ----------

def load_accounts() -> dict:
    if not os.path.exists(ACCOUNTS_FILE):
        return {}
    with open(ACCOUNTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_accounts(accounts: dict) -> None:
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=2)


# ---------- Logging in ----------

def _recent_failures(email: str) -> list:
    cutoff = time.time() - LOCK_MINUTES * 60
    recent = [t for t in _failed_attempts.get(email, []) if t > cutoff]
    _failed_attempts[email] = recent
    return recent


def verify_credentials(email: str, password: str) -> dict:
    """Checks the email and password and returns the staff member."""

    if len(_recent_failures(email)) >= MAX_FAILED_ATTEMPTS:
        raise LoginError(f"Too many wrong attempts. Try again in {LOCK_MINUTES} minutes.")

    account = load_accounts().get(email)
    stored = account["password_hash"] if account else _DUMMY_HASH
    password_ok = _password_matches(password, stored)

    if not account or not account.get("active", True) or not password_ok:
        _failed_attempts.setdefault(email, []).append(time.time())
        raise LoginError("Email or password is incorrect.")

    _failed_attempts.pop(email, None)
    return {
        "id": email,
        "email": email,
        "name": account["name"],
        "role": account.get("role") or "Municipal staff",
    }
