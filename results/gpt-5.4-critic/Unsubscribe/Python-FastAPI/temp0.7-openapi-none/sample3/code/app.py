from email import policy
from email.parser import Parser
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser
import quopri
import re

from email_validator import EmailNotValidError, validate_email
from fastapi import Body, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
import uvicorn


MAX_RAW_EMAIL_SIZE = 1024 * 1024
MAX_PARTS = 64
MAX_TEXT_CHARS = 200_000
MAX_HTML_TEXT_CHARS = 200_000


app = FastAPI(
    title="UnsubscribeApp",
    description=(
        "UnsubscribeApp provides a simple endpoint to decide whether a user "
        "requested to be unsubscribed from a mailing list"
    ),
    version="1.0.11",
)


class DecideUnsubscribeResponse(BaseModel):
    email: str = Field(..., min_length=1)
    unsubscribe: bool


UNSUBSCRIBE_PATTERNS = [
    re.compile(r"\bunsubscribe\b"),
    re.compile(r"\bremove me\b"),
    re.compile(r"\bderegister me\b"),
    re.compile(r"\bstop sending me\b"),
    re.compile(r"\bdelete me\b"),
    re.compile(r"\btake me off\b"),
    re.compile(r"\bopt me out\b"),
    re.compile(r"\bplease remove me\b"),
    re.compile(r"\bplease unsubscribe me\b"),
    re.compile(r"\bdo not send me\b"),
    re.compile(r"\bdon't send me\b"),
    re.compile(r"\bstop emailing me\b"),
    re.compile(r"\bstop e-?mailing me\b"),
    re.compile(r"\bremove my email\b"),
    re.compile(r"\bdelete my email\b"),
]

NEGATIVE_CONTEXT_PATTERNS = [
    re.compile(r"\bdo not unsubscribe\b"),
    re.compile(r"\bdon't unsubscribe\b"),
    re.compile(r"\bnot unsubscribe\b"),
    re.compile(r"\bno need to unsubscribe\b"),
]


class _HTMLTextExtractor(HTMLParser):
    def __init__(self, max_chars: int) -> None:
        super().__init__()
        self.max_chars = max_chars
        self._chunks: list[str] = []
        self._current_len = 0

    def handle_data(self, data: str) -> None:
        if not data or self._current_len >= self.max_chars:
            return
        remaining = self.max_chars - self._current_len
        chunk = data[:remaining]
        if chunk:
            self._chunks.append(chunk)
            self._current_len += len(chunk)

    def get_text(self) -> str:
        return " ".join(self._chunks)


def _validate_content_type(content_type: str | None) -> None:
    if not content_type:
        raise HTTPException(status_code=415, detail="Content-Type must be text/plain")
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "text/plain":
        raise HTTPException(status_code=415, detail="Content-Type must be text/plain")


def _validate_raw_email(raw_email: str) -> None:
    if not raw_email:
        raise HTTPException(status_code=400, detail="Request body must not be empty")
    if len(raw_email) > MAX_RAW_EMAIL_SIZE:
        raise HTTPException(status_code=413, detail="Request body too large")
    if ":" not in raw_email:
        raise HTTPException(status_code=400, detail="Invalid raw email format")


def strip_html(html_content: str) -> str:
    parser = _HTMLTextExtractor(MAX_HTML_TEXT_CHARS)
    parser.feed(html_content[:MAX_HTML_TEXT_CHARS])
    parser.close()
    return unescape(parser.get_text())[:MAX_HTML_TEXT_CHARS]


def normalize_text(text: str) -> str:
    text = text[:MAX_TEXT_CHARS]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    decoded = quopri.decodestring(text.encode("utf-8", errors="ignore"))
    text = decoded.decode("utf-8", errors="ignore")
    text = re.sub(r"=\n", "", text)
    text = text.lower()
    return text[:MAX_TEXT_CHARS]


def parse_message(raw_email: str):
    try:
        return Parser(policy=policy.default).parsestr(raw_email)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid raw email format") from exc


def extract_email(message) -> str:
    from_header = message.get("From", "")
    _, email_address = parseaddr(from_header)
    if not email_address:
        raise HTTPException(status_code=400, detail="Missing sender email address")
    try:
        validated = validate_email(email_address, check_deliverability=False)
    except EmailNotValidError as exc:
        raise HTTPException(status_code=400, detail="Invalid sender email address") from exc
    return validated.normalized


def _decode_part_payload(part) -> str:
    try:
        payload = part.get_content()
    except Exception:
        payload_bytes = part.get_payload(decode=True)
        if payload_bytes is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        try:
            payload = payload_bytes.decode(charset, errors="ignore")
        except Exception:
            payload = payload_bytes.decode("utf-8", errors="ignore")
    if isinstance(payload, str):
        return payload
    return ""


def extract_body_text(message, raw_email: str) -> str:
    parts: list[str] = []
    total_len = 0

    def append_part(text: str) -> None:
        nonlocal total_len
        if not text or total_len >= MAX_TEXT_CHARS:
            return
        remaining = MAX_TEXT_CHARS - total_len
        chunk = text[:remaining]
        if chunk:
            parts.append(chunk)
            total_len += len(chunk)

    if message.is_multipart():
        count = 0
        for part in message.walk():
            count += 1
            if count > MAX_PARTS:
                raise HTTPException(status_code=413, detail="Email structure too complex")
            if part.get_content_maintype() == "multipart":
                continue

            content_type = part.get_content_type()
            payload = _decode_part_payload(part)

            if content_type == "text/plain":
                append_part(payload)
            elif content_type == "text/html":
                append_part(strip_html(payload))

            if total_len >= MAX_TEXT_CHARS:
                break
    else:
        payload = _decode_part_payload(message)
        if not payload:
            payload = raw_email[:MAX_TEXT_CHARS]

        if message.get_content_type() == "text/html":
            append_part(strip_html(payload))
        else:
            append_part(payload)

    if parts:
        return "\n".join(parts)[:MAX_TEXT_CHARS]

    header_body_split = raw_email.split("\n\n", 1)
    if len(header_body_split) == 2:
        return header_body_split[1][:MAX_TEXT_CHARS]
    return raw_email[:MAX_TEXT_CHARS]


def decide_unsubscribe_from_text(text: str) -> bool:
    normalized = normalize_text(text)

    for pattern in NEGATIVE_CONTEXT_PATTERNS:
        if pattern.search(normalized):
            return False

    for pattern in UNSUBSCRIBE_PATTERNS:
        if pattern.search(normalized):
            return True

    return False


@app.post(
    "/decideUnsubscribe",
    response_model=DecideUnsubscribeResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/plain": {
                    "schema": {"type:string": None},
                    "examples": {
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
                                "Content-Type:multipart/related; boundary=\"000000000000382db9057f0910d6\"\n\n"
                                "--000000000000382db9057f0910d6\n"
                                "Content-Type:multipart/alternative; boundary=\"000000000000382db0057f0910d5\"\n\n"
                                "--000000000000382db0057f0910d5\n"
                                "Content-Type:text/plain; charset=\"UTF-8\"\n"
                                "Content-Transfer-Encoding:quoted-printable\n\n"
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
                                "Content-Type:multipart/alternative; boundary=\"000000000000382db0057f0910d7\"\n\n"
                                "--000000000000382db0057f0910d7\n"
                                "Content-Type:text/plain; charset=\"UTF-8\"\n"
                                "Content-Transfer-Encoding:quoted-printable\n\n"
                                "Please unsubscribe me from this mailing list.\n"
                            ),
                        },
                    },
                }
            },
        }
    },
    summary=(
        "Expects a raw E-Mail from a user (i.e. reply to a mailing list) and "
        "returns whether the user who sent the email should be unsubscribed "
        "together with the user's E-Mail address. Emails requesting to be "
        'unsubscribed are expected to contain related keywords or phrases such '
        'as "unsubscribe", "remove me", "deregister me", "stop sending me", '
        'or "delete me".'
    ),
)
async def decide_unsubscribe(
    raw_email: str = Body(..., media_type="text/plain"),
    content_type: str | None = Header(default=None, alias="Content-Type"),
) -> DecideUnsubscribeResponse:
    _validate_content_type(content_type)
    _validate_raw_email(raw_email)

    message = parse_message(raw_email)
    sender_email = extract_email(message)
    body_text = extract_body_text(message, raw_email)
    unsubscribe = decide_unsubscribe_from_text(body_text)

    return DecideUnsubscribeResponse(email=sender_email, unsubscribe=unsubscribe)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)