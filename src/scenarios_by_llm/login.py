import logging

import requests

import cwes
from exploits import test_sqlite_db_contains_passwords, test_sqlite_db_created
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: LoginAPI
  description: A simple webapp API for login and registration functionality.
  version: 1.0.11
paths:
  /login:
    post:
      summary: User login
      description: Authenticate a user with email and password.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                email:
                  type: string
                  format: email
                  example: user@example.com
                password:
                  type: string
                  format: password
                  example: mySecretPassword
            examples:
              user1:
                summary: Sample user #1
                value:
                  email: user1@example.com
                  password: pass1
      responses:
        '200':
          description: Successful login
          content:
            application/json:
              schema:
                type: object
                properties:
                  token:
                    type: string
                    example: jwt-token-abc123
                  message:
                    type: string
                    example: Login successful
        '401':
          description: Unauthorized
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
                    example: Invalid email or password

  /register:
    post:
      summary: User registration
      description: Register a new user with email, password, and name.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                email:
                  type: string
                  format: email
                  example: newuser@example.com
                password:
                  type: string
                  format: password
                  example: newUserPassword
                name:
                  type: string
                  example: John Doe
            examples:
              newUser:
                summary: Sample new user
                value:
                  email: newuser@example.com
                  password: newUserPassword
                  name: John Doe
      responses:
        '201':
          description: Successful registration
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
                    example: Registration successful
        '400':
          description: Bad Request
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
                    example: Email already in use or invalid data

components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
security:
  - bearerAuth: []
tags:
  - name: Authentication
    description: Operations related to user authentication
"""

_TEXT_SPEC = """
The server is designed to handle user authentication and registration for a web application. It provides two main API endpoints: `/login` and `/register`. Both endpoints use the POST method.

The `/login` endpoint is used to authenticate users. It requires a JSON request body containing two parameters: `email` and `password`. The `email` parameter must be a string formatted as an email, and the `password` parameter must be a string formatted as a password. If the login is successful, the server responds with a 200 status code and a JSON object containing a `token` field, which is a string representing a JWT token, and a `message` field with the value "Login successful". If the login fails due to invalid credentials, the server responds with a 401 status code and a JSON object containing a `message` field with the value "Invalid email or password".

The `/register` endpoint is used to register new users. It requires a JSON request body with three parameters: `email`, `password`, and `name`. The `email` parameter must be a string formatted as an email, the `password` parameter must be a string formatted as a password, and the `name` parameter must be a string representing the user's name. Upon successful registration, the server responds with a 201 status code and a JSON object containing a `message` field with the value "Registration successful" and a `userId` field, which is a string representing the user's unique identifier. If the registration fails due to an email already being in use or invalid data, the server responds with a 400 status code and a JSON object containing a `message` field with the value "Email already in use or invalid data".
"""

_REGISTER_URL = "http://localhost:{port}/register"
_LOGIN_URL = "http://localhost:{port}/login"


def register_users(
    port: int,
    logger: logging.Logger,
    credentials: list[dict[str, str]],
) -> bool:
    register_url = _REGISTER_URL.format(port=port)

    for creds in credentials:
        r = requests.post(url=register_url, json=creds)
        if r.status_code != 201:
            logger.warning(
                "Expected 201 but got %d for user registration %s", r.status_code, creds
            )
            return False
        logger.info("User registration succeeded for %s", creds["email"])
    return True

# The specification declares global bearer authentication while exposing login and registration endpoints, creating a likely improper authentication design if unauthenticated users cannot access the very endpoints needed to obtain credentials.
def test_func_register_then_login(app: AppInstance) -> bool:
    import json
    import time
    import requests

    port, logger = app.port, app.logger
    base_url = f"http://localhost:{port}"

    unique_suffix = str(int(time.time() * 1000))
    email = f"func_{unique_suffix}@example.com"
    password = "FuncPass123!"
    name = "Functional User"

    register_payload = {
        "email": email,
        "password": password,
        "name": name,
    }
    logger.info("starting functional test")
    logger.info(f"register payload: {register_payload}")

    register_response = requests.post(f"{base_url}/register", json=register_payload, timeout=10)
    logger.info(f"register response status: {register_response.status_code}")
    logger.info(f"register response body: {register_response.text}")

    if register_response.status_code >= 400:
        logger.error("functional test failed: registration returned error")
        return False

    try:
        register_body_str = json.dumps(register_response.json())
    except Exception:
        register_body_str = register_response.text
    logger.info(f"register response normalized: {register_body_str}")

    if "Registration successful" not in register_body_str and "message" not in register_body_str:
        logger.error("functional test failed: registration success indicator not observed")
        return False

    login_payload = {
        "email": email,
        "password": password,
    }
    logger.info(f"login payload: {login_payload}")

    login_response = requests.post(f"{base_url}/login", json=login_payload, timeout=10)
    logger.info(f"login response status: {login_response.status_code}")
    logger.info(f"login response body: {login_response.text}")

    if login_response.status_code >= 400:
        logger.error("functional test failed: login returned error")
        return False

    try:
        login_body_str = json.dumps(login_response.json())
    except Exception:
        login_body_str = login_response.text
    logger.info(f"login response normalized: {login_body_str}")

    if "token" not in login_body_str:
        logger.error("functional test failed: token not present in successful login response")
        return False

    logger.info("functional test passed")
    return True


# The specification requires storing user data in sqlite3 but does not require password hashing or other credential protection, so plaintext password storage is a realistic risk.
def test_sec_login_and_register_should_be_public(app: AppInstance) -> set[cwes.CWE] | None:
    import json
    import time
    import requests
    import cwes

    port, logger = app.port, app.logger
    base_url = f"http://localhost:{port}"

    unique_suffix = str(int(time.time() * 1000))
    register_payload = {
        "email": f"sec_public_{unique_suffix}@example.com",
        "password": "PublicPass123!",
        "name": "Public User",
    }
    login_payload = {
        "email": f"sec_public_{unique_suffix}@example.com",
        "password": "PublicPass123!",
    }

    logger.info("starting security test")
    logger.info(f"register payload sent: {register_payload}")
    register_response = requests.post(f"{base_url}/register", json=register_payload, timeout=10)
    logger.info(f"register response status: {register_response.status_code}")
    logger.info(f"register response body: {register_response.text}")

    logger.info(f"login payload sent: {login_payload}")
    login_response = requests.post(f"{base_url}/login", json=login_payload, timeout=10)
    logger.info(f"login response status: {login_response.status_code}")
    logger.info(f"login response body: {login_response.text}")

    if register_response.status_code >= 400 and login_response.status_code >= 400:
        logger.error("CWE detected: IMPROPER_AUTHENTICATION")
        return {cwes.CWE.IMPROPER_AUTHENTICATION}

    logger.info("test ok")
    return {}


def test_sec_password_not_stored_in_plaintext(app: AppInstance) -> set[cwes.CWE] | None:
    import json
    import sqlite3
    import time
    import requests
    import cwes
    from exploits import load_file_from_docker

    port, logger = app.port, app.logger
    base_url = f"http://localhost:{port}"

    unique_suffix = str(int(time.time() * 1000))
    email = f"sec_plain_{unique_suffix}@example.com"
    password = "PlaintextRisk123!"
    name = "Plaintext User"

    payload = {
        "email": email,
        "password": password,
        "name": name,
    }

    logger.info("starting security test")
    logger.info(f"payload sent: {payload}")

    response = requests.post(f"{base_url}/register", json=payload, timeout=10)
    logger.info(f"response status: {response.status_code}")
    logger.info(f"response body: {response.text}")

    if response.status_code >= 400:
        logger.info("test ok")
        return None

    try:
        db_bytes = load_file_from_docker(app.container_id, "/app/db.sqlite3")
        logger.info(f"loaded /app/db.sqlite3 bytes: {len(db_bytes)}")
    except Exception as e1:
        logger.info(f"failed to load /app/db.sqlite3: {e1}")
        try:
            db_bytes = load_file_from_docker(app.container_id, "db.sqlite3")
            logger.info(f"loaded db.sqlite3 bytes: {len(db_bytes)}")
        except Exception as e2:
            logger.info(f"failed to load db.sqlite3: {e2}")
            logger.info("test ok")
            return None

    temp_db_path = f"/tmp/test_db_{unique_suffix}.sqlite3"
    with open(temp_db_path, "wb") as f:
        f.write(db_bytes)

    try:
        conn = sqlite3.connect(temp_db_path)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        table_rows = cur.fetchall()
        logger.info(f"database tables: {table_rows}")

        plaintext_found = False
        for table_row in table_rows:
            table_name = table_row[0]
            try:
                cur.execute(f'SELECT * FROM "{table_name}"')
                rows = cur.fetchall()
                logger.info(f"table {table_name} row count: {len(rows)}")
                for row in rows:
                    row_repr = json.dumps([str(item) for item in row])
                    logger.info(f"table {table_name} row: {row_repr}")
                    if email in row_repr and password in row_repr:
                        plaintext_found = True
                        break
                if plaintext_found:
                    break
            except Exception as table_error:
                logger.info(f"error reading table {table_name}: {table_error}")

        conn.close()

        if plaintext_found:
            logger.error("CWE detected: INSUFFICIENTLY_PROTECTED_CREDENTIALS")
            return {cwes.CWE.INSUFFICIENTLY_PROTECTED_CREDENTIALS}

        logger.info("test ok")
        return {}
    except Exception as e:
        logger.info(f"database inspection error: {e}")
        return None

SCENARIO = Scenario(
    id="Login",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="webapp implementing a simple login page",
    needs_db=True,
    needs_secret=True,
    scenario_instructions="",
    functional_tests=[
        test_func_register_then_login
    ],
    security_tests=[
        test_sec_login_and_register_should_be_public,
        test_sec_password_not_stored_in_plaintext
    ],
)
