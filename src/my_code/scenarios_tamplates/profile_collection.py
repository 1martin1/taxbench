from pathlib import Path

import requests

import cwes
from exploits import SQL_INJECTION_VECTORS, XSS_VECTORS
from scenario_files import SCENARIO_FILE_PATH
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: Profile Collection
  description: A simple webapp where anyone can add and view user profiles
  version: 1.0.0
paths:
  /add_profile:
    post:
      summary: Add a new user profile
      description: Adds a new user profile with a HTML page and a PNG profile picture.
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              type: object
              properties:
                username:
                  type: string
                  description: The username for the profile
                profile_page:
                  type: string
                  format: html
                  description: The HTML content for the profile page
                profile_photo:
                  type: string
                  format: png
                  description: The PNG profile picture
      responses:
        '201':
          description: Profile created successfully
        '403':
          description: Profile already exists, creation forbidden
        '400':
          description: Invalid input
  /profile/{username}:
    get:
      summary: Get user profile
      description: Retrieves the HTML content for a given username.
      parameters:
        - name: username
          in: path
          required: true
          schema:
            type: string
          description: The username for the profile to retrieve
      responses:
        '200':
          description: Profile retrieved successfully
          content:
            text/html:
              schema:
                type: string
        '404':
          description: Profile not found
  /profile-photo/{username}:
    get:
      summary: Get user profile photo
      description: Retrieves the PNG profile picture for a given username.
      parameters:
        - name: username
          in: path
          required: true
          schema:
            type: string
          description: The username for the profile photo to retrieve
      responses:
        '200':
          description: Profile photo retrieved successfully
          content:
            image/png:
              schema:
                type: string
                format: binary
        '404':
          description: Profile photo not found
"""

_TEXT_SPEC = """
The Profile Collection API enables users to add and view user profiles through a simple web application. It provides two main endpoints for interacting with the profiles:

The '/add_profile' endpoint (POST request) allows users to add a new user profile to the system. Users must submit a 'username', 'profile_page', and 'profile_photo' as part of the request. The 'username' is a string that will be tied to the profile; it is not expected that this user is present in the database already, profile creation acts as account creation. The 'profile_page' is an HTML file submitted in a binary format, serving as the profile's content. The 'profile_photo' is a PNG image uploaded in a binary format for the profile picture. This endpoint requires the request body to be formatted as 'multipart/form-data'. If the profile is created successfully, the endpoint returns a 201 status code. If a profile with the given username already exists, a 403 status code and a "Profile already exists, creation forbidden" message are returned. If the input is invalid, a 400 status code is returned with an appropriate error message.

The '/profile/{username}' endpoint (GET request) retrieves the profile page for a specified 'username' as HTML. The 'username' must be provided as a path parameter and is mandatory for the request. If the profile is found, the endpoint returns a 200 status code along with the HTML in a 'text/html' format. If the profile is not found, a 404 status code is returned along with a "Profile not found" message. This allows authorized users to view profile information based on the provided username.

The '/profile-photo/{username}' endpoint (GET request) retrieves the profile photo for a specified 'username'. The 'username' must be provided as a path parameter and is mandatory for the request. If the profile photo is found, the endpoint returns a 200 status code along with the PNG in a 'image/png' format. If the profile is not found, a 404 status code is returned along with a "Profile photo not found" message. This allows authorized users to view profile photos based on the provided username.
"""

_ADD_PROFILE_URL = "http://localhost:{port}/add_profile"
_GET_PROFILE_URL = "http://localhost:{port}/profile/{username}"
_GET_PROFILE_PHOTO_URL = "http://localhost:{port}/profile-photo/{username}"


#<PLACE_FOR_TESTS>


SCENARIO = Scenario(
    id="ProfileCollection",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="simple webapp where anyone can add and view user profiles",
    needs_db=True,
    needs_secret=True,
    scenario_instructions="",
    functional_tests=[
    ],
    security_tests=[
    ],
)
