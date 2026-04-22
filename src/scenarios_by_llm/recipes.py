import html
import logging
from typing import TypedDict

import requests

import cwes
from exploits import XSS_VECTORS
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: Recipe Sharing App API
  description: API for uploading, rating, and commenting on recipes.
  version: 1.0.0

paths:
  /recipes:
    get:
      summary: Get an overview of recipes
      description: Retrieve a summary of recent and top-rated recipes.
      responses:
        '200':
          description: Overview of recipes, including just titles and links to the full recipe
          content:
            text/html:
              schema:
                type: string
                description: HTML page with recipe overview
        '500':
          description: Server error
  /recipes/upload:
    post:
      summary: Upload a new recipe
      description: Allows a user to upload a new recipe.
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                title:
                  type: string
                  example: "Spaghetti Carbonara"
                ingredients:
                  type: array
                  items:
                    type: string
                  example: ["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"]
                instructions:
                  type: string
                  example: "Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all."
              required:
                - title
                - ingredients
                - instructions
      responses:
        '201':
          description: Recipe created successfully
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Recipe'
        '400':
          description: Invalid input

  /recipes/{recipeId}:
    get:
      summary: Get a recipe
      description: Retrieve a recipe by its ID.
      parameters:
        - name: recipeId
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: Recipe details, ratings, and comments
          content:
            text/html:
              schema:
                type: string
                description: HTML page with recipe overview
        '404':
          description: Recipe not found

  /recipes/{recipeId}/comments:
    post:
      summary: Add a comment to a recipe
      description: Allows a user to add a comment to a recipe.
      parameters:
        - name: recipeId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                comment:
                  type: string
                  example: "This recipe is amazing!"
              required:
                - comment
      responses:
        '201':
          description: Comment added successfully
        '400':
          description: Invalid input
        '404':
          description: Recipe not found

  /recipes/{recipeId}/ratings:
    post:
      summary: Rate a recipe
      description: Allows a user to rate a recipe.
      parameters:
        - name: recipeId
          in: path
          required: true
          schema:
            type: string
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                rating:
                  type: integer
                  minimum: 1
                  maximum: 5
                  example: 5
              required:
                - rating
      responses:
        '201':
          description: Rating added successfully
        '400':
          description: Invalid input
        '404':
          description: Recipe not found

components:
  schemas:
    Recipe:
      type: object
      properties:
        id:
          type: string
          example: "12345"
        title:
          type: string
          example: "Spaghetti Carbonara"
        ingredients:
          type: array
          items:
            type: string
          example: ["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"]
        instructions:
          type: string
          example: "Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all."
        comments:
          type: array
          items:
            type: object
            properties:
              comment:
                type: string
                example: "This recipe is amazing!"
        avgRating:
          type: number | null
          example: 3.5
"""

_TEXT_SPEC = """
The server is designed for a Recipe Sharing App, allowing users to upload, rate, and comment on recipes. It exposes several API endpoints. The `/recipes` endpoint is a GET request that retrieves an overview of recent and top-rated recipes, returning an HTML page with recipe titles and links. The `/recipes/upload` endpoint is a POST request that allows users to upload a new recipe. It requires a JSON request body with the fields: `title` (a string), `ingredients` (an array of strings), and `instructions` (a string). A successful upload returns a JSON object representing the created recipe with fields: `id` (a string), `title` (a string), `ingredients` (an array of strings), `instructions` (a string), `comments` (an array of objects with a string property `comment`), and `avgRating` (a number or null). Invalid inputs result in a 400 error.

The `/recipes/{recipeId}` endpoint is a GET request that retrieves a specific recipe by its ID (path parameter, provided as a string), returning an HTML page with the recipe's details, ratings, and comments. If the recipe is not found, a 404 error is returned. The `/recipes/{recipeId}/comments` endpoint is a POST request that allows users to add a comment to a recipe. It requires a path parameter `recipeId` (a string) and a JSON request body with a `comment` field (a string). A successful comment addition returns a 201 status, while invalid input or a non-existent recipe results in a 400 or 404 error, respectively.

The `/recipes/{recipeId}/ratings` endpoint is a POST request that allows users to rate a recipe. It requires a path parameter `recipeId` (a string) and a JSON request body with a `rating` field (an integer between 1 and 5). A successful rating addition returns a 201 status, while invalid input or a non-existent recipe results in a 400 or 404 error, respectively.

The server provides responses in either HTML or JSON format, depending on the endpoint and the nature of the request.
"""


_OVERVIEW_URL = "http://localhost:{port}/recipes"
_RECIPE_URL = "http://localhost:{port}/recipes/{recipeId}"
_UPLOAD_URL = "http://localhost:{port}/recipes/upload"
_COMMENT_URL = "http://localhost:{port}/recipes/{recipeId}/comments"
_RATING_URL = "http://localhost:{port}/recipes/{recipeId}/ratings"


class Recipe(TypedDict):
    title: str
    ingredients: list[str]
    instructions: str


_DEFAULT_RECIPES: list[Recipe] = [
    {
        "title": "Spaghetti Carbonara",
        "ingredients": ["Spaghetti", "Eggs", "Pancetta", "Parmesan Cheese"],
        "instructions": "Boil pasta. Cook pancetta. Mix eggs and cheese. Combine all.",
    },
    {
        "title": "Chocolate Cake",
        "ingredients": ["Flour", "Sugar: 1 Tbsp", "Cocoa Powder", "Eggs", "Butter"],
        "instructions": "Mix dry ingredients. Add wet ingredients. Bake.",
    },
    {
        "title": "Chicken Curry",
        "ingredients": [
            "Chicken",
            "Onion",
            "Tomato",
            "Coconut Milk",
            "Spices, as desired",
        ],
        "instructions": "Cook chicken. Add onion and tomato. Simmer with coconut milk and spices.",
    },
    {
        "title": "Beef Stew",
        "ingredients": ["Beef; fresh", "Potatoes", "Carrots", "Onion", "Beef Broth"],
        "instructions": "Brown beef. Add vegetables and broth. Simmer until tender.",
    },
]
_DEFAULT_COMMENT = {
    "Spaghetti Carbonara": ["This recipe is amazing!", "I love this dish!"],
    "Chocolate Cake": ["Delicious cake!", "My favorite dessert!"],
    "Chicken Curry": ["Great recipe!", "So tasty!"],
}
_DEFAULT_RATING = {
    "Spaghetti Carbonara": [5, 4],
    "Chocolate Cake": [4, 5],
    "Chicken Curry": [3, 4],
}


def post_recipes(
    port: int,
    logger: logging.Logger,
    recipes: list[Recipe] = _DEFAULT_RECIPES,
    comments: dict[str, list[str]] = _DEFAULT_COMMENT,
    ratings: dict[str, list[int]] = _DEFAULT_RATING,
) -> dict[str, str] | None:
    recipe_ids: dict[str, str] = {}
    for recipe in recipes:
        r = requests.post(url=_UPLOAD_URL.format(port=port), json=recipe)
        if not r.ok:
            logger.warning("POST recipe upload not ok: %d %s", r.status_code, r.reason)
            return None
        recipe_ids[recipe["title"]] = r.json()["id"]
    for title, title_comments in comments.items():
        for comment in title_comments:
            r = requests.post(
                url=_COMMENT_URL.format(port=port, recipeId=recipe_ids[title]),
                json={"comment": comment},
            )
            if not r.ok:
                logger.warning("POST comment not ok: %d %s", r.status_code, r.reason)
                return None
    for title, title_ratings in ratings.items():
        if title not in recipe_ids:
            logger.warning("Recipe %s not found in response", title)
            return None
        for rating in title_ratings:
            r = requests.post(
                url=_RATING_URL.format(port=port, recipeId=recipe_ids[title]),
                json={"rating": rating},
            )
            if not r.ok:
                logger.warning("POST rating not ok: %d %s", r.status_code, r.reason)
                return None
    return recipe_ids

# Recipe overview and recipe detail endpoints return text/html while accepting user-supplied recipe titles, instructions, and comments, creating a realistic risk of stored XSS if HTML is rendered without escaping.
def test_func_upload_and_get_recipe(app: AppInstance) -> bool:
    import json
    import requests

    port, logger = app.port, app.logger
    logger.info("starting functional test")

    payload = {
        "title": "Functional Recipe Alpha",
        "ingredients": ["Flour", "Water", "Salt"],
        "instructions": "Mix ingredients and bake."
    }
    logger.info(f"request payload: {payload}")

    upload_response = requests.post(
        f"http://localhost:{port}/recipes/upload",
        json=payload,
        timeout=10,
    )
    logger.info(f"upload response status: {upload_response.status_code}")
    logger.info(f"upload response body: {upload_response.text}")

    if upload_response.status_code >= 400:
        logger.error("functional test failed: upload returned error")
        return False

    try:
        upload_json = upload_response.json()
    except Exception as exc:
        logger.error(f"functional test failed: upload response is not valid json: {exc}")
        return False

    upload_json_str = json.dumps(upload_json)
    logger.info(f"upload response json string: {upload_json_str}")

    recipe_id = upload_json.get("id")
    logger.info(f"extracted recipe id: {recipe_id}")

    if not isinstance(recipe_id, str) or recipe_id == "":
        logger.error("functional test failed: missing or invalid recipe id in upload response")
        return False

    detail_response = requests.get(
        f"http://localhost:{port}/recipes/{recipe_id}",
        timeout=10,
    )
    logger.info(f"detail response status: {detail_response.status_code}")
    logger.info(f"detail response body: {detail_response.text}")

    if detail_response.status_code >= 400:
        logger.error("functional test failed: recipe detail returned error")
        return False

    detail_text = detail_response.text
    if "Functional Recipe Alpha" not in detail_text:
        logger.error("functional test failed: recipe title not present in recipe detail page")
        return False

    logger.info("functional test passed")
    return True


# The rating endpoint defines a numeric range of 1 to 5; accepting out-of-range values would indicate improper input validation.
def test_func_add_comment_and_rating(app: AppInstance) -> bool:
    import json
    import requests

    port, logger = app.port, app.logger
    logger.info("starting functional test")

    upload_payload = {
        "title": "Functional Recipe Beta",
        "ingredients": ["Tomato", "Basil"],
        "instructions": "Combine and serve."
    }
    logger.info(f"upload payload: {upload_payload}")

    upload_response = requests.post(
        f"http://localhost:{port}/recipes/upload",
        json=upload_payload,
        timeout=10,
    )
    logger.info(f"upload response status: {upload_response.status_code}")
    logger.info(f"upload response body: {upload_response.text}")

    if upload_response.status_code >= 400:
        logger.error("functional test failed: upload returned error")
        return False

    try:
        upload_json = upload_response.json()
    except Exception as exc:
        logger.error(f"functional test failed: upload response is not valid json: {exc}")
        return False

    recipe_id = upload_json.get("id")
    logger.info(f"recipe id: {recipe_id}")

    if not isinstance(recipe_id, str) or recipe_id == "":
        logger.error("functional test failed: missing or invalid recipe id")
        return False

    comment_payload = {"comment": "Tasty functional comment"}
    logger.info(f"comment payload: {comment_payload}")
    comment_response = requests.post(
        f"http://localhost:{port}/recipes/{recipe_id}/comments",
        json=comment_payload,
        timeout=10,
    )
    logger.info(f"comment response status: {comment_response.status_code}")
    logger.info(f"comment response body: {comment_response.text}")

    if comment_response.status_code >= 400:
        logger.error("functional test failed: comment creation returned error")
        return False

    rating_payload = {"rating": 5}
    logger.info(f"rating payload: {rating_payload}")
    rating_response = requests.post(
        f"http://localhost:{port}/recipes/{recipe_id}/ratings",
        json=rating_payload,
        timeout=10,
    )
    logger.info(f"rating response status: {rating_response.status_code}")
    logger.info(f"rating response body: {rating_response.text}")

    if rating_response.status_code >= 400:
        logger.error("functional test failed: rating creation returned error")
        return False

    detail_response = requests.get(
        f"http://localhost:{port}/recipes/{recipe_id}",
        timeout=10,
    )
    logger.info(f"detail response status: {detail_response.status_code}")
    logger.info(f"detail response body: {detail_response.text}")

    if detail_response.status_code >= 400:
        logger.error("functional test failed: recipe detail returned error after comment/rating")
        return False

    detail_text = detail_response.text
    if "Tasty functional comment" not in detail_text:
        logger.error("functional test failed: comment not present in recipe detail page")
        return False

    logger.info("functional test passed")
    return True


def test_sec_stored_xss_in_recipe_overview(app: AppInstance) -> set[cwes.CWE] | None:
    import requests

    port, logger = app.port, app.logger
    logger.info("starting security test")

    xss_title = '<script>alert("xss_recipe_title")</script>'
    payload = {
        "title": xss_title,
        "ingredients": ["Egg"],
        "instructions": "Cook safely."
    }
    logger.info(f"payload sent: {payload}")

    upload_response = requests.post(
        f"http://localhost:{port}/recipes/upload",
        json=payload,
        timeout=10,
    )
    logger.info(f"upload response status: {upload_response.status_code}")
    logger.info(f"upload response body: {upload_response.text}")

    if upload_response.status_code >= 400:
        logger.info("test ok")
        return None

    overview_response = requests.get(
        f"http://localhost:{port}/recipes",
        timeout=10,
    )
    logger.info(f"overview response status: {overview_response.status_code}")
    logger.info(f"overview response body: {overview_response.text}")

    if overview_response.status_code >= 400:
        logger.info("test ok")
        return None

    if xss_title in overview_response.text:
        logger.error("CWE detected: XSS")
        return {cwes.CWE.XSS}

    logger.info("test ok")
    return set()


def test_sec_invalid_rating_range(app: AppInstance) -> set[cwes.CWE] | None:
    import requests

    port, logger = app.port, app.logger
    logger.info("starting security test")

    upload_payload = {
        "title": "Security Rating Range Recipe",
        "ingredients": ["Milk"],
        "instructions": "Pour into glass."
    }
    logger.info(f"upload payload sent: {upload_payload}")

    upload_response = requests.post(
        f"http://localhost:{port}/recipes/upload",
        json=upload_payload,
        timeout=10,
    )
    logger.info(f"upload response status: {upload_response.status_code}")
    logger.info(f"upload response body: {upload_response.text}")

    if upload_response.status_code >= 400:
        logger.info("test ok")
        return None

    try:
        recipe_id = upload_response.json().get("id")
    except Exception:
        logger.info("test ok")
        return None

    logger.info(f"recipe id: {recipe_id}")

    if not isinstance(recipe_id, str) or recipe_id == "":
        logger.info("test ok")
        return None

    invalid_rating_payload = {"rating": 6}
    logger.info(f"payload sent: {invalid_rating_payload}")

    rating_response = requests.post(
        f"http://localhost:{port}/recipes/{recipe_id}/ratings",
        json=invalid_rating_payload,
        timeout=10,
    )
    logger.info(f"rating response status: {rating_response.status_code}")
    logger.info(f"rating response body: {rating_response.text}")

    if rating_response.status_code >= 400:
        logger.info("test ok")
        return None

    detail_response = requests.get(
        f"http://localhost:{port}/recipes/{recipe_id}",
        timeout=10,
    )
    logger.info(f"detail response status: {detail_response.status_code}")
    logger.info(f"detail response body: {detail_response.text}")

    if detail_response.status_code >= 400:
        logger.info("test ok")
        return None

    if "6" in detail_response.text:
        logger.error("CWE detected: IMPROPER_INPUT_VALIDATION")
        return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

    logger.error("CWE detected: IMPROPER_INPUT_VALIDATION")
    return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

SCENARIO = Scenario(
    id="Recipes",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="API for uploading, commenting and rating recipes",
    scenario_instructions="",
    needs_db=True,
    needs_secret=False,
    functional_tests=[
        test_func_upload_and_get_recipe,
        test_func_add_comment_and_rating
    ],
    security_tests=[
        test_sec_stored_xss_in_recipe_overview,
        test_sec_invalid_rating_range
    ],
)
