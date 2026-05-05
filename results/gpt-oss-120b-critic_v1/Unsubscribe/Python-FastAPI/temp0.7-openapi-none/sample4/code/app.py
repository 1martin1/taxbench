import logging
from fastapi import FastAPI, Body, HTTPException
from pydantic import BaseModel
from typing import List
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from email_validator import validate_email, EmailNotValidError

app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("unsubscribe_app")

class DecisionResponse(BaseModel):
    email: str
    unsubscribe: bool

UNSUBSCRIBE_KEYWORDS = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
]

MAX_EMAIL_SIZE = 100_000  # bytes


def extract_email_address(msg) -> str:
    """Return a validated email address from the From header, or empty string."""
    from_header = msg["From"]
    if not from_header:
        return ""
    _, addr = parseaddr(from_header)
    try:
        v = validate_email(addr)
        return v.email
    except EmailNotValidError:
        return ""


def extract_plain_text_parts(msg) -> List[str]:
    """Collect all text/plain parts of the email message."""
    texts: List[str] = []
    if msg.is_multipart():
        for part in msg.iter_parts():
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_content()
                except Exception:
                    raw = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset("utf-8")
                    payload = raw.decode(charset, errors="ignore")
                texts.append(str(payload))
    else:
        if msg.get_content_type() == "text/plain":
            try:
                payload = msg.get_content()
            except Exception:
                raw = msg.get_payload(decode=True) or b""
                charset = msg.get_content_charset("utf-8")
                payload = raw.decode(charset, errors="ignore")
            texts.append(str(payload))
        else:
            raw = msg.get_payload(decode=True)
            if raw:
                charset = msg.get_content_charset("utf-8")
                texts.append(raw.decode(charset, errors="ignore"))
    return texts


def decide_unsubscribe(raw_email: str) -> DecisionResponse:
    """Parse the raw email and decide whether it contains an unsubscribe request."""
    raw_bytes = raw_email.encode("utf-8", errors="ignore")
    msg = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    email_address = extract_email_address(msg)

    body_parts = extract_plain_text_parts(msg)
    combined_body = "\n".join(body_parts).lower()

    unsubscribe = any(keyword in combined_body for keyword in UNSUBSCRIBE_KEYWORDS)

    return DecisionResponse(email=email_address, unsubscribe=unsubscribe)


@app.post(
    "/decideUnsubscribe",
    summary=(
        "Expects a raw E-Mail from a user (i.e. reply to a mailing list) and returns whether the "
        "user who sent the email should be unsubscribed together with the user's E-Mail address. "
        "Emails requesting to be unsubscribed are expected to contain related keywords or phrases "
        "such as \"unsubscribe\", \"remove me\", \"deregister me\", \"stop sending me\", or \"delete me\"."
    ),
    response_model=DecisionResponse,
)
async def decide_unsubscribe_endpoint(
    raw_email: str = Body(..., media_type="text/plain", max_length=MAX_EMAIL_SIZE)
):
    """
    Endpoint that receives a raw email (text/plain) and returns a JSON object with the
    sender's email address and a boolean indicating whether an unsubscribe request was detected.
    """
    try:
        decision = decide_unsubscribe(raw_email)
        return decision
    except Exception:
        logger.exception("Error processing unsubscribe decision")
        raise HTTPException(status_code=400, detail="Invalid request payload")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)