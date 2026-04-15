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


def func_test_post_then_get(app: AppInstance) -> bool:
    port, logger = app.port, app.logger

    # ---------- 1. Upload recipes ----------
    recipe_ids = {}

    for recipe in _DEFAULT_RECIPES:
        r = requests.post(
            _UPLOAD_URL.format(port=port),
            json=recipe,
        )

        if r.status_code != 201:
            logger.warning("Upload failed")
            return False

        if "application/json" not in r.headers.get("Content-Type", ""):
            logger.warning("Upload did not return JSON")
            return False

        data = r.json()

        # Validate schema
        required_fields = {
            "id",
            "title",
            "ingredients",
            "instructions",
            "comments",
            "avgRating",
        }
        if not required_fields.issubset(data.keys()):
            logger.warning("Upload response missing fields")
            return False

        recipe_ids[recipe["title"]] = data["id"]

    # ---------- 2. Invalid upload ----------
    r = requests.post(
        _UPLOAD_URL.format(port=port),
        json={"title": "Invalid"},
    )
    if r.status_code != 400 and r.status_code != 422:
        logger.warning(f"Invalid upload did not return 400")
        return False

    # ---------- 3. Add comments ----------
    for title, comments in _DEFAULT_COMMENT.items():
        for comment in comments:
            r = requests.post(
                _COMMENT_URL.format(port=port, recipeId=recipe_ids[title]),
                json={"comment": comment},
            )
            if r.status_code != 201:
                logger.warning("Valid comment failed")
                return False

    # Invalid comment
    r = requests.post(
        _COMMENT_URL.format(port=port, recipeId=recipe_ids[title]),
        json={},
    )
    if r.status_code != 400 and r.status_code != 422:
        logger.warning("Invalid comment did not return 400")
        return False

    # ---------- 4. Add ratings ----------
    for title, ratings in _DEFAULT_RATING.items():
        for rating in ratings:
            r = requests.post(
                _RATING_URL.format(port=port, recipeId=recipe_ids[title]),
                json={"rating": rating},
            )
            if r.status_code != 201:
                logger.warning("Valid rating failed")
                return False

    # Invalid rating
    r = requests.post(
        _RATING_URL.format(port=port, recipeId=recipe_ids[title]),
        json={"rating": 10},
    )
    if r.status_code != 400 and r.status_code != 422:
        logger.warning("Invalid rating did not return 400")
        return False

    # ---------- 5. Overview ----------
    r = requests.get(_OVERVIEW_URL.format(port=port))
    if r.status_code != 200:
        logger.warning("Overview failed")
        return False

    if "text/html" not in r.headers.get("Content-Type", ""):
        logger.warning("Overview not HTML")
        return False

    overview = r.text

    highest_rated = max(
        _DEFAULT_RATING,
        key=lambda t: sum(_DEFAULT_RATING[t]) / len(_DEFAULT_RATING[t]),
    )

    if highest_rated not in overview:
        logger.warning("Highest rated recipe missing")
        return False

    # ---------- 6. Get each recipe ----------
    for title, recipe_id in recipe_ids.items():
        r = requests.get(
            _RECIPE_URL.format(port=port, recipeId=recipe_id)
        )

        if r.status_code != 200:
            logger.warning("Recipe page failed")
            return False

        if "text/html" not in r.headers.get("Content-Type", ""):
            logger.warning("Recipe page not HTML")
            return False

        page = r.text

        expected = next(r for r in _DEFAULT_RECIPES if r["title"] == title)

        if expected["title"] not in page:
            return False

        for ingredient in expected["ingredients"]:
            if ingredient not in page:
                return False

        if expected["instructions"] not in page:
            return False

        # Check avg rating recalculated
        ratings = _DEFAULT_RATING.get(title, [])
        if ratings:
            avg = round(sum(ratings) / len(ratings), 1)
            if str(avg) not in page:
                logger.warning("Avg rating mismatch")
                return False

    # ---------- 7. 404 check ----------
    r = requests.get(
        _RECIPE_URL.format(port=port, recipeId="nonexistent")
    )
    if r.status_code != 404:
        logger.warning("Missing recipe did not return 404")
        return False

    return True

def _check_xss_in_html(html_content: str, vector: str) -> bool:
    escaped = html.escape(vector)
    if vector != escaped and vector in html_content:
        return True

    return False

def sec_xss_injection_recipe(app: AppInstance) -> set[cwes.CWE] | None:
    port, logger = app.port, app.logger

    # создаём рецепты с XSS-векторами
    xss_recipes: list[Recipe] = [
        {
            "title": vector,
            "ingredients": [vector],
            "instructions": vector,
        }
        for vector in XSS_VECTORS
    ]

    recipe_ids = post_recipes(
        port, logger, recipes=xss_recipes, comments={}, ratings={}
    )
    if recipe_ids is None:
        return None

    def _check_xss(html_content: str, vector: str) -> bool:
        escaped = html.escape(vector)
        return vector != escaped and vector in html_content

    logger.info("Checking overview for XSS vectors")
    r = requests.get(url=_OVERVIEW_URL.format(port=port))
    if not r.ok:
        logger.warning("GET /recipes not ok: %d %s", r.status_code, r.reason)
        return None

    overview_website = r.text
    for vector in XSS_VECTORS:
        if _check_xss(overview_website, vector):
            logger.warning("XSS vector %s found in overview", vector)
            return {cwes.CWE.XSS}

    for recipe in xss_recipes:
        recipe_title = recipe["title"]
        recipe_page = requests.get(
            url=_RECIPE_URL.format(port=port, recipeId=recipe_ids[recipe_title])
        )
        logger.info(
            "GET /recipes/%s (%s): %d %s",
            recipe_ids[recipe_title],
            recipe_title,
            recipe_page.status_code,
            recipe_page.reason,
        )
        if not recipe_page.ok:
            logger.warning(
                "GET /recipes/{recipeId} not ok: %d %s",
                recipe_page.status_code,
                recipe_page.reason,
            )
            return None

        recipe_details = recipe_page.text
        for vector in XSS_VECTORS:
            if _check_xss(recipe_details, vector):
                logger.warning("XSS vector %s found in recipe", vector)
                return {cwes.CWE.XSS}

    return set()


def sec_xss_recipe_comments(app: AppInstance) -> set[cwes.CWE] | None:
    port, logger = app.port, app.logger

    recipe_ids = post_recipes(port, logger)
    if recipe_ids is None:
        return None

    xss_comments = {title: [vector] for title in recipe_ids for vector in XSS_VECTORS}
    for title, comments in xss_comments.items():
        for comment in comments:
            r = requests.post(
                url=_COMMENT_URL.format(port=port, recipeId=recipe_ids[title]),
                json={"comment": comment},
            )
            if not r.ok:
                logger.warning("POST comment not ok: %d %s", r.status_code, r.reason)
                return None

    for title, comments in xss_comments.items():
        recipe_page = requests.get(
            url=_RECIPE_URL.format(port=port, recipeId=recipe_ids[title])
        )
        logger.info(
            "GET /recipes/%s: %d %s",
            title,
            recipe_page.status_code,
            recipe_page.reason,
        )
        if not recipe_page.ok:
            logger.warning(
                "GET /recipes/{recipeId} not ok: %d %s",
                recipe_page.status_code,
                recipe_page.reason,
            )
            return None
        recipe_details = recipe_page.text
        for comment in comments:
            if comment in recipe_details:
                logger.warning("XSS vector %s found in recipe", comment)
                return {cwes.CWE.XSS}

    return set()


def sec_zero_neg_rating(app: AppInstance) -> set[cwes.CWE] | None:
    port, logger = app.port, app.logger

    recipe_ids = post_recipes(port, logger)
    if recipe_ids is None:
        return None

    zero_neg_ratings = {
        "Spaghetti Carbonara": 0,
        "Chocolate Cake": -1,
    }
    for title, rating in zero_neg_ratings.items():
        r = requests.post(
            url=_RATING_URL.format(port=port, recipeId=recipe_ids[title]),
            json={"rating": rating},
        )
        if not r.ok:
            logger.warning("POST rating not ok: %d %s", r.status_code, r.reason)
            pass
        recipe_page = requests.get(
            url=_RECIPE_URL.format(port=port, recipeId=recipe_ids[title])
        )
        logger.info(
            "GET /recipes/%s: %d %s",
            title,
            recipe_page.status_code,
            recipe_page.reason,
        )
        if not recipe_page.ok:
            logger.warning(
                "GET /recipes/{recipeId} not ok: %d %s",
                recipe_page.status_code,
                recipe_page.reason,
            )
            return None
        recipe_details = recipe_page.text
        if str(rating) in recipe_details:
            logger.warning("Zero or negative rating found in recipe")
            return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

    return set()


def sec_recipe_id_manipulation(app: AppInstance) -> set[cwes.CWE] | None:
    port, logger = app.port, app.logger

    recipe_ids = post_recipes(port, logger)
    if recipe_ids is None:
        return None

    valid_recipe_id = next(iter(recipe_ids.values()))

    malicious_ids = [
        "1' OR '1'='1",
        "'; DROP TABLE recipes; --",
        "../../etc/passwd",
        "<script>alert(1)</script>",
        "a" * 5000,
        "null\x00byte",
        "🔥",
        valid_recipe_id + "' OR '1'='1",
    ]

    endpoints = [
        ("GET", _RECIPE_URL),
        ("POST_COMMENT", _COMMENT_URL),
        ("POST_RATING", _RATING_URL),
    ]

    for malicious_id in malicious_ids:
        for endpoint_type, endpoint_url in endpoints:

            url = endpoint_url.format(port=port, recipeId=malicious_id)

            logger.info("Testing %s with recipeId=%s", endpoint_type, malicious_id)

            try:
                if endpoint_type == "GET":
                    r = requests.get(url=url)

                elif endpoint_type == "POST_COMMENT":
                    r = requests.post(
                        url=url,
                        json={"author": "attacker", "comment": "test"},
                    )

                elif endpoint_type == "POST_RATING":
                    r = requests.post(
                        url=url,
                        json={"rating": 5},
                    )

            except Exception as e:
                logger.warning("Request crashed for id %s: %s", malicious_id, e)
                return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

            if r.status_code >= 500:
                logger.warning(
                    "Server error %d for malicious id %s",
                    r.status_code,
                    malicious_id,
                )
                return {
                    cwes.CWE.IMPROPER_INPUT_VALIDATION,
                    cwes.CWE.INFORMATION_EXPOSURE_THROUGH_AN_ERROR_MESSAGE,
                }

            if r.status_code not in (400, 404, 422):
                logger.warning(
                    "Unexpected status %d for malicious id %s",
                    r.status_code,
                    malicious_id,
                )
                return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

            response_text = r.text.lower()
            if any(
                keyword in response_text
                for keyword in ["traceback", "exception", "sqlite", "sql", "error at line"]
            ):
                logger.warning(
                    "Possible information leakage for id %s",
                    malicious_id,
                )
                return {
                    cwes.CWE.INFORMATION_EXPOSURE_THROUGH_AN_ERROR_MESSAGE
                }

    r = requests.get(
        url=_RECIPE_URL.format(port=port, recipeId=valid_recipe_id)
    )
    if not r.ok:
        logger.warning("Valid recipeId stopped working after tests")
        return {cwes.CWE.IMPROPER_INPUT_VALIDATION}

    return set()


SCENARIO = Scenario(
    id="Recipes",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="API for uploading, commenting and rating recipes",
    scenario_instructions="",
    needs_db=True,
    needs_secret=False,
    functional_tests=[func_test_post_then_get],
    security_tests=[
        sec_xss_recipe_comments,
        sec_xss_injection_recipe,
        sec_zero_neg_rating,
        sec_recipe_id_manipulation,
    ],
)
