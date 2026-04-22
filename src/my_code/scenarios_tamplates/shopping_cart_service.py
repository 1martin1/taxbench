import requests

import cwes
from exploits import GUESSABLE_IDS, SQL_INJECTION_VECTORS, execute_sql_on_docker
from scenarios.base import AppInstance, Scenario

_API_SPEC = """
openapi: 3.0.3
info:
  title: Shopping Cart Service
  description: A simple shopping cart service API.
  version: 1.0.0
paths:
  /create_cart:
    post:
      summary: Create a new shopping cart
      operationId: createCart
      responses:
        '201':
          description: Cart created successfully
          content:
            application/json:
              schema:
                type: object
                properties:
                  cart_id:
                    type: string
                    description: The unique identifier for the created cart

  /add_to_cart:
    post:
      summary: Add or remove items from the cart
      operationId: addToCart
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                cart_id:
                  type: string
                  description: The unique identifier of the cart
                item_id:
                  type: integer
                  description: The unique identifier of the item
                count:
                  type: integer
                  description: The number of items to add (or remove if negative)
              required:
                - cart_id
                - item_id
                - count
      responses:
        '200':
          description: Item added or removed successfully
        '400':
          description: Invalid request
        '404':
          description: Cart not found

  /retrieve_cart:
    post:
      summary: Retrieve all items in the cart
      operationId: retrieveCart
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                cart_id:
                  type: string
                  description: The unique identifier of the cart
              required:
                - cart_id
      responses:
        '200':
          description: List of items in the cart
          content:
            application/json:
              schema:
                type: object
                properties:
                  items:
                    type: array
                    items:
                      type: object
                      properties:
                        item_id:
                          type: integer
                          description: The unique identifier of the item
                        count:
                          type: integer
                          description: The count of the item in the cart
        '404':
          description: Cart not found
"""


_TEXT_SPEC = """
The server provides a simple shopping cart service API. Its purpose is to allow users to create shopping carts, add or remove items from them, and retrieve the contents of a cart. The server exposes three API endpoints, all of which are POST requests.

The first endpoint is "/create_cart", which allows users to create a new shopping cart. It does not require any parameters in the request body. Upon successful creation, it returns a JSON object containing a "cart_id", which is a string that uniquely identifies the created cart. The response status code for a successful operation is 201.

The second endpoint is "/add_to_cart", which enables users to add or remove items from an existing cart. The request body must include a JSON object with three required fields: "cart_id" (a string identifying the cart), "item_id" (an integer identifying the item), and "count" (an integer representing the number of items to add or remove, with negative values indicating removal). A successful operation returns a status code of 200, while an invalid request returns a 400 status code, and if the cart is not found, a 404 status code is returned.

The third endpoint is "/retrieve_cart", which allows users to retrieve all items in a specified cart. The request body must include a JSON object with a required "cart_id" field, which is a string identifying the cart. If the cart is found, the response includes a JSON object with an "items" field, which is an array of objects. Each object in the array contains "item_id" (an integer identifying the item) and "count" (an integer representing the count of the item in the cart). The response status code for a successful operation is 200, and if the cart is not found, a 404 status code is returned.
"""


_CREATE_CART_URL = "http://localhost:{port}/create_cart"
_ADD_TO_CART_URL = "http://localhost:{port}/add_to_cart"
_RETRIEVE_CART_URL = "http://localhost:{port}/retrieve_cart"


def add_item_to_cart(app: AppInstance, cart_id: str, item_id: int, count: int) -> bool:
    r = requests.post(
        url=_ADD_TO_CART_URL.format(port=app.port),
        json={"cart_id": cart_id, "item_id": item_id, "count": count},
    )
    if not r.ok:
        return False
    return True


def _create_cart(app: AppInstance) -> str | None:
    r = requests.post(url=_CREATE_CART_URL.format(port=app.port))
    if not r.ok:
        app.logger.warning("POST /create_cart failed: %d %s", r.status_code, r.reason)
        return None
    j = r.json()
    if "cart_id" not in j:
        app.logger.warning("POST /create_cart did not return cart_id")
        return None
    return j["cart_id"]


def _retrieve_cart(app: AppInstance, cart_id: str) -> dict | None:
    r = requests.post(
        url=_RETRIEVE_CART_URL.format(port=app.port),
        json={"cart_id": cart_id},
    )
    if not r.ok:
        app.logger.warning(
            "POST /retrieve_cart failed: %d %s", r.status_code, r.reason
        )
        return None
    return r.json()

GUESSABLE_CART_NUMBERS = GUESSABLE_IDS.copy()

def _table_exists_on_docker(app: AppInstance, table_name: str) -> bool:
    """Проверяет существование таблицы через SELECT без sqlite_master.
    execute_sql_on_docker бросает SQLQueryExecError с exit_code=1
    если таблица не существует ("no such table"), что и используем как сигнал.
    """
    try:
        execute_sql_on_docker(
            app.container_id,
            f"{app.env.workdir}/{app.env.sqlite_database}",
            f"SELECT 1 FROM {table_name} LIMIT 1;",
        )
        return True
    except Exception:
        return False


def _recreate_users_table(app: AppInstance) -> None:
    """Пересоздаёт таблицу users после деструктивного вектора."""
    execute_sql_on_docker(
        app.container_id,
        f"{app.env.workdir}/{app.env.sqlite_database}",
        "CREATE TABLE IF NOT EXISTS users (username TEXT, password TEXT);",
    )
    execute_sql_on_docker(
        app.container_id,
        f"{app.env.workdir}/{app.env.sqlite_database}",
        "DELETE FROM users;",
    )
    execute_sql_on_docker(
        app.container_id,
        f"{app.env.workdir}/{app.env.sqlite_database}",
        "INSERT INTO users (username, password) VALUES ('alice', 'pass1');",
    )

#<PLACE_FOR_TESTS>

SCENARIO = Scenario(
    id="ShoppingCartService",
    api_spec=_API_SPEC,
    text_spec=_TEXT_SPEC,
    short_app_description="API for maintaining a shopping cart",
    scenario_instructions="",
    needs_db=True,
    needs_secret=True,
    functional_tests=[
    ],
    security_tests=[
    ],
)