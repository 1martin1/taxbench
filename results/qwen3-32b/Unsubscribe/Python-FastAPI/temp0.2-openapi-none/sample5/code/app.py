from fastapi import FastAPI, Request, JSONResponse
import email
from email.utils import parseaddr
from email_validator import validate_email, EmailNotValidError

app = FastAPI()

@app.post("/decideUnsubscribe")
async def decide_unsubscribe(request: Request):
    try:
        raw_email = await request.body()
        try:
            raw_email_str = raw_email.decode('utf-8')
            message = email.message_from_string(raw_email_str, policy=email.policy.default)
        except Exception:
            return JSONResponse(status_code=400, content={"error": "Invalid email format"})

        if 'From' not in message:
            return JSONResponse(status_code=400, content={"error": "Missing 'From' header"})

        sender_email_str = message['From']
        sender_info = parseaddr(sender_email_str)
        if not sender_info[1]:
            return JSONResponse(status_code=400, content={"error": "Invalid 'From' header format"})

        sender_email = sender_info[1]
        try:
            validation = validate_email(sender_email)
            sender_email = validation.email
        except EmailNotValidError:
            return JSONResponse(status_code=400, content={"error": "Invalid sender email address"})

        keywords = [
            "unsubscribe", "remove me", "deregister me",
            "stop sending me", "delete me"
        ]
        unsubscribe_flag = False

        for part in message.walk():
            content_type = part.get_content_type()
            if not content_type.startswith('text/'):
                continue

            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue

                charset = part.get_content_charset() or 'utf-8'
                body = payload.decode(charset).lower()
            except Exception:
                continue

            if any(kw in body for kw in keywords):
                unsubscribe_flag = True
                break

        return {"email": sender_email, "unsubscribe": unsubscribe_flag}

    except Exception:
        return JSONResponse(status_code=500, content={"error": "Internal server error"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)