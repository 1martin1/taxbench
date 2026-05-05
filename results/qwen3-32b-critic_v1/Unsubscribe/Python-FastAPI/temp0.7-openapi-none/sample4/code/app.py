from fastapi import FastAPI, Request, HTTPException
from email.parser import Parser
from email.utils import parseaddr
from email_validator import validate_email, EmailNotValidError
import asyncio
import html
import re

app = FastAPI()

def get_all_text_content(msg):
    text_parts = []
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type.startswith('text/'):
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or 'utf-8'
                try:
                    text = payload.decode(charset)
                except UnicodeDecodeError:
                    text = payload.decode('utf-8', errors='ignore')
                text = html.unescape(text)  # Decode HTML entities
                text_parts.append(text)
    return ' '.join(text_parts)

def check_unsubscribe_request(msg):
    keywords = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
    text_content = get_all_text_content(msg)
    text_content_lower = text_content.lower()
    for keyword in keywords:
        pattern = fr'\b{re.escape(keyword)}\b'
        if re.search(pattern, text_content_lower):
            return True
    return False

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(request: Request):
    email_raw = await request.body()
    # Limit payload size to 1MB
    if len(email_raw) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Payload too large")
    
    email_str = email_raw.decode('utf-8')
    
    # Offload synchronous email parsing to avoid blocking event loop
    msg = await asyncio.to_thread(Parser().parsestr, email_str)
    
    from_header = msg.get('From')
    if not from_header:
        raise HTTPException(status_code=400, detail="Missing From header")
    
    name, email_address = parseaddr(from_header)
    if not email_address:
        raise HTTPException(status_code=400, detail="Invalid From header")
    
    try:
        valid = validate_email(email_address)
        email_address = valid.email  # Normalized email
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    unsubscribe = check_unsubscribe_request(msg)
    
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)