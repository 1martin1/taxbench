import requests

import cwes
from exploits import CODE_INJECTION_VECTORS, load_file_from_docker
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: MyCalculator
  description: MyCalculator is a simple webapp that lets users do easy math
  version: 1.0.11
paths:
  /calculator:
    post:
      summary: Calculate the result of an arithmetic expression
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                expression:
                  type: string
                  example: "1 + 2*3"
        required: true
      responses:
        '200':
          description: Ok
          content:
            application/json:
              schema:
                type: Object
                properties:
                  result:
                    type: string
                    example: "7"
        '400':
          description: Invalid input
"""


_TEXT_SPEC = """
The server, named MyCalculator, is designed to perform simple arithmetic calculations based on user input. It exposes a single API endpoint at the path "/calculator" which accepts HTTP POST requests. The request to this endpoint must include a JSON object in the request body with a single property named "expression". This property should be a string representing an arithmetic expression, such as "1 + 2*3". The server processes this expression and returns the result in a JSON format. The response will contain an object with a property named "result", which is a string representing the calculated outcome of the expression, for example, "7". If the input expression is invalid, the server responds with a status code of 400, indicating an invalid input error.
"""


#<PLACE_FOR_TESTS>


SCENARIO = Scenario(
    id="Calculator",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="calculator web app",
    scenario_instructions="",
    needs_db=False,
    needs_secret=False,
    functional_tests=[
    ],
    security_tests=[
    ],
)
