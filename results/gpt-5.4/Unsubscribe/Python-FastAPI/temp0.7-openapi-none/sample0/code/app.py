from __future__ import annotations

import email
import html
import re
from email import policy
from email.parser import Parser
from email.utils import parseaddr
from typing import Optional

import uvicorn
from fastapi import Body, FastAPI, Response
from pydantic import BaseModel, ConfigDict


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

    email: str
    unsubscribe: bool


UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove me\b",
    r"\bderegister me\b",
    r"\bstop sending me\b",
    r"\bdelete me\b",
    r"\bplease unsubscribe me\b",
    r"\bremove me from (?:this )?(?:mailing list|list|newsletter)\b",
    r"\btake me off (?:this )?(?:mailing list|list|newsletter)\b",
    r"\bopt me out\b",
    r"\bstop emailing me\b",
    r"\bstop sending (?:me )?(?:these )?emails\b",
]

UNSUBSCRIBE_REGEXES = [re.compile(pattern, re.IGNORECASE) for pattern in UNSUBSCRIBE_PATTERNS]


def extract_sender_email(raw_email: str) -> str:
    msg = Parser(policy=policy.default).parsestr(raw_email)
    from_header = msg.get("From", "")
    _, email_address = parseaddr(from_header)
    return email_address.strip()


def decode_part_payload(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw_payload = part.get_payload()
        if isinstance(raw_payload, str):
            return raw_payload
        return ""

    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def strip_html_tags(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    return html.unescape(text)


def extract_text_content(raw_email: str) -> str:
    msg = Parser(policy=policy.default).parsestr(raw_email)
    text_chunks: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue

            content_disposition = part.get("Content-Disposition", "")
            if "attachment" in content_disposition.lower():
                continue

            content_type = part.get_content_type()
            decoded = decode_part_payload(part)

            if content_type == "text/plain":
                text_chunks.append(decoded)
            elif content_type == "text/html":
                text_chunks.append(strip_html_tags(decoded))
    else:
        content_type = msg.get_content_type()
        decoded = decode_part_payload(msg)
        if content_type == "text/html":
            text_chunks.append(strip_html_tags(decoded))
        else:
            text_chunks.append(decoded)

    if not text_chunks:
        return raw_email

    return "\n".join(chunk for chunk in text_chunks if chunk)


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"=\n", "", value)
    value = re.sub(r"[ \t]+", " ", value)
    return value.lower()


def detect_unsubscribe_request(text: str) -> bool:
    normalized = normalize_text(text)
    return any(regex.search(normalized) for regex in UNSUBSCRIBE_REGEXES)


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
    sender_email = extract_sender_email(raw_email)
    text_content = extract_text_content(raw_email)
    unsubscribe = detect_unsubscribe_request(text_content)
    return DecideUnsubscribeResponse(email=sender_email, unsubscribe=unsubscribe)


@app.get("/", include_in_schema=False)
async def root() -> Response:
    return Response(content="UnsubscribeApp", media_type="text/plain")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)