from email import policy
from email.parser import Parser
from email.utils import parseaddr
import html
import re

from fastapi import Body, FastAPI
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


def extract_sender_email(raw_email: str) -> str:
    parsed = Parser(policy=policy.default).parsestr(raw_email)
    from_header = parsed.get("From", "")
    _, email_address = parseaddr(from_header)
    return email_address.strip()


def strip_html_tags(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p\s*>", "\n", text)
    text = re.sub(r"(?is)<.*?>", " ", text)
    return html.unescape(text)


def decode_payload(part) -> str:
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


def extract_text_content(raw_email: str) -> str:
    parsed = Parser(policy=policy.default).parsestr(raw_email)
    parts_text = []

    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_maintype() == "multipart":
                continue
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", "")).lower()
            if "attachment" in content_disposition:
                continue
            content = decode_payload(part)
            if content_type == "text/plain":
                parts_text.append(content)
            elif content_type == "text/html":
                parts_text.append(strip_html_tags(content))
    else:
        content_type = parsed.get_content_type()
        content = decode_payload(parsed)
        if content_type == "text/html":
            parts_text.append(strip_html_tags(content))
        else:
            parts_text.append(content)

    combined = "\n".join(parts_text).strip()
    if combined:
        return combined
    return raw_email


def normalize_text(text: str) -> str:
    text = text.lower()
    text = text.replace("=\n", "")
    text = re.sub(r"=([0-9a-f]{2})", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def decide_unsubscribe(text: str) -> bool:
    normalized = normalize_text(text)
    for pattern in UNSUBSCRIBE_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
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
    sender_email = extract_sender_email(raw_email)
    text_content = extract_text_content(raw_email)
    unsubscribe = decide_unsubscribe(text_content)
    return DecideUnsubscribeResponse(email=sender_email, unsubscribe=unsubscribe)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)