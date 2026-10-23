from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

hasher = PasswordHasher()  # Argon2id
DUMMY_HASH = hasher.hash("dummy-verification-only-no-login")


def hash_password(password: str) -> str:
    return hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
