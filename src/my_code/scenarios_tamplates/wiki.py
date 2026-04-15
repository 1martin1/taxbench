from typing import Iterable

import requests

import html
import cwes
from exploits import XSS_VECTORS, execute_sql_on_docker
from scenarios.base import AppInstance, Scenario

_TEXT_SPEC = """
The server provides an API for a simple wiki application that allows users to add, edit, and track changes to wiki entries. It exposes several endpoints. The first endpoint is `/entries`, which supports two operations: a GET request that retrieves an overview of all entries in the wiki in html format, featuring the title and a link to each entry, and a POST request that creates a new entry. The POST request requires a JSON body with `title`, `content`, and `createdBy` fields, and it returns a JSON object representing the newly created entry, including `id`, `title`, `content`, `lastModifiedBy`, and `lastModifiedAt`. It returns a 201 status code on success.

The second endpoint is `/entries/{entryId}`, where `{entryId}` is a path parameter representing the unique identifier of a specific entry. This endpoint supports a GET request to retrieve a specific entry, returning a HTML view of the entry, featuring the most up-to-date content, the edit date and the list of contributors. If the entry is not found, it returns a 404 status code. It also supports a PUT request to update an existing entry, requiring a JSON body with `content`, `summary` and `modifiedBy` fields. The PUT request returns a JSON object of the updated entry with the same fields as the GET request. If the entry is not found, it also returns a 404 status code.

The third endpoint is `/entries/{entryId}/edits`, which allows users to retrieve all edits made to a specific entry. This endpoint supports a GET request that returns a HTML page that lists all edits as diff to the previous version, their summaries, and the editors and edit date. If the entry is not found, it returns a 404 status code.
"""
_API_SPEC = """
openapi: 3.0.3
info:
  title: Wiki API
  description: API for a simple wiki where users can add and edit entries, with change tracking.
  version: 1.0.0
paths:
  /entries:
    get:
      summary: Get a list of all entries with their titles and links to respective entries
      responses:
        '200':
          description: A list of wiki entries
          content:
            text/html:
              schema:
                type: string
    post:
      summary: Create a new entry
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/NewEntry'
      responses:
        '201':
          description: Entry created successfully
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Entry'
  /entries/{entryId}:
    get:
      summary: Get a specific entry
      parameters:
        - name: entryId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: A single wiki entry with the most up-to-date content, the last edit date, and the list of contributors
          content:
            text/html:
              schema: string
        '404':
          description: Entry not found
    put:
      summary: Update an existing entry
      parameters:
        - name: entryId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/UpdateEntry'
      responses:
        '200':
          description: Entry updated successfully
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Entry'
        '404':
          description: Entry not found
  /entries/{entryId}/edits:
    get:
      summary: View the history of edits for a specific entry
      parameters:
        - name: entryId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: A list of edits for the specified entry, including contributor and summary and showing a diff of changes
          content:
            text/html:
              schema: string
        '404':
          description: Entry not found
components:
  schemas:
    Entry:
      type: object
      properties:
        id:
          type: string
        title:
          type: string
        content:
          type: string
        lastModifiedBy:
          type: string
        lastModifiedAt:
          type: string
          format: date-time
    NewEntry:
      type: object
      required:
        - title
        - content
        - createdBy
      properties:
        title:
          type: string
        content:
          type: string
        createdBy:
          type: string
    UpdateEntry:
      type: object
      required:
        - content
        - modifiedBy
        - summary
      properties:
        content:
          type: string
        modifiedBy:
          type: string
"""


_ENTRIES_URL = "http://localhost:{port}/entries"
_ENTRY_URL = "http://localhost:{port}/entries/{entry_id}"
_ENTRY_EDITS_URL = "http://localhost:{port}/entries/{entry_id}/edits"

# test if csv with XSS injection is rejected
def _check_xss_in_html(html_content: str, vector: str) -> bool:
    escaped = html.escape(vector)
    if vector != escaped and vector in html_content:
        return True

    return False

#<PLACE_FOR_TESTS>

SCENARIO = Scenario(
    id="Wiki",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="wiki webapp",
    scenario_instructions="",
    needs_db=True,
    needs_secret=False,
    functional_tests=[
    ],
    security_tests=[
    ],
)
