import uvicorn
from fastapi import FastAPI, Body, HTTPException
from pydantic import BaseModel
from typing import List

import email
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

app = FastAPI(
    title="UnsubscribeApp",
    description=(
        "UnsubscribeApp provides a simple endpoint to decide whether a user requested "
        "to be unsubscribed from a mailing list"
    ),
    version="1.0.11",
)

# --------------------------------------------------------------------------- #
# Configuration limits (security hardening)
# --------------------------------------------------------------------------- #
MAX_EMAIL_SIZE = 500_000          # 500 KiB – reasonable upper bound for a raw e‑mail
MAX_MIME_PARTS = 20               # Prevent pathological multipart messages

UNSUBSCRIBE_KEYWORDS: List[str] = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
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
def _parse_email(raw_email: str) -> email.message.EmailMessage:
    """
    Parse a raw e‑mail string into an EmailMessage object.
    The function expects a UTF‑8 compatible string and works with the
    default email policy.
    """
    # Encode to bytes once – this is the format expected by BytesParser
    raw_bytes = raw_email.encode("utf-8", errors="replace")
    return BytesParser(policy=policy.default).parsebytes(raw_bytes)


def extract_email_address(raw_email: str) -> str:
    """
    Extract the sender's e‑mail address from the ``From`` header.
    Raises ValueError if the header is missing or malformed.
    """
    msg = _parse_email(raw_email)
    name, addr = parseaddr(msg.get("From", ""))
    if not addr:
        raise ValueError("Missing or malformed 'From' header")
    return addr


def extract_plain_text_body(raw_email: str) -> str:
    """
    Return the first ``text/plain`` part of the message.
    The function limits the number of examined MIME parts to ``MAX_MIME_PARTS``
    to avoid excessive CPU/memory consumption on crafted messages.
    """
    msg = _parse_email(raw_email)

    if msg.is_multipart():
        parts_examined = 0
        for part in msg.walk():
            # Respect the limit on examined parts
            parts_examined += 1
            if parts_examined > MAX_MIME_PARTS:
                break

            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace")
                except Exception:
                    # Fallback to UTF‑8 if declared charset fails
                    return payload.decode("utf-8", errors="replace")
        # No plain‑text part found within the allowed limit
        return ""
    else:
        # Non‑multipart message – treat the whole payload as text
        payload = msg.get_payload(decode=True)
        if payload is None:
            # Payload already a string (unlikely with BytesParser, but safe)
            return str(msg.get_payload())
        charset = msg.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except Exception:
            return payload.decode("utf-8", errors="replace")


def decide_unsubscribe(body_text: str) -> bool:
    """
    Determine whether the e‑mail body contains any unsubscribe keyword.
    The check is case‑insensitive.
    """
    lowered = body_text.lower()
    return any(kw in lowered for kw in UNSUBSCRIBE_KEYWORDS)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #
@app.post(
    "/decideUnsubscribe",
    response_model=DecisionResponse,
    summary=(
        "Expects a raw E‑Mail from a user (i.e. reply to a mailing list) and returns "
        "whether the user who sent the email should be unsubscribed together with "
        "the user's E‑Mail address. Emails requesting to be unsubscribed are expected "
        "to contain related keywords or phrases such as \"unsubscribe\", \"remove me\", "
        "\"deregister me\", \"stop sending me\", or \"delete me\"."
    ),
)
async def decide_unsubscribe_endpoint(
    raw_email: str = Body(..., media_type="text/plain")
):
    # ------------------------------------------------------------------- #
    # Basic payload size validation (prevents uncontrolled resource usage)
    # ------------------------------------------------------------------- #
    if len(raw_email.encode("utf-8")) > MAX_EMAIL_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Payload exceeds maximum allowed size of {MAX_EMAIL_SIZE} bytes",
        )

    if not raw_email.strip():
        raise HTTPException(status_code=400, detail="Empty email payload")

    # ------------------------------------------------------------------- #
    # Extract sender address – detailed errors are surfaced to the client
    # ------------------------------------------------------------------- #
    try:
        sender_email = extract_email_address(raw_email)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid email format: {exc}")

    # ------------------------------------------------------------------- #
    # Extract plain‑text body (with MIME part limits)
    # ------------------------------------------------------------------- #
    try:
        body_text = extract_plain_text_body(raw_email)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to read email body: {exc}")

    # ------------------------------------------------------------------- #
    # Decision logic
    # ------------------------------------------------------------------- #
    unsubscribe = decide_unsubscribe(body_text)

    return DecisionResponse(email=sender_email, unsubscribe=unsubscribe)


# --------------------------------------------------------------------------- #
# Application entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)