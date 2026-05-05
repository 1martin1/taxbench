from email import policy
from email.parser import Parser
from email.utils import parseaddr
import html
import re
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn


app = FastAPI(
    title="UnsubscribeApp",
    description="UnsubscribeApp provides a simple endpoint to decide whether a user requested to be unsubscribed from a mailing list",
    version="1.0.11",
)


class DecideUnsubscribeResponse(BaseModel):
    email: str
    unsubscribe: bool


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
    r"\bcancel subscription\b",
    r"\bstop emailing me\b",
    r"\bdo not send me\b",
    r"\bdon't send me\b",
    r"\bno more emails\b",
    r"\bstop these emails\b",
    r"\bremove my email\b",
]

COMPILED_UNSUBSCRIBE_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in UNSUBSCRIBE_PATTERNS]

MAX_RAW_EMAIL_CHARS = 1_000_000
MAX_EXTRACTED_TEXT_CHARS = 200_000
MAX_MIME_PARTS = 200


def parse_email_message(raw_email: str):
    return Parser(policy=policy.default).parsestr(raw_email)


def extract_sender_email(parsed_message) -> str:
    from_header = parsed_message.get("From", "")
    _, email_address = parseaddr(from_header)
    return email_address.strip()


def strip_html_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    return html.unescape(text)


def decode_payload(part: Any) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = part.get_payload()
            if isinstance(payload, str):
                return payload
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    except Exception:
        try:
            payload = part.get_payload()
            if isinstance(payload, str):
                return payload
        except Exception:
            pass
        return ""


def _append_limited(chunks: list[str], current_length: int, content: str, limit: int) -> int:
    if not content or current_length >= limit:
        return current_length
    remaining = limit - current_length
    if remaining <= 0:
        return current_length
    truncated = content[:remaining]
    chunks.append(truncated)
    return current_length + len(truncated)


def extract_text_content(parsed_message, raw_email: str) -> str:
    parts_text: list[str] = []
    total_length = 0

    if parsed_message.is_multipart():
        for idx, part in enumerate(parsed_message.walk()):
            if idx >= MAX_MIME_PARTS or total_length >= MAX_EXTRACTED_TEXT_CHARS:
                break
            if part.get_content_maintype() == "multipart":
                continue
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in content_disposition:
                continue
            content = decode_payload(part)
            if content_type == "text/plain":
                total_length = _append_limited(parts_text, total_length, content, MAX_EXTRACTED_TEXT_CHARS)
            elif content_type == "text/html":
                total_length = _append_limited(
                    parts_text, total_length, strip_html_tags(content), MAX_EXTRACTED_TEXT_CHARS
                )
    else:
        content_type = parsed_message.get_content_type()
        content = decode_payload(parsed_message)
        if content_type == "text/html":
            total_length = _append_limited(parts_text, total_length, strip_html_tags(content), MAX_EXTRACTED_TEXT_CHARS)
        else:
            total_length = _append_limited(parts_text, total_length, content, MAX_EXTRACTED_TEXT_CHARS)

    combined = "\n".join(parts_text).strip()
    if combined:
        return combined
    return raw_email[:MAX_EXTRACTED_TEXT_CHARS]


def normalize_text(text: str) -> str:
    text = text[:MAX_EXTRACTED_TEXT_CHARS].lower()
    text = text.replace("=\n", "")
    text = re.sub(r"=([0-9a-f]{2})", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def decide_unsubscribe(text: str) -> bool:
    normalized = normalize_text(text)
    for pattern in COMPILED_UNSUBSCRIBE_PATTERNS:
        if pattern.search(normalized):
            return True
    return False


@app.post(
    "/decideUnsubscribe",
    response_model=DecideUnsubscribeResponse,
    summary=(
        "Expects a raw E-Mail from a user (i.e. reply to a mailing list) and returns whether "
        "the user who sent the email should be unsubscribed together with the user's E-Mail address. "
        'Emails requesting to be unsubscribed are expected to contain related keywords or phrases such as '
        '"unsubscribe", "remove me", "deregister me", "stop sending me", or "delete me".'
    ),
)
async def decide_unsubscribe_endpoint(
    raw_email: str = Body(..., media_type="text/plain")
) -> DecideUnsubscribeResponse:
    if len(raw_email) > MAX_RAW_EMAIL_CHARS:
        raise HTTPException(status_code=413, detail="Request body too large")

    parsed_message = parse_email_message(raw_email)
    sender_email = extract_sender_email(parsed_message)
    text_content = extract_text_content(parsed_message, raw_email)
    unsubscribe = decide_unsubscribe(text_content)
    return DecideUnsubscribeResponse(email=sender_email, unsubscribe=unsubscribe)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)