import uvicorn
import re
from typing import Optional

from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from email import policy
from email.parser import BytesParser
from email.message import EmailMessage
from email.utils import parseaddr
from email_validator import validate_email, EmailNotValidError

app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

# Keywords that indicate an unsubscribe request
UNSUBSCRIBE_KEYWORDS = [
    r"\bunsubscribe\b",
    r"\bremove\s+me\b",
    r"\bderegister\s+me\b",
    r"\bstop\s+sending\s+me\b",
    r"\bdelete\s+me\b",
]

# Compile a case‑insensitive regex. The pattern is simple and does not contain
# nested quantifiers, which mitigates the risk of catastrophic back‑tracking.
UNSUB_REGEX = re.compile("|".join(UNSUBSCRIBE_KEYWORDS), re.IGNORECASE)

# Limits
MAX_RAW_EMAIL_SIZE = 1 * 1024 * 1024          # 1 MiB
MAX_BODY_TEXT_SEARCH_SIZE = 50 * 1024        # 50 KiB – enough for detection while preventing huge scans
MAX_PARTS = 1000                             # Prevent processing an excessive number of MIME parts


def extract_email_address(message: EmailMessage) -> str:
    """
    Extract and validate the e‑mail address from the ``From`` header.
    Raises ``ValueError`` if the header is missing or the address is invalid.
    """
    from_header = message.get("From")
    if not from_header:
        raise ValueError("Missing From header")

    name, addr = parseaddr(from_header)
    if not addr:
        raise ValueError("Unable to parse e‑mail address from From header")

    # Use ``email_validator`` to ensure the address is syntactically correct.
    try:
        valid = validate_email(addr, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid e‑mail address: {exc}") from None

    return valid.email


def _collect_text_parts(message: EmailMessage, limit_parts: int) -> list[str]:
    """
    Walk through the message tree (BFS) and collect textual payloads.
    Stops when *limit_parts* parts have been processed to avoid excessive recursion.
    """
    parts_text: list[str] = []
    queue: list[EmailMessage] = [message]

    while queue and len(parts_text) < limit_parts:
        part = queue.pop(0)

        if part.is_multipart():
            # Enqueue sub‑parts for further inspection.
            queue.extend(part.iter_parts())
            continue

        if part.get_content_maintype() != "text":
            continue

        charset = part.get_content_charset() or "utf-8"
        try:
            payload_bytes = part.get_payload(decode=True) or b""
            payload = payload_bytes.decode(charset, errors="replace")
        except Exception:
            # Fallback to the raw payload string if decoding fails.
            payload = part.get_payload()
            if not isinstance(payload, str):
                payload = str(payload)

        parts_text.append(payload)

    return parts_text


def extract_body_text(message: EmailMessage) -> str:
    """
    Return a concatenated string of textual parts (plain or HTML) from the e‑mail.
    The number of processed parts is limited to ``MAX_PARTS`` to avoid DoS.
    """
    parts = _collect_text_parts(message, MAX_PARTS)
    return "\n".join(parts)


@app.post(
    "/decideUnsubscribe",
    summary="Determine if the user wants to unsubscribe",
    response_model=dict,
)
async def decide_unsubscribe(
    raw_email: str = Body(
        ...,
        media_type="text/plain",
        max_length=MAX_RAW_EMAIL_SIZE,
        description="Raw MIME e‑mail message",
    )
):
    """
    Accept a raw MIME e‑mail (as plain text) and decide whether the sender
    requested to be unsubscribed. Returns a JSON object with the sender's e‑mail
    address and a boolean ``unsubscribe`` flag.
    """
    # Enforce size limit early (FastAPI already checks ``max_length``, but we
    # double‑check in case the client bypasses the validation layer).
    if len(raw_email.encode("utf-8")) > MAX_RAW_EMAIL_SIZE:
        raise HTTPException(status_code=413, detail="Payload too large")

    try:
        # Parse the raw e‑mail using BytesParser for better handling of binary data.
        message = BytesParser(policy=policy.default).parsebytes(raw_email.encode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to parse e‑mail")

    try:
        email_address = extract_email_address(message)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    body_text = extract_body_text(message)

    # Limit the text scanned by the regex to a sane size.
    scan_text = body_text[:MAX_BODY_TEXT_SEARCH_SIZE]

    unsubscribe = bool(UNSUB_REGEX.search(scan_text))

    return JSONResponse(content={"email": email_address, "unsubscribe": unsubscribe})


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000 as required.
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")