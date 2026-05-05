import email
from email.utils import parseaddr
from fastapi import FastAPI, Request, HTTPException
import uvicorn

app = FastAPI()

UNSUBSCRIBE_KEYWORDS = {
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me"
}

def extract_email_address(raw_email: str) -> str:
    message = email.message_from_string(raw_email)
    from_header = message.get("From")
    if not from_header:
        raise ValueError("Missing From header")
    name, email_addr = parseaddr(from_header)
    if not email_addr:
        raise ValueError("Invalid email address in From header")
    return email_addr

def extract_plain_text_content(raw_email: str) -> str:
    message = email.message_from_string(raw_email)
    text_content = ""
    
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                try:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True).decode(charset)
                    text_content += payload
                except Exception:
                    continue
    else:
        if message.get_content_type() == "text/plain":
            try:
                charset = message.get_content_charset() or "utf-8"
                payload = message.get_payload(decode=True).decode(charset)
                text_content = payload
            except Exception:
                pass
    return text_content

def contains_unsubscribe_request(text: str) -> bool:
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in UNSUBSCRIBE_KEYWORDS)

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(request: Request):
    try:
        raw_email = await request.body()
        raw_email_str = raw_email.decode("utf-8")
        
        email_address = extract_email_address(raw_email_str)
        plain_text = extract_plain_text_content(raw_email_str)
        should_unsubscribe = contains_unsubscribe_request(plain_text)
        
        return {"email": email_address, "unsubscribe": should_unsubscribe}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)