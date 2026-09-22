#!/usr/bin/env python3
"""Apply OpenSearch setup operations and import Dashboards saved objects."""

from __future__ import annotations

import base64
import json
import os
import random
import ssl
import string
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def ssl_context() -> ssl.SSLContext:
    if not as_bool(env("VERIFY_TLS", "true")):
        return ssl._create_unverified_context()  # noqa: SLF001
    ca_cert = env("CA_CERT")
    return ssl.create_default_context(cafile=ca_cert or None)


SSL_CONTEXT = ssl_context()


def auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def request(
    base_url: str,
    path: str,
    *,
    username: str,
    password: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30,
) -> tuple[int, bytes]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request_headers = {
        "Accept": "application/json",
        "Authorization": auth_header(username, password),
        **(headers or {}),
    }
    req = urllib.request.Request(
        url, data=body, headers=request_headers, method=method.upper()
    )
    try:
        with urllib.request.urlopen(req, context=SSL_CONTEXT, timeout=timeout) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def wait_until_ready(
    name: str,
    base_url: str,
    path: str,
    username: str,
    password: str,
    deadline: float,
    headers: dict[str, str] | None = None,
) -> None:
    last_result = "not contacted"
    while time.monotonic() < deadline:
        try:
            status, body = request(
                base_url,
                path,
                username=username,
                password=password,
                headers=headers,
                timeout=10,
            )
            last_result = f"HTTP {status}: {body[:300].decode(errors='replace')}"
            if 200 <= status < 300:
                print(f"{name} is ready")
                return
        except (OSError, urllib.error.URLError) as exc:
            last_result = str(exc)
        time.sleep(3)
    raise RuntimeError(f"Timed out waiting for {name}; last result: {last_result}")


def apply_setup(
    setup_file: Path, base_url: str, username: str, password: str
) -> None:
    if not setup_file.exists():
        print(f"No setup file at {setup_file}; skipping cluster resources")
        return

    operations = json.loads(os.path.expandvars(setup_file.read_text()))
    if not isinstance(operations, list):
        raise ValueError("SETUP_FILE must contain a JSON array")

    for number, operation in enumerate(operations, start=1):
        method = str(operation.get("method", "PUT")).upper()
        path = operation["path"]
        accepted = operation.get("ok_statuses")
        payload = operation.get("body")
        encoded = None if payload is None else json.dumps(payload).encode()
        headers = {"Content-Type": "application/json"} if encoded is not None else {}
        status, response = request(
            base_url,
            path,
            username=username,
            password=password,
            method=method,
            body=encoded,
            headers=headers,
        )
        ok = status in accepted if accepted is not None else 200 <= status < 300
        if not ok:
            detail = response[:2000].decode(errors="replace")
            raise RuntimeError(
                f"Setup operation {number} ({method} {path}) failed: "
                f"HTTP {status}: {detail}"
            )
        print(f"Applied {method} {path} (HTTP {status})")


def multipart_file(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = "----opensearch-bootstrap-" + "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(24)
    )
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="{field}"; '
                f'filename="{filename}"\r\n'
            ).encode(),
            b"Content-Type: application/x-ndjson\r\n\r\n",
            content,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def import_saved_objects(
    objects_file: Path,
    dashboards_url: str,
    username: str,
    password: str,
    tenant: str,
) -> None:
    if not objects_file.exists():
        print(f"No saved objects file at {objects_file}; skipping import")
        return

    payload, content_type = multipart_file(
        "file", objects_file.name, objects_file.read_bytes()
    )
    overwrite = "true" if as_bool(env("IMPORT_OVERWRITE", "true")) else "false"
    headers = {"Content-Type": content_type, "osd-xsrf": "true"}
    if tenant:
        headers["securitytenant"] = tenant

    status, response = request(
        dashboards_url,
        f"/api/saved_objects/_import?overwrite={overwrite}",
        username=username,
        password=password,
        method="POST",
        body=payload,
        headers=headers,
        timeout=120,
    )
    detail = response.decode(errors="replace")
    if not 200 <= status < 300:
        raise RuntimeError(f"Saved-object import failed: HTTP {status}: {detail[:4000]}")

    result = json.loads(detail)
    if result.get("success") is not True:
        raise RuntimeError(f"Saved-object import reported errors: {detail[:4000]}")
    print(f"Imported {result.get('successCount', 0)} saved objects")


def main() -> int:
    opensearch_url = env("OPENSEARCH_URL", required=True)
    dashboards_url = env("DASHBOARDS_URL", required=True)
    os_user = env("OPENSEARCH_USERNAME", required=True)
    os_password = env("OPENSEARCH_PASSWORD", required=True)
    dashboards_user = env("DASHBOARDS_USERNAME", os_user)
    dashboards_password = env("DASHBOARDS_PASSWORD", os_password)
    tenant = env("DASHBOARDS_TENANT")
    deadline = time.monotonic() + int(env("WAIT_TIMEOUT_SECONDS", "300"))

    dashboards_headers = {"osd-xsrf": "true"}
    if tenant:
        dashboards_headers["securitytenant"] = tenant

    wait_until_ready(
        "OpenSearch",
        opensearch_url,
        "/_cluster/health?wait_for_status=yellow&timeout=5s",
        os_user,
        os_password,
        deadline,
    )
    apply_setup(
        Path(env("SETUP_FILE", "/opt/opensearch-setup/opensearch-setup.json")),
        opensearch_url,
        os_user,
        os_password,
    )
    wait_until_ready(
        "OpenSearch Dashboards",
        dashboards_url,
        "/api/status",
        dashboards_user,
        dashboards_password,
        deadline,
        dashboards_headers,
    )
    import_saved_objects(
        Path(
            env(
                "SAVED_OBJECTS_FILE",
                "/opt/opensearch-setup/saved-objects.ndjson",
            )
        ),
        dashboards_url,
        dashboards_user,
        dashboards_password,
        tenant,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
