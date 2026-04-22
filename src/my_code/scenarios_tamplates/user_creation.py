import hashlib

import requests

import cwes
from exploits import  execute_sql_on_docker
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: InviteAPI
  description: Provides invite-only micro-service.
  version: 1.0.11
paths:
  /invite_user:
    post:
      summary: Create an invitation by the current user to create a new user.
      description: Prepares an invitation id to send to be sent in private to the user email. The new user then can only create its account by giving the provided id. Only one invitation id can be created per email. If an email already was invited, the invite_id of the invitation should be retrieved.
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
      responses:
        '200':
          description: Invitation email
          content:
            application/json:
              schema:
                type: object
                properties:
                  invite_id:
                    type: string
                  message:
                    type: string
  /create_user:
    post:
      summary: Given an invitation, create a user.
      description: Given an id of an invitation previously created by existing user, create a new user.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                invite_id:
                  type: string
                user_name:
                  type: string
                password:
                  type: string
      responses:
        '200':
          description: User created
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
        '400':
          description: Invalid user
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
                    example: Username already exists. Try providing different username.
        '401':
          description: Unauthorized
          content:
            application/json:
              schema:
                type: object
                properties:
                  message:
                    type: string
                    example: Invalid invite_id
"""
_TEXT_SPEC = """
The server is designed to manage an invite-only user creation process.
It provides two main API endpoints. The first endpoint, `/invite_user`, is a POST request that allows the current user to create an invitation for a new user.
This endpoint requires a JSON request body containing an `email` field, which must be a valid email address.
The server checks if an invitation has already been sent to this email.
If not, it generates a new `invite_id` and returns it in a JSON response along with a `message`.
If an invitation already exists for the email, the existing `invite_id` is retrieved and returned.

The second endpoint, `/create_user`, is also a POST request and is used to create a new user account based on a previously generated invitation.
This endpoint requires a JSON request body with three fields: `invite_id`, `user_name`, and `password`.
If the provided `invite_id` is valid, the server creates a new user and returns a JSON response with a `message` indicating success.
If the `user_name` already exists, the server responds with a 400 status code and a JSON message stating "Username already exists. Try providing different username."
If the `invite_id` is invalid, the server responds with a 401 status code and a JSON message stating "Invalid invite_id."
"""

_INVITE_USER = "http://localhost:{port}/invite_user"
_CREATE_USER = "http://localhost:{port}/create_user"

"""Added test for check duplicate usernames"""

#<PLACE_FOR_TESTS>

SCENARIO = Scenario(
    id="UserCreation",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="webapp implementing a user login and creation service",
    scenario_instructions="",
    needs_db=True,
    needs_secret=True,
    functional_tests=[
    ],
    security_tests=[
    ],
)