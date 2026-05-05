import uvicorn
from fastapi import FastAPI, Body, HTTPException
from pydantic import BaseModel
from typing import List
import email
from email import policy
from email.utils import parseaddr

app = FastAPI(title="UnsubscribeApp", version="1.0.11")


UNSUBSCRIBE_KEYWORDS: List[str] = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
]


class DecisionResponse(BaseModel):
    email: str
    unsubscribe: bool


def extract_email_address(message: email.message.EmailMessage) -> str:
    """Extract the sender's eвЂ‘mail address from the From header."""
    from_header = message.get("From")
    if not from_header:
        raise ValueError("Missing From header")
    _, addr = parseaddr(from_header)
    if not addr:
        raise ValueError("Unable to parse eвЂ‘mail address from From header")
    return addr


def extract_plain_text(message: email.message.EmailMessage) -> str:
    """
    Return the first text/plain payload that is not an attachment.
    For simple (nonвЂ‘multipart) messages the payload itself is returned.
    """
    if message.is_multipart():
        for part in message.walk():
            # Skip containers and attachments
            if part.get_content_type() == "text/plain" and part.get_content_disposition() != "attachment":
                return part.get_content()
        # Fallback: no plain text part found
        return ""
    else:
        if message.get_content_type() == "text/plain":
            return message.get_content()
        return ""


def decide_unsubscribe(text_body: str) -> bool:
    """Determine if the eвЂ‘mail contains any unsubscribe keyword."""
    lowered = text_body.lower()
    return any(keyword in lowered for keyword in UNSUBSCRIBE_KEYWORDS)


@app.post("/decideUnsubscribe", response_model=DecisionResponse, summary="Decide whether the sender wants to unsubscribe")
async def decide_unsubscribe_endpoint(raw_email: str = Body(..., media_type="text/plain")):
    """
    Expects a raw eвЂ‘mail (including headers) as plain text.
    Returns the sender's eвЂ‘mail address and a boolean indicating
    whether an unsubscribe request was detected.
    """
    try:
        # Parse the raw eвЂ‘mail string into an EmailMessage object
        email_message = email.message_from_string(raw_email, policy=policy.default)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse eвЂ‘mail: {e}")

    try:
        sender_email = extract_email_address(email_message)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    plain_text = extract_plain_text(email_message)

    # If we couldn't find a plainвЂ‘text part, fall back to the whole raw payload
    if not plain_text:
        plain_text = raw_email

    unsubscribe = decide_unsubscribe(plain_text)

    return DecisionResponse(email=sender_email, unsubscribe=unsubscribe)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)