from email import policy
from email.parser import Parser
from email.utils import parseaddr
import html
import re
from typing import Any

import uvicorn
from email_validator import EmailNotValidError, validate_email
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


MAX_BODY_SIZE_BYTES = 1024 * 1024  # 1 MiB
MAX_EXTRACT_TEXT_CHARS = 200_000
MAX_MIME_PARTS = 100
MAX_HEADER_LENGTH = 10_000

UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove\s+me\b",
    r"\bderegister\s+me\b",
    r"\bstop\s+sending\s+me\b",
    r"\bdelete\s+me\b",
    r"\bplease\s+remove\s+me\b",
    r"\bplease\s+unsubscribe\s+me\b",
    r"\btake\s+me\s+off\b",
    r"\bopt\s+me\s+out\b",
    r"\bstop\s+emailing\s+me\b",
    r"\bdo\s+not\s+send\s+me\b",
]

UNSUBSCRIBE_REGEX = re.compile("|".join(UNSUBSCRIBE_PATTERNS), re.IGNORECASE)
HTML_TAG_REGEX = re.compile(r"<[^>]+>")
WHITESPACE_REGEX = re.compile(r"\s+")


app = FastAPI(
    title="UnsubscribeApp",
    description=(
        "UnsubscribeApp provides a simple endpoint to decide whether a user "
        "requested to be unsubscribed from a mailing list"
    ),
    version="1.0.11",
)


class DecideUnsubscribeResponse(BaseModel):
    email: str | None = None
    unsubscribe: bool | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "c@sigparser.com",
                "unsubscribe": True,
            }
        }
    )


@app.middleware("http")
async def limit_request_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_BODY_SIZE_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large."},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": "Invalid Content-Length header."},
            )

    body = await request.body()
    if len(body) > MAX_BODY_SIZE_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": "Request body too large."},
        )

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive  # type: ignore[attr-defined]
    return await call_next(request)


def parse_message(raw_email: str):
    return Parser(policy=policy.default).parsestr(raw_email)


def decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload[:MAX_EXTRACT_TEXT_CHARS]
        if isinstance(raw_payload, list):
            return ""
        return str(raw_payload)[:MAX_EXTRACT_TEXT_CHARS]

    charset = part.get_content_charset() or "utf-8"
    try:
        decoded = payload.decode(charset, errors="replace")
    except LookupError:
        decoded = payload.decode("utf-8", errors="replace")
    return decoded[:MAX_EXTRACT_TEXT_CHARS]


def html_to_text(value: str) -> str:
    value = value[:MAX_EXTRACT_TEXT_CHARS]
    value = html.unescape(value)
    value = HTML_TAG_REGEX.sub(" ", value)
    value = WHITESPACE_REGEX.sub(" ", value)
    return value.strip()


def extract_text_from_email(raw_email: str) -> str:
    message = parse_message(raw_email)

    text_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        part_count = 0
        for part in message.walk():
            part_count += 1
            if part_count > MAX_MIME_PARTS:
                raise HTTPException(status_code=413, detail="Email structure too large.")
            if part.is_multipart():
                continue

            content_type = part.get_content_type()
            content_disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in content_disposition:
                continue

            if content_type == "text/plain":
                text_parts.append(decode_payload(part))
            elif content_type == "text/html":
                html_parts.append(html_to_text(decode_payload(part)))
    else:
        content_type = message.get_content_type()
        if content_type == "text/plain":
            text_parts.append(decode_payload(message))
        elif content_type == "text/html":
            html_parts.append(html_to_text(decode_payload(message)))
        else:
            text_parts.append(raw_email[:MAX_EXTRACT_TEXT_CHARS])

    combined = "\n".join(part for part in text_parts if part.strip()).strip()
    if combined:
        return combined[:MAX_EXTRACT_TEXT_CHARS]

    combined_html = "\n".join(part for part in html_parts if part.strip()).strip()
    if combined_html:
        return combined_html[:MAX_EXTRACT_TEXT_CHARS]

    return raw_email[:MAX_EXTRACT_TEXT_CHARS]


def extract_sender_email(raw_email: str) -> str:
    message = parse_message(raw_email)
    from_header = message.get("From", "")
    if len(from_header) > MAX_HEADER_LENGTH:
        raise HTTPException(status_code=400, detail="From header too large.")

    _, email_address = parseaddr(from_header)
    email_address = email_address.strip()
    if not email_address:
        return ""

    try:
        validated = validate_email(email_address, check_deliverability=False)
    except EmailNotValidError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sender email address: {exc}") from exc

    return validated.email


def normalize_text(text: str) -> str:
    text = text[:MAX_EXTRACT_TEXT_CHARS]
    text = text.replace("=\r\n", "").replace("=\n", "")
    text = html.unescape(text)
    text = WHITESPACE_REGEX.sub(" ", text)
    return text.strip().lower()


def should_unsubscribe(raw_email: str) -> bool:
    candidate_text = extract_text_from_email(raw_email)
    normalized = normalize_text(candidate_text)
    return bool(UNSUBSCRIBE_REGEX.search(normalized))


@app.post(
    "/decideUnsubscribe",
    response_model=DecideUnsubscribeResponse,
    summary=(
        "Expects a raw E-Mail from a user (i.e. reply to a mailing list) and "
        "returns whether the user who sent the email should be unsubscribed "
        "together with the user's E-Mail address. Emails requesting to be "
        'unsubscribed are expected to contain related keywords or phrases such as '
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
    body: str = Body(
        ...,
        media_type="text/plain",
        openapi_examples={
            "example1": {
                "summary": "Neutral reply",
                "value": """MIME-Version:1.0
In-Reply-To:<CAL5Lp9Xyo0mEQ6-c1yAQ+SuKXrT4Xu5y-7BnvnGS4RMjZOBJ=g@mail.gmail.com>
From:Chris <c@sigparser.com>
Date:Wed, 9 Jan 2019 08:36:15 -0800
Message-ID:<CABxEEoizOPyCLkq4+FBGNaw7KC2TJDfTZF5dp8xD9aFjDQoL+Q@mail.gmail.com>
Subject:Re:food for thought
To:Paul <p@sigparser.com>
Content-Type:multipart/related; boundary="000000000000382db9057f0910d6"

--000000000000382db9057f0910d6
Content-Type:multipart/alternative; boundary="000000000000382db0057f0910d5"

--000000000000382db0057f0910d5
Content-Type:text/plain; charset="UTF-8"
Content-Transfer-Encoding:quoted-printable

Ok.  Just a thought.  Got it.

--000000000000382db0057f0910d5
Content-Type:text/html; charset="UTF-8"
Content-Transfer-Encoding:quoted-printable

<div><div dir=3D"auto">Ok.=C2=A0 Just a thought.=C2=A0 Got it. =C2=A0</div>=
</div><div><br><div class=3D"gmail_quote"><div dir=3D"ltr">On Wed, Jan 9, 2=""",
            },
            "example2": {
                "summary": "Unsubscribe request",
                "value": """MIME-Version:1.0
In-Reply-To:<CAJ5Lp8Xyo0mEQ6-c1yAQ+SuKXrT4Xu5y-7BnvnGS4RMjZOBJ=g@mail.gmail.com>
From:Alex <alex@example.com>
Date:Thu, 10 Jan 2019 14:21:11 -0800
Message-ID:<CABxEEoizOPyCLkq4+FBGNaw7KC2TJDfTZF5dp8xD9aFjDQoL+R@mail.gmail.com>
Subject:Re:Newsletter Subscription
To:Paul <p@sigparser.com>
Content-Type:multipart/alternative; boundary="000000000000382db0057f0910d7"

--000000000000382db0057f0910d7
Content-Type:text/plain; charset="UTF-8"
Content-Transfer-Encoding:quoted-printable

Please unsubscribe me from this mailing list.

--000000000000382db0057f0910d7
Content-Type:text/html; charset="UTF-8"
Content-Transfer-Encoding:quoted-printable
<div>Please unsubscribe me from this mailing list.</div>
--000000000000382db0057f0910d7--""",
            },
        },
    ),
) -> DecideUnsubscribeResponse:
    email_address = extract_sender_email(body)
    unsubscribe = should_unsubscribe(body)

    return DecideUnsubscribeResponse(
        email=email_address,
        unsubscribe=unsubscribe,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)