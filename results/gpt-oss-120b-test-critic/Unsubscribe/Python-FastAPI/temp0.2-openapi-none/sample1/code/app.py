from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, EmailStr
import uvicorn
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from email_validator import validate_email, EmailNotValidError

app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

# Maximum allowed payload size (1 MB)
MAX_EMAIL_SIZE = 1 * 1024 * 1024

UNSUBSCRIBE_KEYWORDS = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
]

# Compile a regex that matches any of the keywords as whole words/phrases, case‑insensitive
keyword_pattern = re.compile(
    r"\b(?:"
    + r"|".join(re.escape(k) for k in UNSUBSCRIBE_KEYWORDS)
    + r")\b",
    re.IGNORECASE,
)


class DecisionResponse(BaseModel):
    email: EmailStr
    unsubscribe: bool


def extract_email_address(from_header: str) -> str:
    """
    Extract the email address from a From header.
    """
    name, addr = parseaddr(from_header)
    return addr


def extract_text_from_email(raw_bytes: bytes) -> str:
    """
    Parse the raw email bytes and return concatenated plain‑text content.
    """
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    # If multipart, collect all text/plain parts
    if message.is_multipart():
        parts_text = []
        for part in message.iter_parts():
            if part.get_content_type() == "text/plain":
                try:
                    parts_text.append(part.get_content())
                except Exception:
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts_text.append(
                            payload.decode(
                                part.get_content_charset("utf-8"), errors="ignore"
                            )
                        )
        return "\n".join(parts_text)
    else:
        # Single part email
        if message.get_content_type() == "text/plain":
            return message.get_content()
        else:
            payload = message.get_payload(decode=True)
            if payload:
                return payload.decode(
                    message.get_content_charset("utf-8"), errors="ignore"
                )
            return ""


def is_unsubscribe_requested(email_body: str) -> bool:
    """
    Determine if the email body contains any unsubscribe keyword.
    """
    return bool(keyword_pattern.search(email_body))


@app.post(
    "/decideUnsubscribe",
    response_model=DecisionResponse,
    summary="Expects a raw E-Mail from a user (i.e. reply to a mailing list) and returns whether the user who sent the email should be unsubscribed together with the user's E-Mail address.",
    responses={
        200: {
            "description": "Decision made",
            "content": {
                "application/json": {
                    "example": {"email": "c@sigparser.com", "unsubscribe": True}
                }
            },
        },
        400: {"description": "Bad request"},
        413: {"description": "Payload too large"},
    },
)
async def decide_unsubscribe(request: Request):
    # Enforce payload size limit
    raw_body = await request.body()
    if len(raw_body) > MAX_EMAIL_SIZE:
        raise HTTPException(status_code=413, detail="Payload too large")

    # Decode as UTF‑8 (replace errors)
    raw_email_str = raw_body.decode("utf-8", errors="replace")

    # Parse email and extract needed information
    try:
        email_body = extract_text_from_email(raw_body)

        # Parse headers to get the From field
        message = BytesParser(policy=policy.default).parsebytes(raw_body)
        from_header = message.get("From")
        if not from_header:
            raise HTTPException(
                status_code=400, detail="Missing 'From' header in email."
            )

        email_address = extract_email_address(from_header)
        if not email_address:
            raise HTTPException(
                status_code=400, detail="Could not parse email address from 'From' header."
            )

        # Validate email format
        try:
            validate_email(email_address)
        except EmailNotValidError:
            raise HTTPException(
                status_code=400, detail="Invalid email address format."
            )
    except HTTPException:
        # Re‑raise known HTTP errors unchanged
        raise
    except Exception:
        # Hide internal details from the client
        raise HTTPException(status_code=400, detail="Invalid email format")

    # Determine unsubscribe intent
    unsubscribe = is_unsubscribe_requested(email_body)

    return DecisionResponse(email=email_address, unsubscribe=unsubscribe)


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run(app, host="0.0.0.0", port=5000, reload=False)