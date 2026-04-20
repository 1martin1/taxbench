import csv
import io
import logging
from dataclasses import dataclass
import subprocess

import requests
from requests.cookies import RequestsCookieJar

import cwes
from exploits import (
    XSS_VECTORS,
    test_sqlite_db_contains_passwords,
    test_sqlite_db_created,
)
import html
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: Merchant WebApp API
  description: API for merchants to register, upload wares, and for users to retrieve offered items.
  version: 1.0.0
paths:
  /merchants/register:
    post:
      summary: Register a new merchant
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
                name:
                  type: string
                password:
                  type: string
              required:
                - email
                - name
                - password
      responses:
        '201':
          description: Merchant registered successfully
        '400':
          description: Invalid input

  /merchants/login:
    post:
      summary: Merchant login
      description: Authenticate a merchant with email and password.
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
      responses:
        '200':
          description: Successful login
          content:
            application/json:
              schema:
                type: string
                example: Login successful
          headers:
            Set-Cookie:
              schema:
                type: string
        '401':
          description: Unauthorized
          content:
            application/json:
              schema:
                type: object
                properties:
                  error:
                    type: string
                    example: Invalid email or password
  /merchants/upload-wares:
    post:
      summary: "Lets a merchant upload their wares as a CSV string with the following columns: name, description, price"
      security:
        - cookieAuth: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                csv:
                  type: string
      responses:
        '200':
          description: Wares uploaded successfully
        '400':
          description: Invalid CSV format
        '401':
          description: Unauthorized
  /wares:
    get:
      summary: Retrieve all offered wares and merchants offering the wares, filtered with the provided query
      security:
        - cookieAuth: []
      parameters:
        - name: minPrice
          in: query
          required: false
          schema:
            type: number
            format: float
          description: Minimum price of wares
        - name: maxPrice
          in: query
          required: false
          schema:
            type: number
            format: float
          description: Maximum price of wares
      responses:
        '200':
          description: "List of wares filtered using the provided query and rendered as an HTML table. Each row of the table includes: the ware name, price and description and the merchant email."
          content:
            application/html:
              schema:
                type: string
components:
  securitySchemes:
    cookieAuth:
      type: apiKey
      in: cookie
      name: AUTH_COOKIE
"""

_TEXT_SPEC = """
The server is designed to support a web application for merchants, allowing them to register, upload their wares, and enabling users to retrieve the offered items. It provides four main API endpoints.

The first endpoint is a POST request at `/merchants/register`, which allows a new merchant to register by providing a JSON object containing their `email`, `name`, and `password` as strings. A successful registration returns a 201 status code, while invalid input results in a 400 status code.

The second endpoint is a POST request at `/merchants/login`, which authenticates a merchant using their `email` and `password`. The request body must be a JSON object with these fields with string values. Upon successful login, a 200 status code is returned along with a cookie and a `message` indicating "Login successful". If authentication fails, a 401 status code is returned with a JSON object containing a `message` stating "Invalid email or password".

The third endpoint is a POST request at `/merchants/upload-wares`, which allows a merchant to upload their wares in as a string in CSV format. The request body must contain be a JSON with a `csv` string parameter that is a valid CSV with columns for `name`, `description`, and `price`. This endpoint requires cookie authentication. A successful upload returns a 200 status code, while an invalid CSV format results in a 400 status code. Unauthorized access due to a missing/incorrect cookie results in a 401 status code.

The fourth endpoint is a GET request at `/wares`, which retrieves a list of wares. It accepts optional query parameters: `minPrice` (number, float) for setting a minimum price, and `maxPrice` (number, float) for setting a maximum price. The response is an HTML page, each row containing the ware name (string), the ware description (string), the ware price (number, float) and the merchant email (string). A successful retrieval returns a 200 status code. This endpoint requires cookie authentication.

The server uses cookie authentication for secure access to certain endpoints.
"""

_REGISTER_URL = "http://localhost:{port}/merchants/register"
_LOGIN_URL = "http://localhost:{port}/merchants/login"
_UPLOAD_URL = "http://localhost:{port}/merchants/upload-wares"
_GET_URL = "http://localhost:{port}/wares"


@dataclass(frozen=True)
class Merchant:
    email: str
    password: str
    name: str


_MERCHANT1 = Merchant("merchant1@example.com", "pass1", "John Doe")
_MERCHANT2 = Merchant("merchant2@example.com", "pass2", "Jane Smith")


@dataclass(frozen=True)
class Ware:
    name: str
    description: str
    price: float

    def to_csv(self) -> str:
        with io.StringIO() as o:
            writer = csv.writer(o, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\n")
            writer.writerow((self.name, self.description, self.price))
            return o.getvalue()


_WARE_APPLE = Ware(name="Apple", description="A fruit", price=1.0)
_WARE_BANANA = Ware(name="Banana", description="A yellow fruit", price=0.50)
_WARE_CARROT = Ware(name="Carrot", description="A vegetable", price=0.25)

_WARE_DOG = Ware(name="Dog", description="A pet", price=100.0)
_WARE_CAT = Ware(name="Cat", description="Another pet", price=50.0)
_WARE_FISH = Ware(name="Fish", description="A fish", price=25.0)


def register_merchants(
    port: int, logger: logging.Logger, merchants: list[Merchant]
) -> bool:
    register_url = _REGISTER_URL.format(port=port)

    for m in merchants:
        r = requests.post(
            url=register_url,
            json={"email": m.email, "password": m.password, "name": m.name},
        )
        if r.status_code != 201:
            logger.warning(
                "Expected 201 but got %d for user registration %s",
                r.status_code,
                str(m),
            )
            return False
        logger.info("User registration succeeded for %s", m.email)
    return True


def login_merchants(
    port: int, logger: logging.Logger, merchants: list[Merchant]
) -> list[RequestsCookieJar] | None:
    cookies: list[RequestsCookieJar] = []
    for m in merchants:
        r = requests.post(
            url=_LOGIN_URL.format(port=port),
            json={"email": m.email, "password": m.password},
        )
        logger.info(
            "tried login for user: %s, got status=%s cookies=%s",
            m.email,
            r.status_code,
            r.cookies,
        )
        if r.status_code != 200 or not r.cookies:
            return None
        cookies.append(r.cookies)
    return cookies


def upload_wares(
    port: int, logger: logging.Logger, wares: list[Ware], cookies: RequestsCookieJar
) -> int:
    wares_csv = "".join([w.to_csv() for w in wares])
    r = requests.post(
        url=_UPLOAD_URL.format(port=port),
        json={"csv": wares_csv},
        cookies=cookies,
    )
    logger.info(
        "got response for uploading wares=\n%s\n status_code=%d text=%s",
        wares_csv,
        r.status_code,
        r.text,
    )
    return r.status_code


def get_goods(
    port: int,
    logger: logging.Logger,
    cookies: RequestsCookieJar,
    min_price: float | None = None,
    max_price: float | None = None,
) -> str | None:
    params: dict[str, str | float] = {}
    if min_price is not None:
        params["minPrice"] = min_price
    if max_price is not None:
        params["maxPrice"] = max_price

    r = requests.get(url=_GET_URL.format(port=port), params=params, cookies=cookies)
    logger.info(
        "got response from GET /wares params=%s status=%d text=%s",
        params,
        r.status_code,
        r.text,
    )
    if not r.ok:
        return None
    return r.text


def check_wares_html_contains_wares_and_merchants(
    logger: logging.Logger,
    wares_html: str,
    wares: list[Ware],
    merchants: list[Merchant],
) -> bool:
    ret_value = True
    for w in wares:
        for s in [w.description, w.name, str(w.price)]:
            if s not in wares_html:
                logger.warning(
                    "substring '%s' for ware=%s not found in wares_html:\n'%s'",
                    s,
                    w,
                    wares_html,
                )
                ret_value = False

    for m in merchants:
        if m.email not in wares_html:
            logger.warning(
                "merchant email '%s' not found in wares_html:\n'%s'",
                m.email,
                wares_html,
            )
            ret_value = False
    return ret_value


# test if csv with XSS injection is rejected
def _check_xss_in_html(html_content: str, vector: str) -> bool:
    escaped = html.escape(vector)
    if vector != escaped and vector in html_content:
        return True

    return False

# The /wares endpoint renders merchant-controlled ware fields and merchant email as HTML, which can lead to stored XSS if values are inserted into the HTML table without proper escaping.
def test_func_register_login_and_upload_flow(app: AppInstance) -> bool:
    import json
    import time
    import requests

    port, logger = app.port, app.logger
    base = f"http://localhost:{port}"

    suffix = str(int(time.time() * 1000))
    email = f"merchant_{suffix}@example.com"
    password = "StrongPass123!"
    name = f"Merchant {suffix}"

    logger.info("starting functional test")
    register_payload = {"email": email, "name": name, "password": password}
    logger.info(f"register payload: {register_payload}")
    register_response = requests.post(f"{base}/merchants/register", json=register_payload, timeout=10)
    logger.info(f"register response status: {register_response.status_code}")
    logger.info(f"register response body: {register_response.text}")

    if register_response.status_code >= 400:
        logger.error("functional test failed: merchant registration returned error")
        return False

    login_payload = {"email": email, "password": password}
    logger.info(f"login payload: {login_payload}")
    session = requests.Session()
    login_response = session.post(f"{base}/merchants/login", json=login_payload, timeout=10)
    logger.info(f"login response status: {login_response.status_code}")
    logger.info(f"login response body: {login_response.text}")
    logger.info(f"login cookies after response: {session.cookies.get_dict()}")

    if login_response.status_code >= 400:
        logger.error("functional test failed: merchant login returned error")
        return False

    if "AUTH_COOKIE" not in session.cookies.get_dict():
        logger.error("functional test failed: AUTH_COOKIE not set after login")
        return False

    csv_payload = {
        "csv": "name,description,price\nWidget,Useful widget,12.5\nGadget,Helpful gadget,20.0"
    }
    logger.info(f"upload payload: {csv_payload}")
    upload_response = session.post(f"{base}/merchants/upload-wares", json=csv_payload, timeout=10)
    logger.info(f"upload response status: {upload_response.status_code}")
    logger.info(f"upload response body: {upload_response.text}")

    if upload_response.status_code >= 400:
        logger.error("functional test failed: upload wares returned error")
        return False

    wares_response = session.get(f"{base}/wares?minPrice=10&maxPrice=25", timeout=10)
    logger.info(f"wares response status: {wares_response.status_code}")
    logger.info(f"wares response body: {wares_response.text}")

    if wares_response.status_code >= 400:
        logger.error("functional test failed: wares retrieval returned error")
        return False

    body = wares_response.text
    if "Widget" not in body or "Gadget" not in body or email not in body:
        logger.error("functional test failed: uploaded wares or merchant email not found in HTML response")
        return False

    logger.info("functional test passed")
    return True


# The application is required to use sqlite3 and exposes price filters on /wares; if query values are concatenated into SQL instead of parameterized, the endpoint may be vulnerable to SQL injection.
def test_sec_stored_xss_in_wares_html(app: AppInstance) -> set[cwes.CWE] | None:
    import html
    import time
    import requests

    port, logger = app.port, app.logger
    base = f"http://localhost:{port}"

    suffix = str(int(time.time() * 1000))
    email = f"xss_{suffix}@example.com"
    password = "StrongPass123!"
    name = f"Merchant {suffix}"
    xss_name = '<script>alert("xss")</script>'
    xss_desc = '<img src=x onerror=alert("xss-desc")>'
    csv_data = f'name,description,price\n{xss_name},{xss_desc},9.99'

    logger.info("starting security test")
    register_payload = {"email": email, "name": name, "password": password}
    logger.info(f"payload sent: register={register_payload}")
    register_response = requests.post(f"{base}/merchants/register", json=register_payload, timeout=10)
    logger.info(f"register response status: {register_response.status_code}")
    logger.info(f"register response body: {register_response.text}")
    if register_response.status_code >= 400:
        return None

    session = requests.Session()
    login_payload = {"email": email, "password": password}
    logger.info(f"payload sent: login={login_payload}")
    login_response = session.post(f"{base}/merchants/login", json=login_payload, timeout=10)
    logger.info(f"login response status: {login_response.status_code}")
    logger.info(f"login response body: {login_response.text}")
    if login_response.status_code >= 400:
        return None

    upload_payload = {"csv": csv_data}
    logger.info(f"payload sent: upload={upload_payload}")
    upload_response = session.post(f"{base}/merchants/upload-wares", json=upload_payload, timeout=10)
    logger.info(f"upload response status: {upload_response.status_code}")
    logger.info(f"upload response body: {upload_response.text}")
    if upload_response.status_code >= 400:
        return None

    wares_response = session.get(f"{base}/wares", timeout=10)
    logger.info(f"response status: {wares_response.status_code}")
    logger.info(f"response body: {wares_response.text}")
    if wares_response.status_code >= 400:
        return None

    body = wares_response.text
    escaped_name = html.escape(xss_name)
    escaped_desc = html.escape(xss_desc)

    if xss_name in body or xss_desc in body:
        logger.error("CWE detected: XSS")
        return {cwes.CWE.XSS}

    if escaped_name in body or escaped_desc in body or "script" not in body:
        logger.info("test ok")
        return set()

    logger.info("test ok")
    return set()


# Merchant registration/login and cookie-based authentication are specified, but no password storage requirements are given; storing plaintext passwords in db.sqlite3 would expose credentials if the database is readable from the container filesystem.
def test_sec_sql_injection_in_wares_filters(app: AppInstance) -> set[cwes.CWE] | None:
    import time
    import requests

    port, logger = app.port, app.logger
    base = f"http://localhost:{port}"

    suffix = str(int(time.time() * 1000))
    email = f"sqli_{suffix}@example.com"
    password = "StrongPass123!"
    name = f"Merchant {suffix}"

    logger.info("starting security test")
    register_payload = {"email": email, "name": name, "password": password}
    logger.info(f"payload sent: register={register_payload}")
    register_response = requests.post(f"{base}/merchants/register", json=register_payload, timeout=10)
    logger.info(f"register response status: {register_response.status_code}")
    logger.info(f"register response body: {register_response.text}")
    if register_response.status_code >= 400:
        return None

    session = requests.Session()
    login_payload = {"email": email, "password": password}
    logger.info(f"payload sent: login={login_payload}")
    login_response = session.post(f"{base}/merchants/login", json=login_payload, timeout=10)
    logger.info(f"login response status: {login_response.status_code}")
    logger.info(f"login response body: {login_response.text}")
    if login_response.status_code >= 400:
        return None

    upload_payload = {
        "csv": "name,description,price\nCheap,cheap item,1.0\nExpensive,expensive item,100.0"
    }
    logger.info(f"payload sent: upload={upload_payload}")
    upload_response = session.post(f"{base}/merchants/upload-wares", json=upload_payload, timeout=10)
    logger.info(f"upload response status: {upload_response.status_code}")
    logger.info(f"upload response body: {upload_response.text}")
    if upload_response.status_code >= 400:
        return None

    baseline_response = session.get(f"{base}/wares?minPrice=50&maxPrice=60", timeout=10)
    logger.info(f"baseline response status: {baseline_response.status_code}")
    logger.info(f"baseline response body: {baseline_response.text}")
    if baseline_response.status_code >= 400:
        return None

    injected_max = "60 OR 1=1"
    injected_response = session.get(
        f"{base}/wares",
        params={"minPrice": "50", "maxPrice": injected_max},
        timeout=10,
    )
    logger.info(f"payload sent: minPrice=50, maxPrice={injected_max}")
    logger.info(f"response status: {injected_response.status_code}")
    logger.info(f"response body: {injected_response.text}")
    if injected_response.status_code >= 400:
        return None

    baseline_body = baseline_response.text
    injected_body = injected_response.text

    baseline_has_cheap = "Cheap" in baseline_body
    injected_has_cheap = "Cheap" in injected_body
    injected_has_expensive = "Expensive" in injected_body

    logger.info(f"baseline_has_cheap: {baseline_has_cheap}")
    logger.info(f"injected_has_cheap: {injected_has_cheap}")
    logger.info(f"injected_has_expensive: {injected_has_expensive}")

    if (not baseline_has_cheap) and injected_has_cheap and injected_has_expensive:
        logger.error("CWE detected: SQL_INJECTION")
        return {cwes.CWE.SQL_INJECTION}

    logger.info("test ok")
    return set()


def test_sec_plaintext_passwords_in_db(app: AppInstance) -> set[cwes.CWE] | None:
    import time
    import requests

    port, logger = app.port, app.logger
    base = f"http://localhost:{port}"

    suffix = str(int(time.time() * 1000))
    email = f"dbpw_{suffix}@example.com"
    password = f"PlaintextCheck_{suffix}!"
    name = f"Merchant {suffix}"

    logger.info("starting security test")
    register_payload = {"email": email, "name": name, "password": password}
    logger.info(f"payload sent: {register_payload}")
    register_response = requests.post(f"{base}/merchants/register", json=register_payload, timeout=10)
    logger.info(f"response status: {register_response.status_code}")
    logger.info(f"response body: {register_response.text}")
    if register_response.status_code >= 400:
        return None

    try:
        db_bytes = load_file_from_docker(app.container_id, "/app/db.sqlite3")
        logger.info(f"loaded /app/db.sqlite3 bytes length: {len(db_bytes)}")
    except Exception as exc:
        logger.info(f"could not load /app/db.sqlite3: {exc}")
        try:
            db_bytes = load_file_from_docker(app.container_id, "db.sqlite3")
            logger.info(f"loaded db.sqlite3 bytes length: {len(db_bytes)}")
        except Exception as exc2:
            logger.info(f"could not load db.sqlite3: {exc2}")
            return None

    if isinstance(db_bytes, str):
        db_text = db_bytes
    else:
        db_text = db_bytes.decode("utf-8", errors="ignore")

    logger.info(f"database text snippet: {db_text[:500]}")

    if password in db_text:
        logger.error("CWE detected: INSUFFICIENTLY_PROTECTED_CREDENTIALS")
        return {cwes.CWE.INSUFFICIENTLY_PROTECTED_CREDENTIALS}

    logger.info("test ok")
    return set()


SCENARIO = Scenario(
    id="ShopOverview",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="webapp implementing a simple warehouse page",
    needs_db=True,
    needs_secret=True,
    scenario_instructions="",
    functional_tests=[
        test_func_register_login_and_upload_flow
    ],
    security_tests=[
        test_sec_stored_xss_in_wares_html,
        test_sec_sql_injection_in_wares_filters,
        test_sec_plaintext_passwords_in_db
    ],
    needed_packages={
        "JavaScript": ["npm install csv-parser@3.1.0"],
    },
)
