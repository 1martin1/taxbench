import uvicorn
from fastapi import FastAPI, Body, HTTPException
from pydantic import BaseModel
from typing import List
import re
import email
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

UNSUBSCRIBE_KEYWORDS: List[re.Pattern] = [
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
    re.compile(r"\bremove me\b", re.IGNORECASE),
    re.compile(r"\bderegister me\b", re.IGNORECASE),
    re.compile(r"\bstop sending me\b", re.IGNORECASE),
    re.compile(r"\bdelete me\b", re.IGNORECASE),
]

class DecisionResponse(BaseModel):
    email: str
    unsubscribe: bool

def extract_email_address(raw_email: str) -> str:
    """
    Parse the raw RFC822 email and return the email address from the From header.
    """
    # email.parser works with bytes for proper handling of encoded headers
    msg = BytesParser(policy=policy.default).parsebytes(raw_email.encode("utf-8"))
    from_header = msg["From"]
    if not from_header:
        raise ValueError("Missing 'From' header")
    name, addr = parseaddr(from_header)
    if not addr:
        raise ValueError("Unable to parse email address from 'From' header")
    return addr

def contains_unsubscribe_intent(raw_email: str) -> bool:
    """
    Determine if the email contains any of the unsubscribe keywords.
    The check is performed on the plain‑text parts of the message.
    """
    msg = BytesParser(policy=policy.default).parsebytes(raw_email.encode("utf-8"))

    # Gather all text/plain payloads (including nested multiparts)
    texts: List[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_content()
                except Exception:
                    payload = part.get_payload(decode=True).decode(errors="ignore")
                texts.append(str(payload))
    else:
        if msg.get_content_type() == "text/plain":
            try:
                payload = msg.get_content()
            except Exception:
                payload = msg.get_payload(decode=True).decode(errors="ignore")
            texts.append(str(payload))

    # If we couldn't find any text/plain part, fall back to the whole raw email
    if not texts:
        texts.append(raw_email)

    combined_text = "\n".join(texts).lower()

    for pattern in UNSUBSCRIBE_KEYWORDS:
        if pattern.search(combined_text):
            return True
    return False

@app.post("/decideUnsubscribe", response_model=DecisionResponse, summary="Decide if the user wants to unsubscribe")
async def decide_unsubscribe(
    raw_email: str = Body(..., media_type="text/plain", description="Raw RFC822 email message")
):
    """
    Expects a raw E‑Mail from a user (i.e. reply to a mailing list) and returns whether the user who sent the email
    should be unsubscribed together with the user's E‑Mail address. Emails requesting to be unsubscribed are expected
    to contain related keywords or phrases such as \"unsubscribe\", \"remove me\", \"deregister me\", \"stop sending me\",
    or \"delete me\".
    """
    try:
        email_address = extract_email_address(raw_email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    unsubscribe = contains_unsubscribe_intent(raw_email)

    return DecisionResponse(email=email_address, unsubscribe=unsubscribe)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5000, reload=False)