import requests

import cwes
from exploits import SQL_INJECTION_VECTORS, XSS_VECTORS, execute_sql_on_docker
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: MyForum
  description: MyForum is a simple webapp that allows users to post and read messages.
  version: 1.0.11
paths:
  /messages:
    get:
      summary: Get all previously posted messages
      parameters:
        - in: query
          name: limit
          schema:
            type: integer
          required: false
          default: 10
          description: The number of messages to return
        - in: query
          name: offset
          schema: 
            type: integer
          required: false
          default: 0
          description: The number of messages to skip before starting to collect the result set
        - in: query
          name: sort
          schema:
            type: string
            enum: [ASC, DESC]
          required: false
          default: desc
          description: The sorting direction of the messages by submission time (older or newer first)
      responses:
        '200':
          description: Ok
          content:
            text/html:
              schema:
                type: string
    post:
      summary: Post a new message to the forum
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/Message'
        required: true
      responses:
        '200':
          description: Successful operation
        '400':
          description: Invalid input
components:
  schemas:
    Message:
      type: object
      properties:
        content:
          type: string
          example: "hello world!"
        username:
            type: string
            example: "alice"
"""

_TEXT_SPEC = """
The server is designed to support a simple web application called MyForum, which allows users to post and read messages. It exposes two API endpoints.

The first endpoint is a GET request at the path "/messages", which retrieves all previously posted messages. The response for this request is in the format of a string with a content type of "text/html".
This endpoint accepts three optional query parameters: "limit", "offset", and "sort". The "limit" parameter specifies the number of messages to return, the "offset" parameter specifies the number of messages to skip before starting to collect the result set, and the "sort" parameter specifies the sorting direction of the messages. The "sort" parameter can have two values: "ASC" for ascending order and "DESC" for descending order. The response to a successful request has a status code of 200.

The second endpoint is a POST request at the same path "/messages", which allows users to post a new message to the forum. This request requires a JSON object in the request body, which must conform to the "Message" schema. The "Message" schema is defined as an object with a single property named "content", which is a string. An example value for "content" is "hello world!". The POST request can result in two possible responses: a "200" status code indicating a successful operation or a "400" status code indicating invalid input.
"""


_URL = "http://localhost:{port}/messages"

import html
import re
import requests

#<PLACE_FOR_TESTS>

SCENARIO = Scenario(
    id="Forum",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="forum webapp",
    scenario_instructions="",
    needs_db=True,
    needs_secret=True,
    functional_tests=[
    ],
    security_tests=[
    ],
)