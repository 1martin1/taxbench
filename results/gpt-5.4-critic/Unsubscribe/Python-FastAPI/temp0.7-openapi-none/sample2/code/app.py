from email import policy
from email.parser import Parser
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser
import quopri
import re
from typing import Optional

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


MAX_RAW_EMAIL_BYTES = 1024 * 1024
MAX_PARTS_TO_PROCESS = 100
MAX_TEXT_CHARS_PER_PART = 100_000
MAX_TOTAL_TEXT_CHARS = 500_000

UNSUBSCRIBE_PATTERNS = [
    re.compile(r"\bunsubscribe\b"),
    re.compile(r"\bremove me\b"),
    re.compile(r"\bderegister me\b"),
    re.compile(r"\bstop sending me\b"),
    re.compile(r"\bdelete me\b"),
    re.compile(r"\bplease remove me\b"),
    re.compile(r"\bplease unsubscribe me\b"),
    re.compile(r"\btake me off\b"),
    re.compile(r"\bopt me out\b"),
    re.compile(r"\bcancel subscription\b"),
]

EMAIL_REGEX = re.compile(r"(?i)\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+\b")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]


def _decode_payload(payload: str, transfer_encoding: Optional[str]) -> str:
    if not payload:
        return ""

    if transfer_encoding and transfer_encoding.lower() == "quoted-printable":
        try:
            decoded_bytes = quopri.decodestring(payload)
            return decoded_bytes.decode("utf-8", errors="replace")
        except (ValueError, TypeError, UnicodeDecodeError):
            return payload

    return payload


def _html_to_text(html_content: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(unescape(html_content))
        parser.close()
        return parser.get_text()
    except ValueError:
        return html_content


def _normalize_raw_email_headers(raw_email: str) -> str:
    header_end_match = re.search(r"\r?\n\r?\n", raw_email)
    if not header_end_match:
        return raw_email

    header_block = raw_email[: header_end_match.start()]
    body = raw_email[header_end_match.start() :]

    normalized_lines: list[str] = []
    for line in header_block.splitlines():
        if line.startswith((" ", "\t")):
            normalized_lines.append(line)
            continue

        if ":" in line:
            name, value = line.split(":", 1)
            if value and not value.startswith((" ", "\t")):
                line = f"{name}: {value}"
        normalized_lines.append(line)

    return "\n".join(normalized_lines) + body


def _parse_email_once(raw_email: str):
    normalized_raw_email = _normalize_raw_email_headers(raw_email)
    return Parser(policy=policy.default).parsestr(normalized_raw_email)


def _extract_sender_email(message, raw_email: str) -> str:
    from_header = message.get("From", "")
    _, email_address = parseaddr(from_header)

    if email_address and EMAIL_REGEX.fullmatch(email_address):
        return email_address

    header_end_match = re.search(r"\r?\n\r?\n", raw_email)
    header_block = raw_email if not header_end_match else raw_email[: header_end_match.start()]

    for line in header_block.splitlines():
        if line.lower().startswith("from:"):
            candidate = line[5:].strip()
            _, fallback_email = parseaddr(candidate)
            if fallback_email and EMAIL_REGEX.fullmatch(fallback_email):
                return fallback_email

            regex_match = EMAIL_REGEX.search(candidate)
            if regex_match:
                return regex_match.group(0)

    regex_match = EMAIL_REGEX.search(header_block)
    if regex_match:
        return regex_match.group(0)

    return ""


def _extract_text_content(message, raw_email: str) -> str:
    collected_parts: list[str] = []
    total_chars = 0
    processed_parts = 0

    def append_part(text: str) -> None:
        nonlocal total_chars
        if not text or total_chars >= MAX_TOTAL_TEXT_CHARS:
            return
        remaining = MAX_TOTAL_TEXT_CHARS - total_chars
        truncated = _truncate_text(text, min(MAX_TEXT_CHARS_PER_PART, remaining))
        if truncated:
            collected_parts.append(truncated)
            total_chars += len(truncated)

    if message.is_multipart():
        for part in message.walk():
            if processed_parts >= MAX_PARTS_TO_PROCESS or total_chars >= MAX_TOTAL_TEXT_CHARS:
                break

            if part.is_multipart():
                continue

            processed_parts += 1
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue

            transfer_encoding = part.get("Content-Transfer-Encoding", "")
            payload = part.get_payload(decode=False)

            if not isinstance(payload, str):
                continue

            payload = _truncate_text(payload, MAX_TEXT_CHARS_PER_PART)
            decoded = _decode_payload(payload, transfer_encoding)

            if content_type == "text/plain":
                append_part(decoded)
            elif content_type == "text/html":
                append_part(_html_to_text(decoded))
    else:
        content_type = message.get_content_type()
        if content_type in {"text/plain", "text/html"}:
            transfer_encoding = message.get("Content-Transfer-Encoding", "")
            payload = message.get_payload(decode=False)

            if isinstance(payload, str):
                payload = _truncate_text(payload, MAX_TEXT_CHARS_PER_PART)
                decoded = _decode_payload(payload, transfer_encoding)
                if content_type == "text/html":
                    append_part(_html_to_text(decoded))
                else:
                    append_part(decoded)

    if not collected_parts:
        separator_match = re.search(r"\r?\n\r?\n", raw_email)
        if separator_match:
            fallback_body = raw_email[separator_match.end() :]
            append_part(fallback_body)

    return "\n".join(collected_parts)


def _normalize_text(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _should_unsubscribe(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(pattern.search(normalized) for pattern in UNSUBSCRIBE_PATTERNS)


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
async def decide_unsubscribe(
    raw_email: str = Body(..., media_type="text/plain")
) -> DecideUnsubscribeResponse:
    if not isinstance(raw_email, str) or not raw_email:
        raise HTTPException(status_code=400, detail="Request body must be a non-empty text/plain email message.")

    if len(raw_email.encode("utf-8")) > MAX_RAW_EMAIL_BYTES:
        raise HTTPException(status_code=413, detail="Request body too large.")

    try:
        message = _parse_email_once(raw_email)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid email message format.") from exc

    sender_email = _extract_sender_email(message, raw_email)
    if not sender_email:
        raise HTTPException(status_code=400, detail="Unable to extract a valid sender email address.")

    content = _extract_text_content(message, raw_email)
    unsubscribe = _should_unsubscribe(content)

    return DecideUnsubscribeResponse(
        email=sender_email,
        unsubscribe=unsubscribe,
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)