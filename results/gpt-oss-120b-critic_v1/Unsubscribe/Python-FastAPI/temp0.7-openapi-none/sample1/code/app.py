from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

MAX_EMAIL_SIZE = 1_048_576  # 1 MiB


@app.middleware("http")
async def enforce_payload_size(request: Request, call_next):
    """
    Middleware that checks the Content‑Length header (if present) and
    rejects requests that exceed the configured maximum size.
    """
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_EMAIL_SIZE:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Payload too large"},
                )
        except ValueError:
            # If the header is malformed we let the request proceed;
            # FastAPI/Pydantic will later enforce the size limit.
            pass
    response = await call_next(request)
    return response


class UnsubscribeDecision(BaseModel):
    email: str
    unsubscribe: bool


UNSUBSCRIBE_PHRASES = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
]


def _extract_email_address(from_header: str) -> str:
    """Return only the email address from a From header."""
    _, email_addr = parseaddr(from_header)
    return email_addr


def _extract_plain_text(msg) -> str:
    """
    Return the first plain‑text part of the email.
    Uses ``EmailMessage.get_body`` to avoid walking the whole MIME tree,
    limiting exposure to maliciously nested structures.
    """
    body_part = msg.get_body(preferencelist=("plain",))
    if body_part is None:
        return ""
    # ``get_content`` returns a string decoded according to the part's charset.
    return body_part.get_content()


def _wants_unsubscribe(text: str) -> bool:
    """Detect unsubscribe intent using simple phrase matching."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in UNSUBSCRIBE_PHRASES)


@app.post(
    "/decideUnsubscribe",
    response_model=UnsubscribeDecision,
    summary="Expects a raw E‑Mail from a user and returns whether the user should be unsubscribed.",
)
async def decide_unsubscribe(
    raw_email: str = Body(..., media_type="text/plain", max_length=MAX_EMAIL_SIZE)
) -> UnsubscribeDecision:
    """
    Parse the raw e‑mail, extract the sender address and decide if the message
    contains an unsubscribe request.
    """
    # Parse the raw e‑mail bytes; using BytesParser avoids the extra ``encode`` step.
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw_email.encode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid e‑mail format")

    # Validate and extract the sender's e‑mail address.
    from_header = msg.get("From")
    if not from_header:
        raise HTTPException(status_code=400, detail="Missing 'From' header")
    email_addr = _extract_email_address(from_header)
    if not email_addr:
        raise HTTPException(status_code=400, detail="Unable to parse sender e‑mail address")

    # Extract plain‑text content safely.
    body_text = _extract_plain_text(msg)

    # Decide if the user wants to unsubscribe based solely on the extracted text.
    unsubscribe = _wants_unsubscribe(body_text)

    return UnsubscribeDecision(email=email_addr, unsubscribe=unsubscribe)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)