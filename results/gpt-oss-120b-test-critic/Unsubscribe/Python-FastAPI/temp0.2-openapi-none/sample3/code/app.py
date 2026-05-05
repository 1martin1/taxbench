import re
from typing import List

import uvicorn
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel
from email_validator import EmailNotValidError, validate_email

app = FastAPI(
    title="UnsubscribeApp",
    version="1.0.11",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
MAX_REQUEST_SIZE = 1_048_576  # 1 MB limit for incoming raw email payload

UNSUBSCRIBE_KEYWORDS: List[re.Pattern] = [
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
    re.compile(r"\bremove\s+me\b", re.IGNORECASE),
    re.compile(r"\bderegister\s+me\b", re.IGNORECASE),
    re.compile(r"\bstop\s+sending\s+me\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+me\b", re.IGNORECASE),
]


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class DecisionResponse(BaseModel):
    email: str
    unsubscribe: bool


# --------------------------------------------------------------------------- #
# Helper functions
# --------------------------------------------------------------------------- #
def extract_email_address(message) -> str:
    """
    Extract the email address from the 'From' header.
    """
    from_header = message.get("From", "")
    _, addr = parseaddr(from_header)
    return addr


def extract_text_content(message) -> str:
    """
    Retrieve all text/plain parts from the email message.
    """
    if message.is_multipart():
        parts = []
        for part in message.iter_parts():
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_content()
                except Exception:
                    payload = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="ignore"
                    )
                parts.append(str(payload))
        return "\n".join(parts)
    else:
        if message.get_content_type() == "text/plain":
            try:
                return str(message.get_content())
            except Exception:
                return message.get_payload(decode=True).decode(
                    message.get_content_charset() or "utf-8", errors="ignore"
                )
        else:
            return ""


def is_unsubscribe_requested(text: str) -> bool:
    """
    Determine if any unsubscribe keyword appears in the provided text.
    """
    lowered = text.lower()
    for pattern in UNSUBSCRIBE_KEYWORDS:
        if pattern.search(lowered):
            return True
    return False


def validate_email_address(email: str) -> str:
    """
    Validate the extracted email address using `email_validator`.
    Returns the normalized email address if valid, otherwise raises HTTPException.
    """
    try:
        valid = validate_email(email, check_deliverability=False)
        return valid.email
    except EmailNotValidError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sender email address: {exc}")


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@app.post(
    "/decideUnsubscribe",
    response_model=DecisionResponse,
    summary="Decide whether the user wants to unsubscribe",
)
async def decide_unsubscribe(
    raw_email: str = Body(..., media_type="text/plain")
):
    """
    Expects a raw MIME email as plain text and returns whether the sender wants to be unsubscribed.
    """
    if not raw_email:
        raise HTTPException(status_code=400, detail="Empty request body")

    # Enforce request size limit
    if len(raw_email.encode("utf-8")) > MAX_REQUEST_SIZE:
        raise HTTPException(status_code=413, detail="Payload too large")

    # Parse the raw email
    try:
        email_bytes = raw_email.encode("utf-8")
        message = BytesParser(policy=policy.default).parsebytes(email_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse email: {e}")

    # Extract and validate sender email address
    sender_email_raw = extract_email_address(message)
    sender_email = validate_email_address(sender_email_raw)

    # Extract textual content for keyword search
    text_content = extract_text_content(message)

    # Determine unsubscribe intent
    unsubscribe = is_unsubscribe_requested(text_content)

    return DecisionResponse(email=sender_email, unsubscribe=unsubscribe)


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)