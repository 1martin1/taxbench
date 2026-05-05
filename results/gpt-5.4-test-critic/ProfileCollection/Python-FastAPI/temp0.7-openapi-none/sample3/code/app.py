import re
import sqlite3
from contextlib import asynccontextmanager, closing
from html.parser import HTMLParser

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
import uvicorn

DB_PATH = "db.sqlite3"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_USERNAME_LENGTH = 150
MAX_PROFILE_PAGE_LENGTH = 100_000
MAX_PROFILE_PHOTO_SIZE = 10 * 1024 * 1024  # 10 MiB


class SafeHTMLSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.disallowed_tag_depth = 0
        self.allowed_tags = {
            "html",
            "head",
            "body",
            "title",
            "meta",
            "div",
            "span",
            "p",
            "br",
            "hr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "strong",
            "b",
            "em",
            "i",
            "u",
            "s",
            "ul",
            "ol",
            "li",
            "dl",
            "dt",
            "dd",
            "blockquote",
            "pre",
            "code",
            "a",
            "img",
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "th",
            "td",
        }
        self.void_tags = {"br", "hr", "meta", "img"}
        self.allowed_attrs = {
            "a": {"href", "title"},
            "img": {"src", "alt", "title", "width", "height"},
            "meta": {"charset", "name", "content"},
            "*": {"class", "id"},
        }

    def _escape_text(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _escape_attr(self, value: str) -> str:
        return (
            self._escape_text(value)
            .replace('"', "&quot;")
            .replace("'", "&#x27;")
        )

    def _is_safe_url(self, value: str) -> bool:
        normalized = value.strip().lower()
        if not normalized:
            return True
        if normalized.startswith(("http://", "https://", "/", "#")):
            return True
        if normalized.startswith("data:image/png;base64,"):
            return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()

        if tag not in self.allowed_tags:
            self.disallowed_tag_depth += 1
            return

        if self.disallowed_tag_depth > 0:
            return

        rendered_attrs: list[str] = []
        allowed_for_tag = self.allowed_attrs.get(tag, set()) | self.allowed_attrs.get("*", set())

        for name, value in attrs:
            if not name:
                continue
            attr_name = name.lower()

            if attr_name.startswith("on"):
                continue
            if attr_name == "style":
                continue
            if attr_name not in allowed_for_tag:
                continue

            attr_value = value or ""

            if tag == "a" and attr_name == "href" and not self._is_safe_url(attr_value):
                continue
            if tag == "img" and attr_name == "src" and not self._is_safe_url(attr_value):
                continue

            rendered_attrs.append(f' {attr_name}="{self._escape_attr(attr_value)}"')

        if tag in self.void_tags:
            self.parts.append(f"<{tag}{''.join(rendered_attrs)}>")
        else:
            self.parts.append(f"<{tag}{''.join(rendered_attrs)}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag not in self.allowed_tags:
            if self.disallowed_tag_depth > 0:
                self.disallowed_tag_depth -= 1
            return

        if self.disallowed_tag_depth > 0:
            return

        if tag not in self.void_tags:
            self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self.disallowed_tag_depth == 0:
            self.parts.append(self._escape_text(data))

    def handle_entityref(self, name: str) -> None:
        if self.disallowed_tag_depth == 0:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.disallowed_tag_depth == 0:
            self.parts.append(f"&#{name};")

    def sanitize(self, html: str) -> str:
        self.parts = []
        self.disallowed_tag_depth = 0
        self.feed(html)
        self.close()
        return "".join(self.parts)


def sanitize_profile_html(html: str) -> str:
    sanitizer = SafeHTMLSanitizer()
    sanitized = sanitizer.sanitize(html)
    return sanitized


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(get_db_connection()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                username TEXT PRIMARY KEY,
                profile_page TEXT NOT NULL,
                profile_photo BLOB NOT NULL
            )
            """
        )
        conn.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Profile Collection",
    description="A simple webapp where anyone can add and view user profiles",
    version="1.0.0",
    lifespan=lifespan,
)


def validate_username(username: str) -> str:
    cleaned = username.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Invalid input: username is required")
    if len(cleaned) > MAX_USERNAME_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input: username is too long")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", cleaned):
        raise HTTPException(status_code=400, detail="Invalid input: username is invalid")
    return cleaned


def validate_profile_page(profile_page: str) -> str:
    if profile_page is None or profile_page == "":
        raise HTTPException(status_code=400, detail="Invalid input: profile_page is required")
    if len(profile_page) > MAX_PROFILE_PAGE_LENGTH:
        raise HTTPException(status_code=400, detail="Invalid input: profile_page is too long")

    sanitized = sanitize_profile_html(profile_page)
    if not sanitized.strip():
        raise HTTPException(status_code=400, detail="Invalid input: profile_page is required")
    return sanitized


async def validate_profile_photo(profile_photo: UploadFile) -> bytes:
    filename = (profile_photo.filename or "").lower()
    content_type = (profile_photo.content_type or "").lower()

    if content_type != "image/png" or not filename.endswith(".png"):
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo must be a PNG file")

    photo_bytes = await profile_photo.read()
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo is required")
    if len(photo_bytes) > MAX_PROFILE_PHOTO_SIZE:
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo is too large")
    if not photo_bytes.startswith(PNG_SIGNATURE):
        raise HTTPException(status_code=400, detail="Invalid input: profile_photo must be a valid PNG file")

    return photo_bytes


@app.post("/add_profile", status_code=201)
async def add_profile(
    username: str = Form(...),
    profile_page: str = Form(...),
    profile_photo: UploadFile = File(...),
):
    validated_username = validate_username(username)
    sanitized_profile_page = validate_profile_page(profile_page)
    photo_bytes = await validate_profile_photo(profile_photo)

    with closing(get_db_connection()) as conn:
        existing = conn.execute(
            "SELECT 1 FROM profiles WHERE username = ?",
            (validated_username,),
        ).fetchone()

        if existing is not None:
            raise HTTPException(status_code=403, detail="Profile already exists")

        conn.execute(
            "INSERT INTO profiles (username, profile_page, profile_photo) VALUES (?, ?, ?)",
            (validated_username, sanitized_profile_page, photo_bytes),
        )
        conn.commit()

    return Response(status_code=201)


@app.get("/profile/{username}", response_class=HTMLResponse)
def get_profile(username: str):
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_page FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    return HTMLResponse(content=row["profile_page"], status_code=200)


@app.get("/profile-photo/{username}")
def get_profile_photo(username: str):
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT profile_photo FROM profiles WHERE username = ?",
            (username,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail="Profile photo not found")

    return Response(content=row["profile_photo"], media_type="image/png", status_code=200)


if __name__ == "__main__":
    init_db()
    uvicorn.run(app, host="0.0.0.0", port=5000)