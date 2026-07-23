from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.config import settings



# Windows CP_ACP (1252) single-byte slots with no defined character — the
# real MultiByteToWideChar/WideCharToMultiByte behavior VB.NET's Chr()/Asc()
# rely on passes these through as the identical code point rather than
# erroring or substituting a replacement character.
_CP1252_UNDEFINED = {0x81, 0x8D, 0x8F, 0x90, 0x9D}


def _vb_chr(code: int) -> str:
    """Mirrors VB.NET's Chr(): for 0-127, a direct Unicode code point; for
    128-255, decoded through the Windows ANSI codepage (CP-1252), not a raw
    Unicode mapping — the two disagree for most of the 128-159 range."""
    code &= 0xFF
    if code < 128 or code in _CP1252_UNDEFINED:
        return chr(code)
    return bytes([code]).decode("cp1252")


def _vb_asc(ch: str) -> int:
    """Mirrors VB.NET's Asc(): the inverse of _vb_chr — encodes a character
    back to its CP-1252 byte value (0-255)."""
    code = ord(ch)
    if code < 128:
        return code
    try:
        return ch.encode("cp1252")[0]
    except UnicodeEncodeError:
        return code & 0xFF


def legacy_password_obfuscate(plaintext: str) -> str:
    """Mirrors clsDBLayer.PasswordEncrypt: Chr(255 - Asc(char)) per character.

    This is not real encryption (it's how the legacy database's stored
    procedures compare passwords), reproduced here only so login continues
    to work against the real UserMaster-stored values without changing the
    DB. Confirmed live (2026-07) that a naive chr(255-ord(ch)) port is WRONG
    for the 128-159 obfuscated range (where most lowercase letters land) —
    VB.NET's Chr()/Asc() go through the Windows ANSI codepage for values
    128-255, not a direct Unicode code-point mapping, so this must round-trip
    through the same codepage semantics (_vb_chr/_vb_asc above) to match
    passwords the real ASP.NET app actually wrote.
    """
    return "".join(_vb_chr(255 - _vb_asc(ch)) for ch in plaintext)


def create_access_token(claims: dict[str, Any]) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {**claims, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None
