from fastapi import FastAPI, Body, HTTPException
from email import policy
from email.parser import Parser
from email.utils import parseaddr

app = FastAPI()

def contains_unsubscribe_keywords(text):
    keywords = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
    text_lower = text.lower()
    for keyword in keywords:
        if keyword in text_lower:
            return True
    return False

def check_email_body(msg):
    for part in msg.walk():
        content_type = part.get_content_type()
        if content_type.startswith('text/'):
            try:
                charset = part.get_content_charset() or 'utf-8'
                payload = part.get_payload(decode=True).decode(charset)
                if contains_unsubscribe_keywords(payload):
                    return True
            except Exception:
                continue
    return False

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email_raw: str = Body(..., media_type='text/plain')):
    msg = Parser(policy=policy.default).parsestr(email_raw)
    from_header = msg['From']
    if not from_header:
        raise HTTPException(status_code=400, detail="Missing From header")
    email = parseaddr(from_header)[1]
    if not email:
        raise HTTPException(status_code=400, detail="Invalid From header")
    unsubscribe = check_email_body(msg)
    return {"email": email, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)