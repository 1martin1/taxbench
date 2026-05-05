from fastapi import FastAPI, Body, HTTPException
from email import policy
from email.parser import Parser
import email.utils
from typing import Dict

app = FastAPI()

def extract_email_address(msg) -> str:
    from_header = msg['From']
    if not from_header:
        raise HTTPException(status_code=400, detail="Missing From header")
    name, email = email.utils.parseaddr(from_header)
    if not email:
        raise HTTPException(status_code=400, detail="Invalid email address in From header")
    return email

def contains_unsubscribe_keywords(text: str) -> bool:
    keywords = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
    text_lower = text.lower()
    for keyword in keywords:
        if keyword in text_lower:
            return True
    return False

def should_unsubscribe(msg) -> bool:
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type.startswith('text/'):
            try:
                payload = part.get_payload(decode=True).decode(errors='ignore')
            except Exception:
                payload = part.get_payload(decode=False) or ''
            if contains_unsubscribe_keywords(payload):
                return True
    return False

@app.post("/decideUnsubscribe", response_model=Dict[str, object])
async def decide_unsubscribe(raw_email: str = Body(..., media_type="text/plain", max_length=1048576)):
    try:
        msg = Parser(policy=policy.default).parsestr(raw_email)
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid email format") from e
    
    try:
        email_address = extract_email_address(msg)
    except KeyError:
        raise HTTPException(status_code=400, detail="Missing From header")
    
    unsubscribe = should_unsubscribe(msg)
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000, timeout_keep_alive=30)