from __future__ import annotations

import base64
from typing import Callable, Any, Literal
from urllib import parse
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Request
from fastapi import HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from criterions import BaseCriterion, NameCriterion
from helper_functions import strip_email


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Port your modifier service listens on.
    sub_port: int = 8080
    base_sub_service_url: str = "sub"

    # Upstream 3x-ui subscription endpoint pieces.
    base_sub_port: int = 2053
    base_sub_url: str = "unconfigured"
    upstream_host: str = "127.0.0.1"

    # Rewrites applied to each supported link.
    target_host: str = "inbound.anti-vpn.ru"
    target_port: int = 443
    config_alpn: str = "http/1.1,h2"
    config_host: str = ""  # This goes into XHTTP host settings!
    config_security: Literal["none", "reality", "tls"] = "tls"

    target_link_modifier: Callable[[str], str] | str | None = None
    target_sni: str | None = None

    # Optional request timeout for the upstream fetch.
    upstream_timeout_seconds: float = 10.0

    @field_validator("config_alpn", "config_security", mode="before")
    @classmethod
    def lowercase_everything(cls, value: str):
        return value.lower() #TLS -> tls, HTTP/1.1,H2 -> http/1.1,h2

    def model_post_init(self, context: Any, /) -> None:
        if self.target_link_modifier is None:
            def _(arg: str):
                return str(arg)
            self.target_link_modifier = _
        elif isinstance(self.target_link_modifier, str):
            value = str(self.target_link_modifier)
            def _(arg: str):
                return str(value)
            self.target_link_modifier = _


settings = Settings()
async_client = httpx.AsyncClient()
app = FastAPI()


# ----------------------------
# Query parameter helpers
# ----------------------------

def _split_query(query: str) -> list[tuple[str, str]]:
    return parse_qsl(query, keep_blank_values=True)


def add_query_param(url: str, name: str, value: str) -> str:
    """Add a query parameter without removing existing duplicates."""
    parts = urlsplit(url)
    items = _split_query(parts.query)
    items.append((name, value))
    new_query = urlencode(items, doseq=True, quote_via=quote)
    return urlunsplit(parts._replace(query=new_query))


def remove_query_param(url: str, name: str) -> str:
    """Remove all occurrences of a query parameter."""
    parts = urlsplit(url)
    items = [(k, v) for k, v in _split_query(parts.query) if k != name]
    new_query = urlencode(items, doseq=True, quote_via=quote)
    return urlunsplit(parts._replace(query=new_query))


def set_query_param(url: str, name: str, value: str) -> str:
    """Replace a query parameter by removing existing copies and adding one value."""
    return add_query_param(remove_query_param(url, name), name, value)


# ----------------------------
# URL component helpers
# ----------------------------

def _format_host_for_netloc(host: str) -> str:
    host = host.strip()
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def replace_host_and_port(url: str, host: str, port: int) -> str:
    """Swap the host and port while preserving userinfo, path, query, and fragment."""
    parts = urlsplit(url)

    userinfo = ""
    if parts.username is not None:
        userinfo = quote(parts.username, safe="")
        if parts.password is not None:
            userinfo += f":{quote(parts.password, safe='')}"
        userinfo += "@"

    new_netloc = f"{userinfo}{_format_host_for_netloc(host)}:{port}"
    return urlunsplit(parts._replace(netloc=new_netloc))


def replace_fragment(url: str, fragment: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(fragment=fragment))


def decode_base64_subscription(payload: str) -> str:
    raw = payload.strip()
    compact = "".join(raw.split())
    try:
        return base64.b64decode(compact, validate=True).decode("utf-8", errors="replace")
    except Exception:
        # Some upstreams may already return plain text or non-strict base64.
        try:
            return payload
        except Exception:
            payload = base64.b64decode(compact + "===").decode("utf-8", errors="replace")
            return payload


def encode_base64_subscription(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def strip_email(text: str) -> str:
    return text[:text.rfind("-")]


def rewrite_subscription_text(decoded_text: str,
                              criterion: BaseCriterion | Callable[[str], bool] = lambda x: True,
                              constant_modifier: Callable[[str], str] | None = None
                              ) -> str:
    lines = decoded_text.splitlines()
    rewritten_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        stripped = strip_email(stripped)
        if criterion(stripped):
            rewritten_lines.append(rewrite_subscription_link(stripped))
        else:
            rewritten_lines.append(stripped)

    return "\n".join(rewritten_lines)


async def fetch_upstream_subscription(sub_id: str, client: httpx.AsyncClient) -> str:
    upstream_url = "https://handler.somenewsteps.space:2096/sub/pedriodri"
    #upstream_url = f"http://{settings.upstream_host}:{settings.base_sub_port}/{settings.base_sub_url.strip('/')}/{sub_id}"

    timeout = httpx.Timeout(settings.upstream_timeout_seconds)

    response = await client.get(upstream_url, timeout=timeout)
    response.raise_for_status()

    return response.text


# ----------------------------
# Link rewriting
# ----------------------------

def rewrite_subscription_link(link: str) -> str:
    link = link.strip()
    if not link:
        return link

    parts = urlsplit(link)
    if parts.scheme.lower() not in {"vless", "vmess", "trojan", "ss", "socks", "http", "https"}:
        return link

    # 1) Host + port rewrite.
    rewritten = replace_host_and_port(link, settings.target_host, settings.target_port)

    # 2) Required / requested query parameter changes.
    rewritten = set_query_param(rewritten, "alpn", settings.config_alpn)
    rewritten = set_query_param(rewritten, "host", settings.config_host)  # keep it present even when empty
    rewritten = set_query_param(rewritten, "security", settings.config_security)

    # Optional server-name-related query rewrite.
    if settings.target_sni:
        rewritten = set_query_param(rewritten, "sni", settings.target_sni)

    # 3) Fragment / display name change.
    _ = (settings.target_link_modifier(rewritten))
    rewritten = replace_fragment(rewritten, "this is a new and fresh uwu")

    return rewritten


# ----------------------------
# Routes
# ----------------------------

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    print("We're in the exception handlier")
    if exc.status_code == 405:
        print("nf")
        return PlainTextResponse("jew", status_code=404)
    if exc.status_code // 100 == 5:
        print("nf2")
        return PlainTextResponse("jew2", status_code=404)
    return PlainTextResponse(exc.detail, status_code=exc.status_code)


@app.get("/healthzzz")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/{full_path:path}")
async def subscription_modifier(full_path: str, request: Request) -> Response:
    print("I got something for", full_path, "\n", str(request.__dict__))
    expected_prefix = settings.base_sub_service_url.strip("/")
    if not expected_prefix:
        raise HTTPException(status_code=500, detail="BASE_SUB_URL is not configured")

    full_path = full_path.strip("/")
    prefix = f"{expected_prefix}/"
    # if not full_path.startswith(prefix):
    #     raise HTTPException(status_code=418)

    sub_id = full_path[len(prefix):]
    if not sub_id:
        raise HTTPException(status_code=419)

    upstream_body = await fetch_upstream_subscription(sub_id, async_client)
    decoded = decode_base64_subscription(upstream_body)
    rewritten_text = rewrite_subscription_text(decoded, criterion=NameCriterion("TLS"),) #constant_modifier=strip_email)
    encoded = rewritten_text
    #encoded = encode_base64_subscription(rewritten_text)

    # Plain text is what most subscription clients expect.
    return Response(content=encoded, media_type="text/plain; charset=utf-8")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=settings.sub_port, reload=False, proxy_headers=True)
