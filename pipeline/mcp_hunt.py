"""Optional MCP job sources. Indeed's official server is OAuth (Cursor), not Camoufox."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pipeline.config import Config
from pipeline.search import html_to_text, hunt_queries

log = logging.getLogger(__name__)

INDEED_MCP_URL = "https://mcp.indeed.com/claude/mcp"

SEARCH_TOOL_HINTS = ("search_jobs", "job_search", "searchjobs", "search")
DETAIL_KEYS = ("title", "job_title", "name", "role")
COMPANY_KEYS = ("company", "company_name", "employer", "organization")
URL_KEYS = ("url", "job_url", "link", "apply_url", "view_url", "absolute_url")
JD_KEYS = ("description", "job_description", "snippet", "summary", "contents", "jd")
LOC_KEYS = ("location", "formatted_location", "city")


def mcp_indeed_enabled(cfg: Config) -> bool:
    return bool(cfg.get("hunt.mcp.indeed.enabled", False))


def _as_list(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(item).strip() for item in raw if str(item).strip()]


def _first_str(item: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("name") or value.get("url") or value.get("text")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return ""


def listings_from_mcp_payload(payload: Any, source: str = "mcp:indeed") -> list[dict]:
    """Turn Indeed/HasData-style MCP JSON into hunt listings."""
    if payload is None:
        return []
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []
    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("jobs", "results", "listings", "data", "items"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break
        if not items:
            role = _first_str(payload, DETAIL_KEYS)
            url = _first_str(payload, URL_KEYS)
            if role or url:
                items = [payload]
    listings = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        role = _first_str(item, DETAIL_KEYS)
        url = _first_str(item, URL_KEYS)
        company = _first_str(item, COMPANY_KEYS) or "Unknown"
        if not role:
            continue
        key = (url or f"{company}:{role}").lower()
        if key in seen:
            continue
        seen.add(key)
        jd = _first_str(item, JD_KEYS)
        listings.append(
            {
                "company": company,
                "role": role[:160],
                "url": url,
                "location": _first_str(item, LOC_KEYS),
                "jd": html_to_text(jd) if jd else "",
                "source": source,
            }
        )
    return listings


def _pick_search_tool(tools: list) -> Any | None:
    named = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            continue
        named.append((name.lower().replace(" ", "_"), tool))
    for hint in SEARCH_TOOL_HINTS:
        for slug, tool in named:
            if hint in slug:
                return tool
    return named[0][1] if named else None


def _tool_arguments(tool, query: str, location: str) -> dict:
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    keys = set(props.keys()) if isinstance(props, dict) else set()
    args: dict[str, str] = {}
    if not keys:
        return {"query": query, "location": location}
    for candidate, value in (
        ("query", query),
        ("keywords", query),
        ("q", query),
        ("what", query),
        ("title", query),
        ("search", query),
        ("location", location),
        ("where", location),
        ("l", location),
        ("city", location),
    ):
        if candidate in keys and value:
            args[candidate] = value
    if not args:
        required = schema.get("required") if isinstance(schema, dict) else []
        if required:
            args[str(required[0])] = query
        else:
            args["query"] = query
    return args


def _content_to_payload(result: Any) -> Any:
    if result is None:
        return None
    if isinstance(result, (dict, list)):
        return result
    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
        if content is None:
            return result
    texts = []
    for block in content or []:
        if isinstance(block, dict):
            if block.get("type") == "text":
                texts.append(block.get("text") or "")
            continue
        text = getattr(block, "text", None)
        if text:
            texts.append(text)
    blob = "\n".join(texts).strip()
    if not blob:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", blob, re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                return None
        return None


async def harvest_mcp_listings(cfg: Config) -> list[dict]:
    """Call Indeed MCP if enabled. OAuth usually only works from Cursor, not this process."""
    if not mcp_indeed_enabled(cfg):
        return []
    url = (cfg.get("hunt.mcp.indeed.url") or INDEED_MCP_URL).strip()
    timeout = float(cfg.get("hunt.mcp.indeed.timeout_seconds") or 12)
    headers = cfg.get("hunt.mcp.indeed.headers") or {}
    if not isinstance(headers, dict):
        headers = {}
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:
        log.info("Indeed MCP skipped — install the mcp package, or connect Indeed in Cursor Settings → MCP.")
        return []

    queries = hunt_queries(cfg)
    city = (cfg.get("user.city") or "").strip()
    country = (cfg.get("user.country") or "").strip()
    location = f"{city}, {country}".strip(", ") or country or "Canada"
    listings: list[dict] = []
    try:
        import asyncio

        async def _run() -> list[dict]:
            found: list[dict] = []
            http_headers = {str(k): str(v) for k, v in headers.items() if v}
            client_kwargs = {}
            if http_headers:
                client_kwargs["headers"] = http_headers
            async with streamablehttp_client(url, **client_kwargs) as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    catalog = await session.list_tools()
                    tools = list(getattr(catalog, "tools", None) or catalog or [])
                    tool = _pick_search_tool(tools)
                    if tool is None:
                        log.info("Indeed MCP connected but exposed no search tool.")
                        return []
                    name = getattr(tool, "name", "search_jobs")
                    for query in queries[:2]:
                        args = _tool_arguments(tool, query, location)
                        log.info("Indeed MCP %s %s", name, query)
                        result = await session.call_tool(name, args)
                        found.extend(listings_from_mcp_payload(_content_to_payload(result), "mcp:indeed"))
            return found

        listings = await asyncio.wait_for(_run(), timeout=timeout)
    except Exception as exc:
        log.info(
            "Indeed MCP not used (%s). Connect it in Cursor Settings → MCP, or hunt via Camoufox/saved jobs.",
            exc.__class__.__name__,
        )
        return []
    log.info("Indeed MCP returned %s posting(s).", len(listings))
    return listings
