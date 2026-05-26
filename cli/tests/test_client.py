from __future__ import annotations

import httpx
import pytest

from czm_cli.client import CzmClient
from czm_cli.errors import EXIT_AUTH, EXIT_CONFLICT, EXIT_NOT_FOUND, EXIT_TRANSPORT, EXIT_USAGE


def test_client_sets_api_key_header_and_parses_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["header"] = request.headers["x-api-key"]
        return httpx.Response(200, json={"ok": True})

    client = CzmClient("http://example.test", "secret", transport=httpx.MockTransport(handler))
    try:
        assert client.get("/health") == {"ok": True}
    finally:
        client.close()
    assert seen["header"] == "secret"


@pytest.mark.parametrize(
    "status,exit_code",
    [
        (401, EXIT_AUTH),
        (404, EXIT_NOT_FOUND),
        (409, EXIT_CONFLICT),
        (422, EXIT_USAGE),
    ],
)
def test_client_maps_http_errors(status: int, exit_code: int):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": "x", "message": "boom"}})

    client = CzmClient("http://example.test", "secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Exception) as excinfo:
            client.get("/boom")
        assert getattr(excinfo.value, "exit_code") == exit_code
    finally:
        client.close()


def test_client_maps_malformed_success_json_to_transport_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not-json", headers={"content-type": "application/json"})

    client = CzmClient("http://example.test", "secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(Exception) as excinfo:
            client.get("/episodes/due")
        assert getattr(excinfo.value, "exit_code") == EXIT_TRANSPORT
        assert "unreadable" in str(excinfo.value)
    finally:
        client.close()
