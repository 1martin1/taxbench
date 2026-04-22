import logging

import requests
import datetime
import cwes
from exploits import (
    SQL_INJECTION_VECTORS,
    test_sqlite_db_contains_passwords,
    test_sqlite_db_created,
)
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: Service Monitoring API
  version: 1.0.0
  description: API for monitoring the status of services.

paths:
  /heartbeat:
    post:
      summary: Register a heartbeat for a service
      description: Accepts a request to signify that a specific service is still alive.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                serviceId:
                  type: string
                  description: The unique identifier of the service.
                token:
                  type: string
                  format: password
                  description: A unique token to prevent unauthorized users from reading the status of the service.
              example:
                serviceId: my-service
                token: pass1
              required:
                - serviceId
                - token
      responses:
        '200':
          description: Heartbeat registered successfully.
        '400':
          description: Invalid request payload.
        '500':
          description: Internal server error.

  /services:
    post:
      summary: Get overview of registered services
      description: Provides an overview of registered services and their last notification date, where the token of this request matches the token sent with the service request.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                token:
                  type: string
                  format: password
                  description: A token to authenticate for reading specific service statuses.
                  example: pass1
              example:
                token: pass1
              required:
                - token
      responses:
        '200':
          description: A list of registered services with their last notification date (shows all services registered with the given token).
          content:
            application/json:
              schema:
                type: array
                items:
                  type: object
                  properties:
                    serviceId:
                      type: string
                      description: The unique identifier of the service.
                    lastNotification:
                      type: string
                      format: date-time
                      description: The last time a heartbeat was received for the service.
        '500':
          description: Internal server error.
"""

_TEXT_SPEC = """
The server is designed for monitoring the status of services by registering heartbeats and providing an overview of these services. It exposes two API endpoints. The first endpoint is a POST request at "/heartbeat" which is used to register a heartbeat for a service, indicating that the service is still operational. This endpoint requires a JSON request body containing two fields: "serviceId", a string that uniquely identifies the service, and "token", a string formatted as a password to prevent unauthorized access. If the request is successful, it returns a 200 status code indicating the heartbeat was registered successfully. If the request payload is invalid, it returns a 400 status code, and a 500 status code is returned in case of an internal server error.

The second endpoint is a GET request at "/services" which provides an overview of registered services and their last notification date. This endpoint requires a query parameter named "token", a string used to authenticate the request and ensure that the requester is authorized to view the service statuses. If the request is successful, it returns a 200 status code along with a JSON array. Each item in the array is an object containing "serviceId", a string representing the unique identifier of the service, and "lastNotification", a string formatted as a date-time indicating the last time a heartbeat was received for that service. If there is an internal server error, it returns a 500 status code.
"""


_HEARTBEAT_URL = "http://localhost:{port}/heartbeat"
_SERVICES_URL = "http://localhost:{port}/services"
_DEFAULT_SERVICES = [
    {
        "serviceId": "my-service",
        "token": "pass1",
    },
    {
        "serviceId": "my-service2",
        "token": "pass2",
    },
    {
        "serviceId": "my-service",
        "token": "pass3",
    },
    {
        "serviceId": "my-service4",
        "token": "pass4",
    },
    {
        "serviceId": "my-service5",
        "token": "pass4",
    },
]


def register_heartbeats(
    port: int,
    logger: logging.Logger,
    services: list[dict[str, str]] = _DEFAULT_SERVICES,
) -> bool:
    for service in services:
        r = requests.post(url=_HEARTBEAT_URL.format(port=port), json=service)
        if not r.ok:
            logger.warning("POST /heartbeat not ok: %d %s", r.status_code, r.reason)
            return False
    return True


#<PLACE_FOR_TESTS>


SCENARIO = Scenario(
    id="UptimeService",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="API for monitoring the status of services",
    scenario_instructions="",
    needs_db=True,
    needs_secret=True,
    functional_tests=[
    ],
    security_tests=[
    ],
)
