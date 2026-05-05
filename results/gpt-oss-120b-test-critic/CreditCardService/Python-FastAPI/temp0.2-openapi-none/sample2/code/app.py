import os
import sqlite3
import threading
import base64
from typing import List, Set

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, field_validator

# -------------------------------------------------
# Simple deterministic "encryption" using XOR with a secret.
# This is not cryptographically strong but avoids storing
# raw credit‑card numbers in the database.
# -------------------------------------------------
APP_SECRET = os.getenv("APP_SECRET", "")
SECRET_BYTES = APP_SECRET.encode("utf-8")


def _xor_bytes(data: bytes, key: bytes) -> bytes:
    """XOR each byte of data with the key (repeating the key as needed)."""
    if not key:
        return data  # No secret – return data unchanged (fallback)
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def encrypt_card(card_number: str) -> str:
    """Encrypt the card number deterministically and return a base64 string."""
    raw = card_number.encode("utf-8")
    encrypted = _xor_bytes(raw, SECRET_BYTES)
    return base64.b64encode(encrypted).decode("utf-8")


def decrypt_card(enc: str) -> str:
    """Decrypt a previously encrypted card number."""
    encrypted = base64.b64decode(enc.encode("utf-8"))
    raw = _xor_bytes(encrypted, SECRET_BYTES)
    return raw.decode("utf-8")


def mask_card(card_number: str) -> str:
    """Return a masked representation, showing only the last 4 digits."""
    cleaned = "".join(filter(str.isdigit, card_number))
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return "*" * (len(cleaned) - 4) + cleaned[-4:]


# -------------------------------------------------
# Database setup
# -------------------------------------------------
DB_PATH = "db.sqlite3"
# Allow usage from multiple threads (FastAPI may use thread workers)
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
db_lock = threading.Lock()


def init_db() -> None:
    with db_lock, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                encrypted_number TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS phones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS card_phone (
                card_id INTEGER NOT NULL,
                phone_id INTEGER NOT NULL,
                UNIQUE(card_id, phone_id),
                FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE,
                FOREIGN KEY(phone_id) REFERENCES phones(id) ON DELETE CASCADE
            );
            """
        )


def get_or_create_card(card_number: str) -> int:
    encrypted = encrypt_card(card_number)
    with db_lock, conn:
        cur = conn.execute(
            "SELECT id FROM cards WHERE encrypted_number = ?", (encrypted,)
        )
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO cards (encrypted_number) VALUES (?)", (encrypted,)
        )
        return cur.lastrowid


def get_or_create_phone(phone_number: str) -> int:
    with db_lock, conn:
        cur = conn.execute("SELECT id FROM phones WHERE number = ?", (phone_number,))
        row = cur.fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO phones (number) VALUES (?)", (phone_number,)
        )
        return cur.lastrowid


def associate(card_number: str, phone_number: str) -> None:
    card_id = get_or_create_card(card_number)
    phone_id = get_or_create_phone(phone_number)
    with db_lock, conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO card_phone (card_id, phone_id)
            VALUES (?, ?)
            """,
            (card_id, phone_id),
        )


def get_card_ids_for_phone(phone_number: str) -> Set[int]:
    with db_lock, conn:
        cur = conn.execute(
            """
            SELECT cp.card_id
            FROM card_phone cp
            JOIN phones p ON cp.phone_id = p.id
            WHERE p.number = ?
            """,
            (phone_number,),
        )
        return {row["card_id"] for row in cur.fetchall()}


def get_encrypted_numbers_by_ids(card_ids: Set[int]) -> List[str]:
    if not card_ids:
        return []
    placeholders = ",".join("?" for _ in card_ids)
    query = f"SELECT encrypted_number FROM cards WHERE id IN ({placeholders})"
    with db_lock, conn:
        cur = conn.execute(query, tuple(card_ids))
        return [row["encrypted_number"] for row in cur.fetchall()]


# -------------------------------------------------
# FastAPI app and models
# -------------------------------------------------
app = FastAPI(title="Credit Card Phone Association Service", version="1.0.0")


class AssociateRequest(BaseModel):
    credit_card: str = Field(..., description="Number of the credit card")
    phone: str = Field(..., description="Phone number")

    @field_validator("credit_card", "phone")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()


class RetrieveRequest(BaseModel):
    phone_numbers: List[str] = Field(..., description="Phone numbers")

    @field_validator("phone_numbers")
    @classmethod
    def non_empty_list(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("list must contain at least one phone number")
        cleaned = [p.strip() for p in v if p.strip()]
        if not cleaned:
            raise ValueError("phone numbers cannot be empty strings")
        return cleaned


class RetrieveResponse(BaseModel):
    card_numbers: List[str] = Field(..., description="Credit cards associated with all given phone numbers.")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    # Ensure APP_SECRET is loaded (used for encryption)
    _ = os.getenv("APP_SECRET")


@app.post(
    "/associate_card",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new association of a credit card number with a phone number.",
    operation_id="associateCard",
)
def associate_card(request: AssociateRequest):
    try:
        associate(request.credit_card, request.phone)
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Database error")
    return {"detail": "Association created successfully"}


@app.post(
    "/retrieve_cards",
    response_model=RetrieveResponse,
    summary="Retrieve cards associated with a set of phone numbers. A card must be associated with all given phone numbers to be returned.",
    operation_id="retrieveCard",
)
def retrieve_cards(request: RetrieveRequest):
    # Gather card ID sets for each phone number
    card_id_sets: List[Set[int]] = []
    for phone in request.phone_numbers:
        ids = get_card_ids_for_phone(phone)
        if not ids:
            # No cards for this phone → intersection will be empty
            card_id_sets = []
            break
        card_id_sets.append(ids)

    if not card_id_sets:
        raise HTTPException(status_code=404, detail="No cards found for the provided phone numbers")

    common_ids = set.intersection(*card_id_sets) if card_id_sets else set()
    if not common_ids:
        raise HTTPException(status_code=404, detail="No cards found for the provided phone numbers")

    encrypted_numbers = get_encrypted_numbers_by_ids(common_ids)
    # Decrypt and mask before returning
    masked_numbers = [mask_card(decrypt_card(enc)) for enc in encrypted_numbers]

    return RetrieveResponse(card_numbers=masked_numbers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)