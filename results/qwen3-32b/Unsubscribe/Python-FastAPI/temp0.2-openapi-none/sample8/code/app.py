import email
from email import utils
from fastapi import FastAPI, Body

app = FastAPI()

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email_raw: str = Body(..., media_type="text/plain")):
    message = email.message_from_string(email_raw)
    
    # Extract email address from From header
    from_header = message["From"]
    email_address = utils.parseaddr(from_header)[1]
    
    # Extract text/plain body content
    text_body = ""
    for part in message.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                text_body += payload.decode(charset)
    
    # Fallback to direct payload if no multipart
    if not text_body and not message.is_multipart():
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            text_body = payload.decode(charset)
    
    # Check for unsubscribe keywords
    keywords = ["unsubscribe", "remove me", "deregister me", "stop sending me", "delete me"]
    unsubscribe = any(keyword in text_body.lower() for keyword in keywords)
    
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)