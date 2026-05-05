from email import message_from_string
from email.errors import MessageError
from email.message import Message
from email.utils import parseaddr
from html.parser import HTMLParser
import re
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn


MAX_RAW_EMAIL_BYTES = 256 * 1024
MAX_PARTS = 64
MAX_EXTRACTED_TEXT_CHARS = 64 * 1024
MAX_HTML_INPUT_CHARS = 32 * 1024
MAX_DECODED_PART_CHARS = 16 * 1024


app = FastAPI(
    title="UnsubscribeApp",
    description=(
        "UnsubscribeApp provides a simple endpoint to decide whether a user "
        "requested to be unsubscribed from a mailing list"
    ),
    version="1.0.11",
)


class DecideUnsubscribeResponse(BaseModel):
    email: str
    unsubscribe: bool


UNSUBSCRIBE_PATTERNS = [
    r"\bunsubscribe\b",
    r"\bremove\s+me\b",
    r"\bderegister\s+me\b",
    r"\bstop\s+sending\s+me\b",
    r"\bdelete\s+me\b",
    r"\bplease\s+unsubscribe\s+me\b",
    r"\btake\s+me\s+off\b",
    r"\bopt\s+me\s+out\b",
    r"\bstop\s+emails?\b",
    r"\bno\s+more\s+emails?\b",
]
UNSUBSCRIBE_REGEXES = [re.compile(pattern, re.IGNORECASE) for pattern in UNSUBSCRIBE_PATTERNS]


class _HTMLTextExtractor(HTMLParser):
    def __init__(self, max_output_chars: int) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.current_length = 0
        self.max_output_chars = max_output_chars

    def handle_data(self, data: str) -> None:
        if not data or self.current_length >= self.max_output_chars:
            return
        remaining = self.max_output_chars - self.current_length
        chunk = data[:remaining]
        if chunk:
            self.parts.append(chunk)
            self.current_length += len(chunk)

    def get_text(self) -> str:
        return " ".join(self.parts)


def _safe_message_from_string(raw_email: str) -> Message | None:
    try:
        return message_from_string(raw_email)
    except (MessageError, TypeError, ValueError):
        return None


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def extract_sender_email(raw_email: str) -> str:
    msg = _safe_message_from_string(raw_email)
    if msg is None:
        return ""
    try:
        from_header = msg.get("From", "")
        _, email_address = parseaddr(from_header)
        return email_address.strip()
    except Exception:
        return ""


def extract_text_from_html(html: str) -> str:
    try:
        limited_html = _truncate_text(html, MAX_HTML_INPUT_CHARS)
        parser = _HTMLTextExtractor(max_output_chars=MAX_DECODED_PART_CHARS)
        parser.feed(limited_html)
        parser.close()
        return parser.get_text()
    except Exception:
        return ""


def decode_payload(part: Message) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            raw_payload = part.get_payload()
            if isinstance(raw_payload, str):
                return _truncate_text(raw_payload, MAX_DECODED_PART_CHARS)
            if isinstance(raw_payload, list):
                return ""
            return _truncate_text(str(raw_payload), MAX_DECODED_PART_CHARS)

        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            decoded = payload.decode("utf-8", errors="replace")
        return _truncate_text(decoded, MAX_DECODED_PART_CHARS)
    except Exception:
        return ""


def extract_relevant_text(raw_email: str) -> str:
    msg = _safe_message_from_string(raw_email)
    if msg is None:
        return _truncate_text(raw_email, MAX_EXTRACTED_TEXT_CHARS)

    collected_parts: list[str] = []
    total_chars = 0
    processed_parts = 0

    def append_content(content: str) -> None:
        nonlocal total_chars
        if not content or total_chars >= MAX_EXTRACTED_TEXT_CHARS:
            return
        remaining = MAX_EXTRACTED_TEXT_CHARS - total_chars
        chunk = content[:remaining]
        if chunk:
            collected_parts.append(chunk)
            total_chars += len(chunk)

    try:
        if msg.is_multipart():
            for part in msg.walk():
                if processed_parts >= MAX_PARTS or total_chars >= MAX_EXTRACTED_TEXT_CHARS:
                    break
                if part.is_multipart():
                    continue

                processed_parts += 1
                content_disposition = (part.get("Content-Disposition") or "").lower()
                if "attachment" in content_disposition:
                    continue

                content_type = part.get_content_type()
                content = decode_payload(part)

                if content_type == "text/plain":
                    append_content(content)
                elif content_type == "text/html":
                    append_content(extract_text_from_html(content))
        else:
            content_type = msg.get_content_type()
            content = decode_payload(msg)
            if content_type == "text/plain":
                append_content(content)
            elif content_type == "text/html":
                append_content(extract_text_from_html(content))
            else:
                append_content(_truncate_text(raw_email, MAX_EXTRACTED_TEXT_CHARS))
    except Exception:
        return _truncate_text(raw_email, MAX_EXTRACTED_TEXT_CHARS)

    combined = "\n".join(collected_parts).strip()
    if combined:
        return combined
    return _truncate_text(raw_email, MAX_EXTRACTED_TEXT_CHARS)


def is_unsubscribe_request(text: str) -> bool:
    try:
        normalized = re.sub(r"\s+", " ", _truncate_text(text, MAX_EXTRACTED_TEXT_CHARS).lower())
        return any(pattern.search(normalized) for pattern in UNSUBSCRIBE_REGEXES)
    except Exception:
        return False


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=DecideUnsubscribeResponse(email="", unsubscribe=False).model_dump(),
    )


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    path_item = openapi_schema.get("paths", {}).get("/decideUnsubscribe", {}).get("post", {})
    request_body = path_item.get("requestBody", {})
    content = request_body.get("content", {}).get("text/plain", {})
    content["schema"] = {"type": "string"}
    request_body["content"] = {"text/plain": content}
    path_item["requestBody"] = request_body
    path_item["responses"] = {
        "200": {
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
    }

    openapi_schema["paths"]["/decideUnsubscribe"]["post"] = path_item
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


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
    raw_email: str = Body(..., media_type="text/plain")
) -> DecideUnsubscribeResponse:
    if len(raw_email.encode("utf-8", errors="ignore")) > MAX_RAW_EMAIL_BYTES:
        return DecideUnsubscribeResponse(email="", unsubscribe=False)

    sender_email = extract_sender_email(raw_email)
    if not sender_email:
        return DecideUnsubscribeResponse(email="", unsubscribe=False)

    relevant_text = extract_relevant_text(raw_email)
    unsubscribe = is_unsubscribe_request(relevant_text)
    return DecideUnsubscribeResponse(email=sender_email, unsubscribe=unsubscribe)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)