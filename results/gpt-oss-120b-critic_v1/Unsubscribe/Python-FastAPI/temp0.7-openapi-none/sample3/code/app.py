import os
from typing import List

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from email import policy
from email.parser import Parser
from email.utils import parseaddr

app = FastAPI(title="UnsubscribeApp", version="1.0.11")

# ---------------------------------------------------------------------------
# Configuration / limits
# ---------------------------------------------------------------------------
MAX_EMAIL_SIZE = 100_000          # 100 KB – reasonable upper bound for a raw email
MAX_MULTIPART_PARTS = 10          # Prevent excessively deep or large multipart bodies

UNSUBSCRIBE_PHRASES: List[str] = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
]

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def safe_parse_email(raw_email: str):
    """
    Parse a raw e‑mail string safely.
    Raises HTTPException(400) if parsing fails.
    """
    try:
        return Parser(policy=policy.default).parsestr(raw_email)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse email: {exc}") from exc


def extract_email_address(raw_email: str) -> str:
    """
    Return the address from the ``From`` header.
    Raises HTTPException(400) on missing or malformed header.
    """
    message = safe_parse_email(raw_email)
    from_header = message["From"]
    if not from_header:
        raise HTTPException(status_code=400, detail="Missing 'From' header in email.")
    name, addr = parseaddr(from_header)
    if not addr:
        raise HTTPException(
            status_code=400,
            detail="Unable to parse email address from 'From' header.",
        )
    return addr


def extract_plain_text_body(raw_email: str) -> str:
    """
    Return the first ``text/plain`` part of the e‑mail.
    If the message is not multipart or no plain‑text part exists,
    the payload is returned as‑is (could be empty).
    Limits the number of inspected parts to ``MAX_MULTIPART_PARTS``.
    """
    message = safe_parse_email(raw_email)

    if message.is_multipart():
        for idx, part in enumerate(message.iter_parts()):
            if idx >= MAX_MULTIPART_PARTS:
                break  # stop processing to avoid resource exhaustion
            if part.get_content_type() == "text/plain":
                return part.get_content()
        # No plain‑text part found within the allowed depth
        return ""
    else:
        # Not multipart – treat the whole payload as text
        if message.get_content_type() == "text/plain":
            return message.get_content()
        return message.get_content()


def is_unsubscribe_requested(body_text: str) -> bool:
    """
    Determine whether the e‑mail body contains any of the unsubscribe phrases.
    Matching is case‑insensitive.
    """
    lowered = body_text.lower()
    return any(phrase in lowered for phrase in UNSUBSCRIBE_PHRASES)


# ---------------------------------------------------------------------------
# Response model (matches OpenAPI spec: plain string for email)
# ---------------------------------------------------------------------------
class DecisionResponse(BaseModel):
    email: str = Field(..., example="c@sigparser.com")
    unsubscribe: bool = Field(..., example=True)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post(
    "/decideUnsubscribe",
    response_model=DecisionResponse,
    summary="Decide whether the sender wants to be unsubscribed",
    response_description="Decision made",
)
async def decide_unsubscribe(request: Request) -> DecisionResponse:
    """
    Accept a raw e‑mail (``text/plain``) and decide if it contains an unsubscribe request.
    Returns the sender's e‑mail address and a boolean flag.
    """
    # -----------------------------------------------------------------------
    # Enforce request size limit early (pre‑emptive DoS protection)
    # -----------------------------------------------------------------------
    raw_bytes = await request.body()
    if len(raw_bytes) > MAX_EMAIL_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Request body too large (>{MAX_EMAIL_SIZE} bytes).",
        )
    # Decode using UTF‑8 with replacement for invalid sequences
    raw_email = raw_bytes.decode("utf-8", errors="replace")

    # -----------------------------------------------------------------------
    # Core processing
    # -----------------------------------------------------------------------
    sender_email = extract_email_address(raw_email)
    body_text = extract_plain_text_body(raw_email)
    unsubscribe_flag = is_unsubscribe_requested(body_text)

    return DecisionResponse(email=sender_email, unsubscribe=unsubscribe_flag)


# ---------------------------------------------------------------------------
# Run server
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)