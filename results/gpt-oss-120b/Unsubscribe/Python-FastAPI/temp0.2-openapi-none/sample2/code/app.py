from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import JSONResponse
from typing import List
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr

app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

UNSUBSCRIBE_KEYWORDS: List[str] = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me",
]


def _extract_email_address(from_header: str) -> str:
    """
    Extract the email address from a From header.
    """
    name, addr = parseaddr(from_header)
    return addr


def _contains_unsubscribe(text: str) -> bool:
    """
    Return True if any of the unsubscribe keywords appear in the given text.
    """
    lowered = text.lower()
    return any(keyword in lowered for keyword in UNSUBSCRIBE_KEYWORDS)


def _gather_plain_text(message) -> str:
    """
    Walk through the email message and concatenate all text/plain parts.
    """
    parts: List[str] = []

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_content()
                except Exception:
                    # Fallback to raw payload decoding
                    payload = part.get_payload(decode=True).decode(part.get_content_charset("utf-8"), errors="ignore")
                if isinstance(payload, str):
                    parts.append(payload)
    else:
        if message.get_content_type() == "text/plain":
            try:
                payload = message.get_content()
            except Exception:
                payload = message.get_payload(decode=True).decode(message.get_content_charset("utf-8"), errors="ignore")
            if isinstance(payload, str):
                parts.append(payload)

    return "\n".join(parts)


@app.post("/decideUnsubscribe", response_model=dict, summary="Decide if the user wants to unsubscribe")
async def decide_unsubscribe(raw_email: str = Body(..., media_type="text/plain")):
    """
    Expects a raw E-Mail (text/plain) and returns whether the sender should be unsubscribed.
    """
    if not raw_email:
        raise HTTPException(status_code=400, detail="Empty request body")

    # Parse the raw email bytes
    raw_bytes = raw_email.encode("utf-8", errors="ignore")
    try:
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse email: {e}")

    # Extract sender email address
    from_header = message.get("From", "")
    email_address = _extract_email_address(from_header)

    # Gather all plain text content
    plain_text = _gather_plain_text(message)

    # Determine unsubscribe decision
    unsubscribe = _contains_unsubscribe(plain_text)

    response_data = {"email": email_address, "unsubscribe": unsubscribe}
    return JSONResponse(content=response_data)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)