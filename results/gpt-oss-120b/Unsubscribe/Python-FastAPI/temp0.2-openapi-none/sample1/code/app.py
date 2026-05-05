from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn
import re
from email import policy
from email.parser import BytesParser, Parser
from email.utils import parseaddr

app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

UNSUBSCRIBE_KEYWORDS = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
]

# Compile a regex that matches any of the keywords as whole words/phrases, case‑insensitive
keyword_pattern = re.compile(
    r"|".join(re.escape(k) for k in UNSUBSCRIBE_KEYWORDS), re.IGNORECASE
)


class DecisionResponse(BaseModel):
    email: str
    unsubscribe: bool


def extract_email_address(from_header: str) -> str:
    """
    Extract the email address from a From header.
    """
    name, addr = parseaddr(from_header)
    return addr


def extract_text_from_email(raw_email: str) -> str:
    """
    Parse the raw email string and return concatenated plain‑text content.
    """
    # The email may contain CRLF line endings; ensure we treat it as bytes for the parser
    if isinstance(raw_email, str):
        raw_bytes = raw_email.encode("utf-8")
    else:
        raw_bytes = raw_email

    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

    # If multipart, collect all text/plain parts
    if message.is_multipart():
        parts_text = []
        for part in message.iter_parts():
            if part.get_content_type() == "text/plain":
                try:
                    parts_text.append(part.get_content())
                except Exception:
                    # Fallback to raw payload decoding
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts_text.append(payload.decode(part.get_content_charset("utf-8"), errors="ignore"))
        return "\n".join(parts_text)
    else:
        # Single part email
        if message.get_content_type() == "text/plain":
            return message.get_content()
        else:
            # Not plain text – try to get the payload as fallback
            payload = message.get_payload(decode=True)
            if payload:
                return payload.decode(message.get_content_charset("utf-8"), errors="ignore")
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
        }
    },
)
async def decide_unsubscribe(
    raw_email: str = Body(..., media_type="text/plain")
):
    # Parse the email and extract the From address
    try:
        # Use the same parsing logic as in the helper to also get the body
        email_body = extract_text_from_email(raw_email)

        # Parse headers separately to get the From field
        header_parser = Parser(policy=policy.default)
        header_msg = header_parser.parsestr(raw_email)
        from_header = header_msg.get("From")
        if not from_header:
            raise HTTPException(status_code=400, detail="Missing 'From' header in email.")
        email_address = extract_email_address(from_header)
        if not email_address:
            raise HTTPException(status_code=400, detail="Could not parse email address from 'From' header.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid email format: {e}")

    # Determine unsubscribe intent
    unsubscribe = is_unsubscribe_requested(email_body)

    response = DecisionResponse(email=email_address, unsubscribe=unsubscribe)
    return JSONResponse(content=response.model_dump())


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)