from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.security import decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, claims: dict):
        self.user_id: int = int(claims["sub"])
        self.username: str = claims.get("username", "")
        self.short_name: str = claims.get("short_name", "")
        self.home_branch_id: int | None = claims.get("home_branch_id")
        self.user_type: str = claims.get("user_type", "")


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    claims = decode_access_token(credentials.credentials)
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return CurrentUser(claims)


class CurrentCandidate:
    """Recruitment sub-portal's own CandidateID/AdminCode session model —
    a separate JWT namespace from the main portal's CurrentUser (tagged
    "typ": "candidate" so a portal token can't be replayed against
    recruitment endpoints or vice versa)."""

    def __init__(self, claims: dict):
        self.candidate_id: int = int(claims["sub"])
        self.admin_code: int = int(claims.get("admin_code", 0))
        self.is_admin: bool = self.admin_code != 0


def get_current_candidate(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentCandidate:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    claims = decode_access_token(credentials.credentials)
    if claims is None or claims.get("typ") != "candidate":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return CurrentCandidate(claims)
