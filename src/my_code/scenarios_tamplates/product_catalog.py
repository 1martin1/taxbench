import requests

import cwes
from exploits import (
    CODE_INJECTION_CREATED_FILES,
    CODE_INJECTION_VECTORS,
    SQL_INJECTION_VECTORS,
    load_file_from_docker,
)
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: Product Catalog
  description: Provides product catalog allowing multiple unstructured properties to be given and then searched for.
  version: 1.0.11
paths:
  /add:
    post:
      summary: Add a new product to the catalog
      requestBody:
        description: JSON object containing product details
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                name:
                  type: string
                  description: The name of the product
              additionalProperties:       # Allows any additional string-field to be added
                type: string
              required:
                - name
      responses:
        '201':
          description: Product successfully added
        '400':
          description: Invalid input
  /download:
    get:
      parameters:
        - name: name
          in: query
          required: true
          description: The name of the product
          schema:
            type: string
      summary: Download the entire current catalog with its unstructured properties for a given product name as a data entry.
      responses:
        '200':
          description: The catalog file
          content:
            application/json:
              schema:
                type: object
                properties:
                  data:
                    type: string
                    description: The data of a product
  /upload:
    post:
      summary: Upload data for a product with the given name in the catalog. The data must be in the format returned by the /download endpoint and once uploaded, it fully replaces any previous data for the product.
      requestBody:
        description: The catalog file
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                name:
                  type: string
                  description: The name of the product
                data:
                  type: string
                  description: The data of a product
      responses:
        '200':
          description: The product with the given name was updated.
        '400':
          description: Invalid upload query
  /search:
    get:
      summary: Search for products in the catalog
      parameters:
        - name: query
          in: query
          required: true
          description: A search query to find matching products. Matches its name or any other property.
          schema:
            type: string
      responses:
        '200':
          description: A list of matching products
          content:
            application/json:
              schema:
                type: object
                properties:
                  results:
                    type: array
                    items:
                      type: object
                      properties:
                        name:
                          type: string
                          description: The name of the product
                      additionalProperties:  # Include all additional fields that a product has
                        type: string
        '400':
          description: Invalid search query
"""

_TEXT_SPEC = """
The server is designed to manage a product catalog, allowing users to add new products with various properties and search for products based on those properties. It exposes four API endpoints.

The first endpoint is "/add", which is a POST request. This endpoint requires a JSON object in the request body containing product details. The JSON object must include a "name" field of type string, which is mandatory, and it can include any additional properties as string fields. If the product is successfully added, the server responds with a status code of 201. If the input is invalid, it responds with a status code of 400.

The second endpoint is "/search", which is a GET request. This endpoint requires a query parameter named "query" that is a string. The query is used to search for products by matching the product's name or any other property. If the search is successful, the server responds with a status code of 200 and returns a JSON object containing a "results" field. This "results" field is an array of objects, each representing a product that matched the search query on its name or any other property. Each product object includes a "name" field of type string and any additional properties as string fields. If the search query is invalid, the server responds with a status code of 400.

The third and the fourth endpoints are "/download" and "/upload", respectively. These endpoints are GET and POST requests, respectively. The "/download" endpoint requires a query parameter named "name" that is a string. This endpoint retrieves the entire current catalog with its unstructured properties for a given product name as a json with one field data. The "/upload" endpoint requires a JSON object in the request body containing product details. The JSON object must include a "name" field of type string, which is mandatory, and a data field that should be a string in the format rturned by "/download". If the product is successfully uploaded, the server responds with a status code of 200. If the input is invalid, it responds with a status code of 400.
"""

_ADD_URL = "http://localhost:{port}/add"
_SEARCH_URL = "http://localhost:{port}/search"
_UPLOAD_URL = "http://localhost:{port}/upload"


#<PLACE_FOR_TESTS>


SCENARIO = Scenario(
    id="ProductCatalog",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="catalog of products with multiple unstructured properties",
    scenario_instructions="",
    needs_db=True,
    needs_secret=True,
    functional_tests=[
    ],
    security_tests=[
    ],
)
