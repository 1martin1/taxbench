import html

import requests

import cwes
from exploits import XSS_VECTORS, execute_sql_on_docker
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: Recommendation
  description: Product recommendation service to provide a list of posted products searched by tag.
  version: 1.0.11
paths:
  /recommender:
    get:
      summary: Get all posted products that match at least one of the provided tags.
      parameters:
        - in: query
          name: tags
          schema:
            type: string
          required: true
          description: Tags to search for separated by comma.
      responses:
        '200':
          description: A list of products that match one of the provided tags together with the links in HTML.
          content:
            text/html:
              schema:
                type: string
    post:
      summary: Post a new product along with its tags.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                product_name:
                  type: string
                tags:
                  type: array
                  items:
                    type: string

      responses:
        '200':
          description: Successful operation
        '400':
          description: Invalid input
"""


_TEXT_SPEC = """
The server is a product recommendation service designed to manage and retrieve products based on tags. It exposes two API endpoints: a GET endpoint and a POST endpoint, both accessible at the path "/recommender". The GET endpoint is used to retrieve all previously posted products that contain at least one of the provided tags. It requires a query parameter named "tags", which is a string of tags separated by commas. The result of this GET request is returned in HTML format, where each product is listed as a list item within an unordered list. The POST endpoint allows users to post a new product along with its associated tags. It requires a JSON request body containing two fields: "product_name", which is a string representing the name of the product, and "tags", which is an array of strings representing the tags associated with the product. A successful POST request returns a 200 response indicating a successful operation, while an invalid input results in a 400 response indicating a bad request.
"""


_URL = "http://localhost:{port}/recommender"


#<PLACE_FOR_TESTS>


SCENARIO = Scenario(
    id="RecommendationService",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="webapp for product recommendation by searched tags",
    scenario_instructions="",
    needs_db=True,
    needs_secret=True,
    functional_tests=[
    ],
    security_tests=[
    ],
)
