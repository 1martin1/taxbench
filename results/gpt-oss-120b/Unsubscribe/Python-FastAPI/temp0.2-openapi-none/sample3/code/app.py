import uvicorn
from fastapi import FastAPI, Body, HTTPException
from pydantic import BaseModel
from typing import List
import re
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

app = FastAPI(title="UnsubscribeApp", version="1.0.11", description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list")


UNSUBSCRIBE_KEYWORDS: List[re.Pattern] = [
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
    re.compile(r"\bremove\s+me\b", re.IGNORECASE),
    re.compile(r"\bderegister\s+me\b", re.IGNORECASE),
    re.compile(r"\bstop\s+sending\s+me\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+me\b", re.IGNORECASE),
]


class DecisionResponse(BaseModel):
    email: str
    unsubscribe: bool


def extract_email_address(message) -> str:
    """
    Extract the email address from the 'From' header.
    """
    from_header = message.get("From", "")
    name, addr = parseaddr(from_header)
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
                    payload = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="ignore")
                parts.append(str(payload))
        return "\n".join(parts)
    else:
        if message.get_content_type() == "text/plain":
            try:
                return str(message.get_content())
            except Exception:
                return message.get_payload(decode=True).decode(message.get_content_charset() or "utf-8", errors="ignore")
        else:
            # Not plain text, return empty string
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


@app.post("/decideUnsubscribe", response_model=DecisionResponse, summary="Decide whether the user wants to unsubscribe")
async def decide_unsubscribe(raw_email: str = Body(..., media_type="text/plain")):
    """
    Expects a raw MIME email as plain text and returns whether the sender wants to be unsubscribed.
    """
    if not raw_email:
        raise HTTPException(status_code=400, detail="Empty request body")

    # Parse the raw email
    try:
        email_bytes = raw_email.encode("utf-8")
        message = BytesParser(policy=policy.default).parsebytes(email_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse email: {e}")

    # Extract sender email address
    sender_email = extract_email_address(message)

    # Extract textual content for keyword search
    text_content = extract_text_content(message)

    # Determine unsubscribe intent
    unsubscribe = is_unsubscribe_requested(text_content)

    return DecisionResponse(email=sender_email, unsubscribe=unsubscribe)


if __name__ == "__main__":
    # Run the application on 0.0.0.0:5000
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)