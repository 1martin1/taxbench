from fastapi import FastAPI, Body
import email
from email.utils import parseaddr

app = FastAPI()

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email_raw: str = Body(..., media_type="text/plain")):
    # Parse the email message
    msg = email.message_from_string(email_raw, policy=email.policy.default)
    
    # Extract email address from From header
    from_header = msg.get("From", "")
    parsed_from = parseaddr(from_header)
    email_address = parsed_from[1]
    
    # Extract text/plain content
    text_content = ""
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                text_content += payload.decode(charset).lower() + "\n"
            except Exception:
                continue
    
    # Check for unsubscribe keywords
    keywords = [
        "unsubscribe",
        "remove me",
        "deregister me",
        "stop sending me",
        "delete me"
    ]
    unsubscribe = any(keyword in text_content for keyword in keywords)
    
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)