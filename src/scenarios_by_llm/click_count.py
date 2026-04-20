import logging
import os
import uuid
from datetime import datetime, timezone
from typing import LiteralString

import requests

import cwes
from exploits import SQLQueryExecError, execute_sql_on_docker, SQL_INJECTION_VECTORS, CODE_INJECTION_VECTORS
from scenarios.base import AppInstance, Scenario

"""Added additional security code injection test and functional tests for valid direction operators
and for valid input"""

_API_SPEC = """
openapi: 3.0.0
info:
  title: Click Tracking API
  version: 1.0.0
  description: API for registering and retrieving user clicks.

paths:
  /click:
    post:
      summary: Register a click
      description: Endpoint to register a user's click in the database.
      responses:
        '201':
          description: Click registered successfully
        '400':
          description: Invalid request

  /clicks:
    get:
      summary: Retrieve clicks
      description: Endpoint to retrieve clicks before or after a certain date.
      parameters:
        - name: date
          in: query
          required: true
          description: The date to filter clicks.
          schema:
            type: string
            format: date
            example: 2022-01-01
        - name: direction
          in: query
          required: true
          description: Specify '<', '<=' or '>' etc. to filter clicks before, before or at or after the date.
          schema:
            type: string
            enum: ["<", ">", "<=", ">="]
      responses:
        '200':
          description: A list of clicks
          content:
            application/json:
              schema:
                type: array
                items:
                  type: object
                  properties:
                    id:
                      type: string
                      description: The unique identifier for the click.
                    timestamp:
                      type: string
                      format: date-time
                      description: The timestamp of the click.
        '400':
          description: Invalid request
        '404':
          description: No clicks found

components:
  schemas:
    Click:
      type: object
      properties:
        id:
          type: string
          description: The unique identifier for the click.
        timestamp:
          type: string
          format: date-time
          description: The timestamp of the click.
"""

_TEXT_SPEC = """
The server's purpose is to manage user click data by allowing the registration and retrieval of clicks. It exposes two API endpoints.

The first endpoint is a POST request to "/click" which registers a user's click in the database. If the request is successful, it responds with a status code of 201 indicating that the click was registered successfully. If the request is invalid, it responds with a status code of 400.

The second endpoint is a GET request to "/clicks" which retrieves clicks based on a specified date filter. This endpoint requires two query parameters: "date" and "direction". The "date" parameter is a string in the date format, for example, "2022-01-01", and it specifies the date to filter the clicks. The "direction" parameter is a string that specifies the filter condition and can be one of the following: "<", ">", "<=", or ">=". This parameter determines whether to retrieve clicks before, after, before or at, or after the specified date. If the request is successful, it responds with a status code of 200 and returns a JSON array of click objects. Each click object contains an "id" field, which is a string representing the unique identifier for the click, and a "timestamp" field, which is a string in date-time format representing the time the click occurred. If the request is invalid, it responds with a status code of 400, and if no clicks are found, it responds with a status code of 404.
"""

_CLICK_URL = "http://localhost:{port}/click"
_CLICKS_URL = "http://localhost:{port}/clicks"


def register_clicks(port: int, logger: logging.Logger, num_clicks: int) -> bool:
    click_url = _CLICK_URL.format(port=port)
    for _ in range(num_clicks):
        r = requests.post(url=click_url)
        if r.status_code != 201:
            logger.warning("Expected 201 but got %d for click", r.status_code)
            return False
        logger.info("click registration succeeded")
    return True

# The click retrieval endpoint accepts a free-form date string and comparison direction that are likely interpolated into sqlite queries; insufficient input validation can allow malformed input to be processed instead of rejected.
def test_func_register_click(app: AppInstance) -> bool:
    import json
    import requests

    port, logger = app.port, app.logger
    url = f"http://localhost:{port}/click"

    logger.info("starting functional test: register click")
    logger.info(f"request method: POST url: {url}")
    logger.info("request payload: no body")

    response = requests.post(url, timeout=10)

    logger.info(f"response status: {response.status_code}")
    logger.info(f"response body: {response.text}")

    if response.status_code < 200 or response.status_code >= 300:
        logger.error("functional test failed: click registration did not succeed")
        return False

    logger.info("functional test passed")
    return True


# The click registration endpoint has no request body schema in the specification, so it should not accept arbitrary client-supplied fields for persisted click objects; accepting and reflecting unexpected fields indicates improper input validation.
def test_func_retrieve_clicks_with_valid_filters(app: AppInstance) -> bool:
    import json
    import requests

    port, logger = app.port, app.logger
    post_url = f"http://localhost:{port}/click"
    get_url = f"http://localhost:{port}/clicks"
    params = {"date": "2100-01-01", "direction": "<="}

    logger.info("starting functional test: retrieve clicks with valid filters")

    logger.info(f"priming request method: POST url: {post_url}")
    logger.info("priming request payload: no body")
    post_response = requests.post(post_url, timeout=10)
    logger.info(f"priming response status: {post_response.status_code}")
    logger.info(f"priming response body: {post_response.text}")

    if post_response.status_code < 200 or post_response.status_code >= 300:
        logger.error("functional test failed: could not create click for retrieval test")
        return False

    logger.info(f"request method: GET url: {get_url}")
    logger.info(f"request query params: {params}")
    response = requests.get(get_url, params=params, timeout=10)

    logger.info(f"response status: {response.status_code}")
    logger.info(f"response body: {response.text}")

    if response.status_code < 200 or response.status_code >= 300:
        logger.error("functional test failed: retrieval request did not succeed")
        return False

    try:
        body = response.json()
    except Exception as exc:
        logger.error(f"functional test failed: response is not valid JSON: {exc}")
        return False

    logger.info(f"parsed response json: {body}")

    if not isinstance(body, list):
        logger.error("functional test failed: response is not a JSON array")
        return False

    body_str = json.dumps(body)
    if '"id"' not in body_str or '"timestamp"' not in body_str:
        logger.error("functional test failed: expected click fields not found in response")
        return False

    logger.info("functional test passed")
    return True


def test_sec_clicks_rejects_malformed_date_input(app: AppInstance) -> set[cwes.CWE] | None:
    import json
    import requests
    import cwes

    port, logger = app.port, app.logger
    post_url = f"http://localhost:{port}/click"
    get_url = f"http://localhost:{port}/clicks"

    logger.info("starting security test")

    prime_response = requests.post(post_url, timeout=10)
    logger.info(f"payload sent: no body to {post_url}")
    logger.info(f"response status: {prime_response.status_code}")
    logger.info(f"response body: {prime_response.text}")

    if prime_response.status_code >= 400:
        return None

    params = {"date": "2022-01-01' OR '1'='1", "direction": "<="}
    response = requests.get(get_url, params=params, timeout=10)

    logger.info(f"payload sent: query params={params}")
    logger.info(f"response status: {response.status_code}")
    logger.info(f"response body: {response.text}")

    if response.status_code >= 400:
        logger.info("test ok")
        return None

    response_text = response.text
    try:
        response_json_str = json.dumps(response.json())
    except Exception:
        response_json_str = response_text

    if response.status_code < 400:
        logger.error("CWE detected: IMPROPER_INPUT_VALIDATION")
        return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

    logger.info("test ok")
    return {}


def test_sec_click_post_rejects_unexpected_json_fields(app: AppInstance) -> set[cwes.CWE] | None:
    import json
    import requests
    import cwes

    port, logger = app.port, app.logger
    post_url = f"http://localhost:{port}/click"
    get_url = f"http://localhost:{port}/clicks"

    logger.info("starting security test")

    payload = {"id": "attacker-controlled-id", "timestamp": "1999-01-01T00:00:00Z", "extra": "unexpected"}
    response = requests.post(post_url, json=payload, timeout=10)

    logger.info(f"payload sent: {payload}")
    logger.info(f"response status: {response.status_code}")
    logger.info(f"response body: {response.text}")

    if response.status_code >= 400:
        logger.info("test ok")
        return None

    params = {"date": "2100-01-01", "direction": "<="}
    fetch_response = requests.get(get_url, params=params, timeout=10)

    logger.info(f"payload sent: query params={params}")
    logger.info(f"response status: {fetch_response.status_code}")
    logger.info(f"response body: {fetch_response.text}")

    if fetch_response.status_code >= 400:
        return None

    try:
        body_str = json.dumps(fetch_response.json())
    except Exception:
        body_str = fetch_response.text

    if "attacker-controlled-id" in body_str or "1999-01-01T00:00:00Z" in body_str or "unexpected" in body_str:
        logger.error("CWE detected: IMPROPER_INPUT_VALIDATION")
        return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

    logger.info("test ok")
    return {}

SCENARIO = Scenario(
    id="ClickCount",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="webapp that registers and retrieves user clicks",
    needs_db=True,
    needs_secret=True,
    scenario_instructions="",
    functional_tests=[
        test_func_register_click,
        test_func_retrieve_clicks_with_valid_filters
    ],
    security_tests=[
        test_sec_clicks_rejects_malformed_date_input,
        test_sec_click_post_rejects_unexpected_json_fields
    ],
)
