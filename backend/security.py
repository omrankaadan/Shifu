import os
from typing import List

def _admin_emails() -> List[str]:
    raw = os.getenv("ADMIN_EMAILS", "").strip()
    if not raw:
        return []
    return [x.strip().lower() for x in raw.split(",") if x.strip()]

def is_admin_email(email: str) -> bool:
    return bool(email and email.lower() in _admin_emails())
