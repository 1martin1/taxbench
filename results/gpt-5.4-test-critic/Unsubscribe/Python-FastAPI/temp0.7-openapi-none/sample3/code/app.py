from __future__ import annotations

import email
import html
import re
from email import policy
from email.utils import parseaddr

import uvicorn
from email_validator import EmailNotValidError, validate_email
from fastapi import Body, FastAPI, Request
from pydantic import BaseModel, ConfigDict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


app = FastAPI(
    title="UnsubscribeApp",
    description=(
        "UnsubscribeApp provides a simple endpoint to decide whether a user "
        "requested to be unsubscribed from a mailing list"
    ),
    version="1.0.11",
)


MAX_REQUEST_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 200_000


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
            except ValueError:
                pass

        body = await request.body()
        if len(body) > MAX_REQUEST_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large"},
            )

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)


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


UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bplease unsubscribe me\b",
    r"\bremove me\b",
    r"\bremove my email\b",
    r"\bderegister me\b",
    r"\bdelete me\b",
    r"\bdelete my email\b",
    r"\bstop sending me\b",
    r"\bstop emailing me\b",
    r"\btake me off\b",
    r"\btake me off (?:this )?(?:mailing )?list\b",
    r"\bopt me out\b",
    r"\bopt-out\b",
    r"\bopt out\b",
    r"\bno more emails\b",
    r"\bdo not email me\b",
    r"\bdon't email me\b",
    r"\bdo not send me\b",
    r"\bdon't send me\b",
    r"\bremove me from (?:this )?(?:mailing )?list\b",
    r"\bunsubscribe me from (?:this )?(?:mailing )?list\b",
]

COMPILED_UNSUBSCRIBE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in UNSUBSCRIBE_PATTERNS
]


def clamp_text(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return clamp_text(raw_payload)
        return ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return clamp_text(payload.decode(charset, errors="replace"))
    except LookupError:
        return clamp_text(payload.decode("utf-8", errors="replace"))


def strip_html_tags(text: str) -> str:
    text = clamp_text(text)
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    return clamp_text(html.unescape(text))


def extract_email_address(raw_email: str) -> str:
    message = email.message_from_string(raw_email, policy=policy.default)
    from_header = message.get("From", "")
    _, addr = parseaddr(from_header)
    if not addr:
        return ""

    try:
        validated = validate_email(addr, check_deliverability=False)
        return validated.normalized
    except EmailNotValidError:
        return ""


def extract_text_content(raw_email: str) -> str:
    message = email.message_from_string(raw_email, policy=policy.default)
    parts_text: list[str] = []
    total_chars = 0

    if message.is_multipart():
        for part in message.walk():
            if total_chars >= MAX_TEXT_CHARS:
                break

            if part.is_multipart():
                continue

            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in content_disposition:
                continue

            extracted = ""
            if content_type == "text/plain":
                extracted = decode_payload(part)
            elif content_type == "text/html":
                extracted = strip_html_tags(decode_payload(part))

            if extracted:
                remaining = MAX_TEXT_CHARS - total_chars
                extracted = extracted[:remaining]
                parts_text.append(extracted)
                total_chars += len(extracted)
    else:
        content_type = message.get_content_type()
        body = decode_payload(message)
        if content_type == "text/html":
            body = strip_html_tags(body)
        parts_text.append(body[:MAX_TEXT_CHARS])

    if not parts_text:
        return clamp_text(raw_email)

    return clamp_text("\n".join(parts_text))


def normalize_text(text: str) -> str:
    text = clamp_text(text)
    text = text.replace("=\r\n", "").replace("=\n", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def is_unsubscribe_request(text: str) -> bool:
    normalized = normalize_text(text)
    return any(pattern.search(normalized) for pattern in COMPILED_UNSUBSCRIBE_PATTERNS)


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
                    "--000000000000382db0057f0910d7\n"
                    'Content-Type:text/html; charset="UTF-8"\n'
                    "Content-Transfer-Encoding:quoted-printable\n"
                    "<div>Please unsubscribe me from this mailing list.</div>\n"
                    "--000000000000382db0057f0910d7--\n"
                ),
            },
        },
    ),
) -> DecideUnsubscribeResponse:
    sender_email = extract_email_address(raw_email)
    text_content = extract_text_content(raw_email)
    unsubscribe = is_unsubscribe_request(text_content)

    return DecideUnsubscribeResponse(email=sender_email, unsubscribe=unsubscribe)


@app.get("/", include_in_schema=False)
async def root() -> Response:
    return Response(status_code=404)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)