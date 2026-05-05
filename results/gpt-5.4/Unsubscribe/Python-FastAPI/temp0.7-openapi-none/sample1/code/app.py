import email
import html
import re
from email import policy
from email.parser import Parser
from typing import Optional

import uvicorn
from fastapi import Body, FastAPI
from pydantic import BaseModel, Field


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


def extract_sender_email(raw_email: str) -> str:
    parsed = Parser(policy=policy.default).parsestr(raw_email)
    from_header = parsed.get("From", "")
    _, addr = email.utils.parseaddr(from_header)
    return addr


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


def extract_body_text(raw_email: str) -> str:
    parsed = Parser(policy=policy.default).parsestr(raw_email)

    if parsed.is_multipart():
        text_parts = []
        html_parts = []

        for part in parsed.walk():
            if part.get_content_maintype() == "multipart":
                continue

            content_type = part.get_content_type()
            content_disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in content_disposition:
                continue

            content = decode_text_part(part)
            if content_type == "text/plain":
                text_parts.append(content)
            elif content_type == "text/html":
                html_parts.append(strip_html_tags(content))

        if text_parts:
            return "\n".join(text_parts)
        if html_parts:
            return "\n".join(html_parts)
        return ""

    content_type = parsed.get_content_type()
    content = decode_text_part(parsed)
    if content_type == "text/html":
        return strip_html_tags(content)
    return content


def normalize_text(text: str) -> str:
    text = text.replace("=\r\n", "").replace("=\n", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def detect_unsubscribe_request(text: str) -> bool:
    normalized = normalize_text(text)
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in UNSUBSCRIBE_PATTERNS)


def decide_unsubscribe(raw_email: str) -> DecideUnsubscribeResponse:
    sender_email: str = extract_sender_email(raw_email)
    body_text: str = extract_body_text(raw_email)
    unsubscribe: bool = detect_unsubscribe_request(body_text)
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
    raw_email: str = Body(..., media_type="text/plain")
) -> DecideUnsubscribeResponse:
    return decide_unsubscribe(raw_email)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)