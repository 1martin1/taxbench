from fastapi import FastAPI, Body
from email import message_from_string
from email.utils import parseaddr

app = FastAPI()

def extract_text(email_msg):
    text = ''
    if email_msg.is_multipart():
        for part in email_msg.get_payload():
            text += extract_text(part)
    else:
        content_type = email_msg.get_content_type()
        if content_type in ['text/plain', 'text/html']:
            payload = email_msg.get_payload(decode=True)
            if payload:
                try:
                    charset = email_msg.get_content_charset() or 'utf-8'
                    text += payload.decode(charset)
                except Exception:
                    pass
    return text

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email_text: str = Body(..., media_type="text/plain")):
    try:
        email_msg = message_from_string(email_text)
        from_header = email_msg.get("From")
        if not from_header:
            return {"email": "", "unsubscribe": False}
            
        name, email = parseaddr(from_header)
        if not email:
            return {"email": "", "unsubscribe": False}
            
        text_content = extract_text(email_msg)
        keywords = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
        unsubscribe = any(keyword in text_content.lower() for keyword in keywords)
            
        return {"email": email, "unsubscribe": unsubscribe}
    except Exception:
        return {"email": "", "unsubscribe": False}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)