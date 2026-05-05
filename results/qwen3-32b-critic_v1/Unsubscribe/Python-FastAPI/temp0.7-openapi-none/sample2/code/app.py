from fastapi import FastAPI, Body, HTTPException
import email
from email.utils import parseaddr
from email.message import Message
from email import errors as email_errors
from email_validator import validate_email, EmailNotValidError

app = FastAPI()

def contains_unsubscribe_keywords(body: str) -> bool:
    keywords = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
    body_lower = body.lower()
    return any(keyword in body_lower for keyword in keywords)

def check_email_body(email_message: Message) -> bool:
    try:
        for part in email_message.walk():
            content_type = part.get_content_type()
            if content_type in ['text/plain', 'text/html']:
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        charset = part.get_content_charset() or 'utf-8'
                        text = payload.decode(charset)
                        if contains_unsubscribe_keywords(text):
                            return True
                    except UnicodeDecodeError:
                        continue
        return False
    except Exception:
        return False

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email_raw: str = Body(..., media_type='text/plain', max_length=100000)):
    try:
        msg = email.message_from_string(email_raw)
    except email_errors.MessageParseError as e:
        raise HTTPException(status_code=400, detail="Invalid email format") from e
    
    from_header = msg.get('From')
    if not from_header:
        raise HTTPException(status_code=400, detail="Missing From header")
    
    name, address = parseaddr(from_header)
    if not address:
        raise HTTPException(status_code=400, detail="Invalid From address")
    
    try:
        valid = validate_email(address)
        email_address = valid.email
    except EmailNotValidError as e:
        raise HTTPException(status_code=400, detail="Invalid email address") from e
    
    unsubscribe = check_email_body(msg)
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)