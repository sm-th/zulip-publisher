"""Tests for the preparation client contract."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from zulip_publisher.prepare import PreparationClient, PrepareError


class Handler(BaseHTTPRequestHandler):
    def __init__(self, responses, received, *args, **kwargs):
        self.responses = responses
        self.received = received
        super().__init__(*args, **kwargs)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length).decode())
        auth = self.headers.get("Authorization", "")
        status, response = self.responses.pop(0)
        self.received.append({"body": body, "auth": auth})
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, format, *args):
        pass


@pytest.fixture
def prep_server():
    responses = []
    received = []

    def make_handler(*args, **kwargs):
        return Handler(responses, received, *args, **kwargs)

    server = HTTPServer(("127.0.0.1", 0), make_handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, responses, received
    server.shutdown()


def test_prepare_request_shape(prep_server):
    server, responses, received = prep_server
    responses.append((200, {
        "fingerprint": "abc",
        "policy": "faithful-en-v1",
        "prepared_body": "Clean body.",
        "cache_hit": False,
    }))
    client = PreparationClient(f"http://127.0.0.1:{server.server_address[1]}", "tok")
    doc = client.prepare("body", title="title", policy="faithful-en-v1", fmt="markdown")

    assert doc.fingerprint == "abc"
    assert doc.policy == "faithful-en-v1"
    assert doc.body == "Clean body."
    assert doc.cache_hit is False

    assert received[0]["body"]["policy"] == "faithful-en-v1"
    assert received[0]["body"]["format"] == "markdown"
    assert received[0]["body"]["title"] == "title"
    assert received[0]["body"]["body"] == "body"
    assert received[0]["auth"] == "Bearer tok"


def test_prepare_optional_title(prep_server):
    server, responses, received = prep_server
    responses.append((200, {"id": "xyz", "body": "No title."}))
    client = PreparationClient(f"http://127.0.0.1:{server.server_address[1]}", "tok")
    doc = client.prepare("body")
    assert doc.fingerprint == "xyz"
    assert "title" not in received[0]["body"]


def test_prepare_error_raises(prep_server):
    server, responses, _ = prep_server
    responses.append((500, {"error": "model unavailable"}))
    client = PreparationClient(f"http://127.0.0.1:{server.server_address[1]}", "tok")
    with pytest.raises(PrepareError):
        client.prepare("body")
