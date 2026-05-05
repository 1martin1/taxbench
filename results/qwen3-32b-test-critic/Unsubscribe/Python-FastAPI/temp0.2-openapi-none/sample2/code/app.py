from fastapi import FastAPI, Request
from email import policy
from email.parser import Parser
from email.utils import parseaddr
from email_validator import validate_email, EmailNotValidError

app = FastAPI()

def has_unsubscribe_phrases(text: str) -> bool:
    phrases = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
    text_lower = text.lower()
    for phrase in phrases:
        if phrase in text_lower:
            return True
    return False

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(request: Request):
    raw_email_bytes = await request.body()
    raw_email_str = raw_email_bytes.decode('utf-8', errors='ignore')
    
    message = Parser(policy=policy.default).parsestr(raw_email_str)
    
    from_header = message.get('From', '')
    email_address = parseaddr(from_header)[1]
    
    # Validate and sanitize email address
    try:
        valid = validate_email(email_address)
        email_address = valid.email  # normalized form
    except EmailNotValidError:
        email_address = ""  # Return empty string for invalid emails
    
    text_content = ""
    for part in message.walk():
        if part.get_content_type() == 'text/plain':
            payload = part.get_payload(decode=True)
            if payload:
                try:
                    text_content += payload.decode(part.get_content_charset() or 'utf-8')
                except:
                    text_content += payload.decode('utf-8', errors='ignore')
    
    unsubscribe = has_unsubscribe_phrases(text_content)
    
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)