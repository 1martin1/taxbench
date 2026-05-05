import email
import html
import re
from email import policy
from email.parser import Parser

import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


MAX_RAW_EMAIL_BYTES = 1024 * 1024  # 1 MiB
MAX_TEXT_TO_SCAN_CHARS = 200_000


app = FastAPI(
    title="UnsubscribeApp",
    description=(
        "UnsubscribeApp provides a simple endpoint to decide whether a user "
        "requested to be unsubscribed from a mailing list"
    ),
    version="1.0.11",
)


class DecideUnsubscribeResponse(BaseModel):
    email: str = Field(..., example="c@sigparser.com")
    unsubscribe: bool = Field(..., example=True)


UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\bderegister me\b",
    r"\bdelete me\b",
    r"\bstop sending me\b",
    r"\bstop emailing me\b",
    r"\bplease remove me\b",
    r"\bplease unsubscribe me\b",
    r"\btake me off\b",
    r"\bopt me out\b",
    r"\bcancel (?:my )?subscription\b",
    r"\bdo not send me\b",
    r"\bdon't send me\b",
]


def validate_content_type(content_type: str | None) -> None:
    if not content_type:
        raise HTTPException(status_code=415, detail="Content-Type must be text/plain")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "text/plain":
        raise HTTPException(status_code=415, detail="Content-Type must be text/plain")


def validate_raw_email_size(raw_email: str) -> None:
    raw_size = len(raw_email.encode("utf-8"))
    if raw_size > MAX_RAW_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")


def parse_email_message(raw_email: str):
    return Parser(policy=policy.default).parsestr(raw_email)


def extract_sender_email(parsed_message) -> str:
    from_header = parsed_message.get("From", "")
    _, addr = email.utils.parseaddr(from_header)
    return addr.strip()


def decode_text_part(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        return raw_payload if isinstance(raw_payload, str) else ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def strip_html_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    return html.unescape(text)


def append_limited(parts: list[str], content: str, current_len: int) -> int:
    if current_len >= MAX_TEXT_TO_SCAN_CHARS or not content:
        return current_len
    remaining = MAX_TEXT_TO_SCAN_CHARS - current_len
    truncated = content[:remaining]
    if truncated:
        parts.append(truncated)
        current_len += len(truncated)
    return current_len


def extract_body_text(parsed) -> str:
    if parsed.is_multipart():
        text_parts: list[str] = []
        html_parts: list[str] = []
        text_len = 0
        html_len = 0

        for part in parsed.walk():
            if text_len >= MAX_TEXT_TO_SCAN_CHARS and html_len >= MAX_TEXT_TO_SCAN_CHARS:
                break

            if part.get_content_maintype() == "multipart":
                continue

            content_type = part.get_content_type()
            content_disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in content_disposition:
                continue

            content = decode_text_part(part)
            if content_type == "text/plain":
                text_len = append_limited(text_parts, content, text_len)
            elif content_type == "text/html":
                html_len = append_limited(html_parts, strip_html_tags(content), html_len)

        if text_parts:
            return "\n".join(text_parts)
        if html_parts:
            return "\n".join(html_parts)
        return ""

    content_type = parsed.get_content_type()
    content = decode_text_part(parsed)
    if content_type == "text/html":
        content = strip_html_tags(content)
    return content[:MAX_TEXT_TO_SCAN_CHARS]


def normalize_text(text: str) -> str:
    text = text.replace("=\r\n", "").replace("=\n", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def detect_unsubscribe_request(text: str) -> bool:
    normalized = normalize_text(text[:MAX_TEXT_TO_SCAN_CHARS])
    return any(
        re.search(pattern, normalized, flags=re.IGNORECASE)
        for pattern in UNSUBSCRIBE_PATTERNS
    )


def decide_unsubscribe(raw_email: str) -> DecideUnsubscribeResponse:
    validate_raw_email_size(raw_email)
    parsed = parse_email_message(raw_email)
    sender_email = extract_sender_email(parsed)
    if not sender_email:
        raise HTTPException(status_code=422, detail="Missing or invalid From header")
    body_text = extract_body_text(parsed)
    unsubscribe = detect_unsubscribe_request(body_text)
    return DecideUnsubscribeResponse(email=sender_email, unsubscribe=unsubscribe)


@app.post(
    "/decideUnsubscribe",
    response_model=DecideUnsubscribeResponse,
    summary=(
        "Expects a raw E-Mail from a user (i.e. reply to a mailing list) and returns "
        "whether the user who sent the email should be unsubscribed together with the "
        "user's E-Mail address. Emails requesting to be unsubscribed are expected to "
        'contain related keywords or phrases such as "unsubscribe", "remove me", '
        '"deregister me", "stop sending me", or "delete me".'
    ),
)
async def decide_unsubscribe_endpoint(
    raw_email: str = Body(..., media_type="text/plain"),
    content_type: str | None = Header(default=None, alias="Content-Type"),
) -> DecideUnsubscribeResponse:
    validate_content_type(content_type)
    return decide_unsubscribe(raw_email)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)