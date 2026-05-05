import html
import logging
import re
from email import policy
from email.parser import Parser
from email.utils import parseaddr
from typing import List

import uvicorn
from email_validator import EmailNotValidError, validate_email
from fastapi import Body, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict


app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)

logger = logging.getLogger("unsubscribe_app")

MAX_RAW_EMAIL_CHARS = 1_000_000
MAX_PARTS = 100
MAX_EXTRACTED_TEXT_CHARS = 200_000

UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\bderegister me\b",
    r"\bstop sending me\b",
    r"\bdelete me\b",
    r"\bplease remove me\b",
    r"\bplease unsubscribe me\b",
    r"\btake me off\b",
    r"\bopt me out\b",
    r"\bopt-out\b",
    r"\bopt out\b",
    r"\bcancel subscription\b",
    r"\bstop emailing me\b",
    r"\bdo not send me\b",
    r"\bdon't send me\b",
]
UNSUBSCRIBE_REGEXES = [re.compile(pattern, re.IGNORECASE) for pattern in UNSUBSCRIBE_PATTERNS]


class DecideUnsubscribeResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "c@sigparser.com",
                "unsubscribe": True,
            }
        }
    )

    email: str
    unsubscribe: bool


def parse_email_message(raw_email: str):
    return Parser(policy=policy.default).parsestr(raw_email)


def validate_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "text/plain":
        raise HTTPException(status_code=415, detail="Content-Type must be text/plain")


def validate_raw_email_input(raw_email: str) -> None:
    if not isinstance(raw_email, str) or not raw_email.strip():
        raise HTTPException(status_code=422, detail="Request body must be a non-empty text/plain email message")
    if len(raw_email) > MAX_RAW_EMAIL_CHARS:
        raise HTTPException(status_code=413, detail="Request body too large")


def extract_sender_email(msg) -> str:
    from_header = msg.get("From", "")
    _, email_address = parseaddr(from_header)
    email_address = email_address.strip()

    if not email_address:
        raise HTTPException(status_code=422, detail="Missing or invalid From header email address")

    try:
        validated = validate_email(email_address, check_deliverability=False)
        return validated.email
    except EmailNotValidError:
        raise HTTPException(status_code=422, detail="Missing or invalid From header email address")


def strip_html_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    return html.unescape(text)


def decode_payload(part) -> str:
    payload_bytes = part.get_payload(decode=True)
    if payload_bytes is None:
        payload = part.get_payload()
        return payload if isinstance(payload, str) else ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload_bytes.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload_bytes.decode("utf-8", errors="replace")


def append_limited(parts_text: List[str], content: str, current_size: int) -> int:
    if not content or current_size >= MAX_EXTRACTED_TEXT_CHARS:
        return current_size

    remaining = MAX_EXTRACTED_TEXT_CHARS - current_size
    truncated = content[:remaining]
    if truncated:
        parts_text.append(truncated)
        current_size += len(truncated)
    return current_size


def extract_text_content(msg, raw_email: str) -> str:
    parts_text: List[str] = []
    current_size = 0
    processed_parts = 0

    if msg.is_multipart():
        for part in msg.walk():
            processed_parts += 1
            if processed_parts > MAX_PARTS:
                raise HTTPException(status_code=413, detail="Email structure too complex")

            if part.get_content_maintype() == "multipart":
                continue

            content_disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in content_disposition:
                continue

            content_type = part.get_content_type()
            if content_type not in ("text/plain", "text/html"):
                continue

            content = decode_payload(part)
            if not content:
                continue

            if content_type == "text/html":
                content = strip_html_tags(content)

            current_size = append_limited(parts_text, content, current_size)
            if current_size >= MAX_EXTRACTED_TEXT_CHARS:
                break
    else:
        content_type = msg.get_content_type()
        content = decode_payload(msg)
        if content_type == "text/html":
            content = strip_html_tags(content)
        current_size = append_limited(parts_text, content, current_size)

    combined = "\n".join(parts_text).strip()
    if combined:
        return combined

    return raw_email[:MAX_EXTRACTED_TEXT_CHARS]


def normalize_text(text: str) -> str:
    text = text[:MAX_EXTRACTED_TEXT_CHARS]
    text = text.replace("=\r\n", "").replace("=\n", "")
    text = re.sub(r"=\s*([0-9A-Fa-f]{2})", lambda m: bytes.fromhex(m.group(1)).decode("latin1"), text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def should_unsubscribe(text: str) -> bool:
    normalized = normalize_text(text)
    return any(regex.search(normalized) for regex in UNSUBSCRIBE_REGEXES)


@app.post(
    "/decideUnsubscribe",
    response_model=DecideUnsubscribeResponse,
    summary=(
        "Expects a raw E-Mail from a user (i.e. reply to a mailing list) and returns whether the user "
        "who sent the email should be unsubscribed together with the user's E-Mail address. Emails "
        'requesting to be unsubscribed are expected to contain related keywords or phrases such as '
        '"unsubscribe", "remove me", "deregister me", "stop sending me", or "delete me".'
    ),
    responses={
        200: {
            "description": "Decision made",
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "email": {"type": "string", "example": "c@sigparser.com"},
                            "unsubscribe": {"type": "boolean", "example": True},
                        },
                    }
                }
            },
        }
    },
)
async def decide_unsubscribe(
    request: Request, raw_email: str = Body(..., media_type="text/plain")
) -> DecideUnsubscribeResponse:
    validate_content_type(request)
    validate_raw_email_input(raw_email)

    try:
        msg = parse_email_message(raw_email)
        email_address = extract_sender_email(msg)
        content = extract_text_content(msg, raw_email)
        unsubscribe = should_unsubscribe(content)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Failed to process unsubscribe decision request")
        raise HTTPException(status_code=500, detail="Internal Server Error")

    return DecideUnsubscribeResponse(
        email=email_address,
        unsubscribe=unsubscribe,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)