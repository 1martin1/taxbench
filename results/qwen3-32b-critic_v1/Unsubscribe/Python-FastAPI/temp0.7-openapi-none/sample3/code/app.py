import email
import email.utils
import base64
import quopri
from fastapi import FastAPI, Body

app = FastAPI()

def is_unsubscribe_request(raw_email: str) -> tuple[str, bool]:
    email_address = ""
    is_unsub = False
    try:
        msg = email.message_from_string(raw_email)
        from_header = msg.get("From", "")
        parsed_email = email.utils.parseaddr(from_header)
        email_address = parsed_email[1]
    except:
        return (email_address, is_unsub)
    
    try:
        unsubscribe_phrases = {
            "unsubscribe",
            "remove me",
            "deregister me",
            "stop sending me",
            "delete me",
        }
        
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type in ("text/plain", "text/html"):
                payload_str = part.get_payload(decode=False)
                if len(payload_str) > 1024 * 1024:  # 1MB payload size limit
                    continue
                
                encoding = part.get_content_transfer_encoding().lower()
                decoded_bytes = None
                
                if encoding == 'base64':
                    try:
                        decoded_bytes = base64.b64decode(payload_str)
                    except (base64.binascii.Error, TypeError):
                        continue
                elif encoding == 'quoted-printable':
                    try:
                        payload_bytes = payload_str.encode('utf-8')
                        decoded_bytes = quopri.decodestring(payload_bytes)
                    except:
                        continue
                else:
                    decoded_bytes = payload_str.encode('utf-8')
                
                try:
                    charset = part.get_content_charset() or 'utf-8'
                    payload = decoded_bytes.decode(charset)
                except UnicodeDecodeError:
                    payload = decoded_bytes.decode('utf-8', errors='ignore')
                
                payload_lower = payload.lower()
                if any(phrase in payload_lower for phrase in unsubscribe_phrases):
                    is_unsub = True
                    break
    except:
        pass
    
    return (email_address, is_unsub)

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(email: str = Body(..., media_type="text/plain", max_length=1024 * 1024)):
    email_address, unsubscribe = is_unsubscribe_request(email)
    return {"email": email_address, "unsubscribe": unsubscribe}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)