from email.utils import parseaddr
from email.message import Message
from email import policy
from email.parser import BytesParser
from fastapi import FastAPI, Request, HTTPException
import uvicorn
from email_validator import validate_email, EmailNotValidError

app = FastAPI()

UNSUBSCRIBE_KEYWORDS = {
    "unsubscribe",
    "remove me",
    "deregister me",
    "stop sending me",
    "delete me"
}

def extract_email_address(raw_email: str) -> str:
    message = Message()
    message.set_policy(policy.default)
    message.parsestr(raw_email)
    from_header = message.get("From")
    if not from_header:
        raise ValueError("Missing From header")
    name, email_addr = parseaddr(from_header)
    if not email_addr:
        raise ValueError("Invalid email address in From header")
    
    try:
        valid = validate_email(email_addr)
        return valid.email
    except EmailNotValidError:
        raise ValueError("Invalid email address format")

def extract_plain_text_content(raw_email: str) -> str:
    text_content = ""
    
    try:
        message = Message()
        message.set_policy(policy.default)
        message.parsestr(raw_email)
        
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        payload = part.get_payload(decode=True)
                        charset = part.get_content_charset() or "utf-8"
                        text_content += payload.decode(charset)
                    except Exception:
                        continue
        elif message.get_content_type() == "text/plain":
            try:
                payload = message.get_payload(decode=True)
                charset = message.get_content_charset() or "utf-8"
                text_content = payload.decode(charset)
            except Exception:
                pass
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
        raw_email_str = raw_email.decode("utf-8", errors="ignore")
        
        email_address = extract_email_address(raw_email_str)
        plain_text = extract_plain_text_content(raw_email_str)
        should_unsubscribe = contains_unsubscribe_request(plain_text)
        
        return {"email": email_address, "unsubscribe": should_unsubscribe}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid email format")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)