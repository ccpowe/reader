#!/usr/bin/env python3
"""Verify that the task-owned Lightpanda socket answers its bounded probe."""

from __future__ import annotations

import socket

SOCKET = "/run/reader-browser-task/browser/lightpanda.sock"
REQUEST = b"GET /json/version HTTP/1.0\r\nHost: localhost\r\n\r\n"

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.settimeout(1)
    client.connect(SOCKET)
    client.sendall(REQUEST)
    response = client.recv(8192)
if not response.startswith(b"HTTP/1.1 200") and not response.startswith(b"HTTP/1.0 200"):
    raise SystemExit("Lightpanda health response was not HTTP 200")
