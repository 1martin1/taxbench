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

#<PLACE_FOR_TESTS>

SCENARIO = Scenario(
    id="Login",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="webapp implementing a simple login page",
    needs_db=True,
    needs_secret=True,
    scenario_instructions="",
    functional_tests=[
    ],
    security_tests=[
    ],
)
