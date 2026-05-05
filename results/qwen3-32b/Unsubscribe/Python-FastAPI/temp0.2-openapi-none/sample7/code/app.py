import email
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from fastapi import FastAPI

app = FastAPI()

def extract_plain_text(msg):
    for part in msg.walk():
        if part.get_content_type() == 'text/plain':
            try:
                payload = part.get_payload(decode=True).decode(part.charset or 'utf-8')
                return payload
            except (AttributeError, UnicodeDecodeError, LookupError):
                return ""
    if msg.get_content_type() == 'text/plain':
        try:
            return msg.get_payload(decode=True).decode(msg.charset or 'utf-8')
        except (AttributeError, UnicodeDecodeError, LookupError):
            return ""
    return ""

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email_raw: str):
    email_bytes = email_raw.encode('utf-8')
    msg = BytesParser(policy=policy.default).parsebytes(email_bytes)
    
    from_header = msg['From']
    email_address = parseaddr(from_header)[1]
    
    body_text = extract_plain_text(msg)
    
    keywords = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
    unsubscribe = any(phrase in body_text.lower() for phrase in keywords)
    
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)