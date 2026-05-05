from __future__ import annotations

import email
import html
import logging
import re
from email import policy
from email.errors import MessageError
from email.utils import parseaddr

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr


MAX_RAW_EMAIL_BYTES = 1024 * 1024  # 1 MiB

logger = logging.getLogger("unsubscribe_app")
logging.basicConfig(level=logging.INFO)


app = FastAPI(
    title="UnsubscribeApp",
    description=(
        "UnsubscribeApp provides a simple endpoint to decide whether a user "
        "requested to be unsubscribed from a mailing list"
    ),
    version="1.0.11",
)


class DecideUnsubscribeResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "c@sigparser.com",
                "unsubscribe": True,
            }
        }
    )

    email: EmailStr
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
    r"\bstop emailing me\b",
    r"\bstop sending emails\b",
    r"\bremove my email\b",
    r"\bdelete my email\b",
    r"\bno more emails\b",
    r"\bdo not email me\b",
    r"\bdon't email me\b",
    r"\bdo not send me\b",
    r"\bdon't send me\b",
]

UNSUBSCRIBE_REGEXES = [re.compile(pattern, re.IGNORECASE) for pattern in UNSUBSCRIBE_PATTERNS]
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")


def ensure_size_limit(raw_email: str) -> None:
    if len(raw_email.encode("utf-8")) > MAX_RAW_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large")


def parse_email_message(raw_email: str):
    try:
        return email.message_from_string(raw_email, policy=policy.default)
    except (MessageError, ValueError) as exc:
        logger.warning("Failed to parse email message: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid email content") from exc


def extract_sender_email(message) -> str:
    from_header = message.get("From")
    if not from_header:
        raise HTTPException(status_code=400, detail="Missing From header")

    _, addr = parseaddr(from_header)
    if not addr:
        raise HTTPException(status_code=400, detail="Invalid sender email address")

    try:
        validated = EmailStr(addr)
    except Exception as exc:
        logger.warning("Invalid sender email extracted: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid sender email address") from exc

    return str(validated)


def decode_part_payload(part) -> str:
    payload = part.get_payload(decode=True)
    charset = part.get_content_charset() or "utf-8"

    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload
        return ""

    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def html_to_text(value: str) -> str:
    text = html.unescape(value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = HTML_TAG_RE.sub(" ", text)
    return text


def extract_body_text(message, raw_email: str) -> str:
    text_parts = []
    html_parts = []

    if message.is_multipart():
        for part in message.walk():
            if part.is_multipart():
                continue

            content_disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in content_disposition:
                continue

            content_type = part.get_content_type()
            content = decode_part_payload(part)

            if content_type == "text/plain":
                text_parts.append(content)
            elif content_type == "text/html":
                html_parts.append(content)
    else:
        content_type = message.get_content_type()
        content = decode_part_payload(message)
        if content_type == "text/plain":
            text_parts.append(content)
        elif content_type == "text/html":
            html_parts.append(content)
        else:
            text_parts.append(content)

    if text_parts:
        return "\n".join(text_parts)

    if html_parts:
        return "\n".join(html_to_text(part) for part in html_parts)

    return raw_email


def normalize_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("=\r\n", "").replace("=\n", "")
    text = WHITESPACE_RE.sub(" ", text)
    return text.strip().lower()


def should_unsubscribe(text: str) -> bool:
    normalized = normalize_text(text)
    return any(regex.search(normalized) for regex in UNSUBSCRIBE_REGEXES)


@app.post(
    "/decideUnsubscribe",
    response_model=DecideUnsubscribeResponse,
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
    raw_email: str = Body(
        ...,
        media_type="text/plain",
        openapi_examples={
            "example1": {
                "summary": "Neutral reply",
                "value": (
                    "MIME-Version:1.0\n"
                    "In-Reply-To:<CAL5Lp9Xyo0mEQ6-c1yAQ+SuKXrT4Xu5y-7BnvnGS4RMjZOBJ=g@mail.gmail.com>\n"
                    "From:Chris <c@sigparser.com>\n"
                    "Date:Wed, 9 Jan 2019 08:36:15 -0800\n"
                    "Message-ID:<CABxEEoizOPyCLkq4+FBGNaw7KC2TJDfTZF5dp8xD9aFjDQoL+Q@mail.gmail.com>\n"
                    "Subject:Re:food for thought\n"
                    "To:Paul <p@sigparser.com>\n"
                    'Content-Type:multipart/related; boundary="000000000000382db9057f0910d6"\n'
                    "\n"
                    "--000000000000382db9057f0910d6\n"
                    'Content-Type:multipart/alternative; boundary="000000000000382db0057f0910d5"\n'
                    "\n"
                    "--000000000000382db0057f0910d5\n"
                    'Content-Type:text/plain; charset="UTF-8"\n'
                    "Content-Transfer-Encoding:quoted-printable\n"
                    "\n"
                    "Ok.  Just a thought.  Got it.\n"
                ),
            },
            "example2": {
                "summary": "Unsubscribe request",
                "value": (
                    "MIME-Version:1.0\n"
                    "In-Reply-To:<CAJ5Lp8Xyo0mEQ6-c1yAQ+SuKXrT4Xu5y-7BnvnGS4RMjZOBJ=g@mail.gmail.com>\n"
                    "From:Alex <alex@example.com>\n"
                    "Date:Thu, 10 Jan 2019 14:21:11 -0800\n"
                    "Message-ID:<CABxEEoizOPyCLkq4+FBGNaw7KC2TJDfTZF5dp8xD9aFjDQoL+R@mail.gmail.com>\n"
                    "Subject:Re:Newsletter Subscription\n"
                    "To:Paul <p@sigparser.com>\n"
                    'Content-Type:multipart/alternative; boundary="000000000000382db0057f0910d7"\n'
                    "\n"
                    "--000000000000382db0057f0910d7\n"
                    'Content-Type:text/plain; charset="UTF-8"\n'
                    "Content-Transfer-Encoding:quoted-printable\n"
                    "\n"
                    "Please unsubscribe me from this mailing list.\n"
                    "\n"
                    "--000000000000382db0057f0910d7--\n"
                ),
            },
        },
    ),
) -> DecideUnsubscribeResponse:
    ensure_size_limit(raw_email)
    message = parse_email_message(raw_email)
    sender_email = extract_sender_email(message)
    body_text = extract_body_text(message, raw_email)
    unsubscribe = should_unsubscribe(body_text)

    return DecideUnsubscribeResponse(
        email=sender_email,
        unsubscribe=unsubscribe,
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.warning("Request validation error on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled server error on %s", request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)