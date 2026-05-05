import uvicorn
from fastapi import FastAPI, Body, HTTPException
from email import policy
from email.parser import Parser
from email.utils import parseaddr
import asyncio

app = FastAPI()

def parse_email(raw_email: str):
    try:
        message = Parser(policy=policy.default).parsestr(raw_email)
    except Exception:
        # Return dummy message if parsing fails
        from email.message import Message
        message = Message()
        message['From'] = ''
    from_header = message.get('From', '')
    email_address = parseaddr(from_header)[1]
    return message, email_address

def is_unsubscribe_request(message):
    keywords = {
        "unsubscribe", "remove me", "deregister me",
        "stop sending me", "delete me"
    }
    part_count = 0
    max_parts = 10  # Limit MIME parts to prevent deep nesting
    for part in message.walk():
        part_count += 1
        if part_count > max_parts:
            break  # Stop after 10 parts
        content_type = part.get_content_type()
        if content_type in ['text/plain', 'text/html']:
            try:
                payload_bytes = part.get_payload(decode=True)
                if payload_bytes and len(payload_bytes) > 1024 * 100:  # 100KB per part
                    continue  # Skip large payloads
                charset = part.get_content_charset() or 'utf-8'
                try:
                    payload = payload_bytes.decode(charset) if payload_bytes else ''
                except (LookupError, UnicodeDecodeError):
                    payload = payload_bytes.decode('utf-8', errors='ignore') if payload_bytes else ''
                # Truncate to 10,000 characters to avoid long processing
                truncated_payload = payload[:10000]
                if any(kw.lower() in truncated_payload.lower() for kw in keywords):
                    return True
            except Exception:
                continue
    return False

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email_text: str = Body(..., media_type="text/plain", max_length=1024*1024)):
    try:
        async with asyncio.timeout(5):  # 5 seconds timeout for processing
            message, email = parse_email(email_text)
            unsubscribe = is_unsubscribe_request(message)
            return {"email": email, "unsubscribe": unsubscribe}
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="Request processing timed out")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)