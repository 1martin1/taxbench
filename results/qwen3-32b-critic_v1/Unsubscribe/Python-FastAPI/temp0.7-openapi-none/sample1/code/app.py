from email import message_from_string
from email import utils
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
import re

app = FastAPI()

class UnsubscribeResponse(BaseModel):
    email: str
    unsubscribe: bool

UNSUBSCRIBE_KEYWORDS = [
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me"
]

MAX_EMAIL_SIZE = 1024 * 1024  # 1MB
MAX_TEXT_LENGTH = 1024 * 1024  # 1MB
MAX_PARTS = 5

def extract_email_address(from_header: str) -> str:
    name, address = utils.parseaddr(from_header)
    if not address:
        raise ValueError("No valid email address found in From header")
    return address

def contains_unsubscribe_request(text: str) -> bool:
    text = text.lower()
    for keyword in UNSUBSCRIBE_KEYWORDS:
        if re.search(r'\b' + re.escape(keyword) + r'\b', text, re.IGNORECASE):
            return True
    return False

def parse_email_content(email_msg):
    text_parts = []
    total_length = 0
    part_count = 0

    for part in email_msg.walk():
        if part_count >= MAX_PARTS:
            break
        content_type = part.get_content_type()
        if content_type in ['text/plain', 'text/html']:
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or 'utf-8'
                try:
                    text = payload.decode(charset).lower()
                    remaining = MAX_TEXT_LENGTH - total_length
                    if remaining <= 0:
                        continue
                    text = text[:remaining]
                    text_parts.append(text)
                    total_length += len(text)
                    part_count += 1
                except UnicodeDecodeError:
                    continue

    return ' '.join(text_parts)

@app.post("/decideUnsubscribe", response_model=UnsubscribeResponse)
async def decide_unsubscribe(email_raw: str):
    if len(email_raw) > MAX_EMAIL_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Email too large")
    
    try:
        email_msg = message_from_string(email_raw)
        from_header = email_msg.get('From')
        if not from_header:
            raise ValueError("Missing From header")
        email_address = extract_email_address(from_header)
        full_text = parse_email_content(email_msg)
        should_unsubscribe = contains_unsubscribe_request(full_text)
        return {"email": email_address, "unsubscribe": should_unsubscribe}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)