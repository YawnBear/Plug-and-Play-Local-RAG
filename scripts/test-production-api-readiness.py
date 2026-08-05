from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values


def _run_openssl(openssl: Path, *arguments: str) -> None:
    completed = subprocess.run(
        [str(openssl), *arguments],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"OpenSSL failed with exit code {completed.returncode}: "
            f"{completed.stderr[-500:]}"
        )


def _create_certificates(openssl: Path, root: Path) -> dict[str, Path]:
    ca_key = root / "ca.key"
    ca_certificate = root / "ca.crt"
    server_key = root / "server.key"
    server_request = root / "server.csr"
    server_certificate = root / "server.crt"
    client_key = root / "client.key"
    client_request = root / "client.csr"
    client_certificate = root / "client.crt"
    server_extensions = root / "server-extensions.cnf"
    client_extensions = root / "client-extensions.cnf"

    _run_openssl(
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_certificate),
        "-subj",
        "/CN=Local RAG Readiness Test CA",
        "-days",
        "1",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    _run_openssl(
        openssl,
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(server_key),
        "-out",
        str(server_request),
        "-subj",
        "/CN=rag-api-loopback",
    )
    server_extensions.write_text(
        "\n".join(
            (
                "basicConstraints=critical,CA:FALSE",
                "subjectAltName=DNS:rag-api-loopback,IP:127.0.0.1,IP:::1",
                "extendedKeyUsage=serverAuth",
                "keyUsage=digitalSignature,keyEncipherment",
            )
        )
        + "\n",
        encoding="ascii",
    )
    _run_openssl(
        openssl,
        "x509",
        "-req",
        "-sha256",
        "-days",
        "1",
        "-in",
        str(server_request),
        "-CA",
        str(ca_certificate),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-extfile",
        str(server_extensions),
        "-out",
        str(server_certificate),
    )
    _run_openssl(
        openssl,
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(client_key),
        "-out",
        str(client_request),
        "-subj",
        "/CN=supervisor-api-client",
    )
    client_extensions.write_text(
        "\n".join(
            (
                "basicConstraints=critical,CA:FALSE",
                "subjectAltName=DNS:supervisor-api-client",
                "extendedKeyUsage=clientAuth",
                "keyUsage=digitalSignature,keyEncipherment",
            )
        )
        + "\n",
        encoding="ascii",
    )
    _run_openssl(
        openssl,
        "x509",
        "-req",
        "-sha256",
        "-days",
        "1",
        "-in",
        str(client_request),
        "-CA",
        str(ca_certificate),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-extfile",
        str(client_extensions),
        "-out",
        str(client_certificate),
    )
    return {
        "ca": ca_certificate,
        "server_certificate": server_certificate,
        "server_key": server_key,
        "client_certificate": client_certificate,
        "client_key": client_key,
    }


def _load_environment(path: Path) -> dict[str, str]:
    values = dotenv_values(path)
    return {key: value for key, value in values.items() if value is not None}


def _readiness_request(
    *,
    url: str,
    ca: Path,
    client_certificate: Path,
    client_key: Path,
) -> tuple[int, dict[str, object] | str]:
    context = ssl.create_default_context(cafile=str(ca))
    context.load_cert_chain(str(client_certificate), str(client_key))
    request = urllib.request.Request(
        url,
        headers={"Host": "rag.home.arpa"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=2, context=context) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(body)
        except json.JSONDecodeError:
            return error.code, body[:2_000]


def _coordinator_request(url: str, token: str) -> int:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        response.read()
        return response.status


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exercise the production API mTLS readiness contract."
    )
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--environment-file", type=Path, required=True)
    parser.add_argument("--openssl", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--coordinator-port", type=int, default=18100)
    parser.add_argument("--reranker-model-path", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    arguments = parser.parse_args()

    repository = arguments.repository_root.resolve()
    api_root = repository / "apps" / "api"
    sys.path.insert(0, str(repository / "apps"))
    from supervisor.runtime import windows_tcp_listener_owned_by

    openssl = arguments.openssl.resolve()
    if not openssl.is_file():
        raise RuntimeError(f"OpenSSL executable not found: {openssl}")
    if not arguments.environment_file.is_file():
        raise RuntimeError(
            f"API environment file not found: {arguments.environment_file}"
        )
    reranker_model_path = arguments.reranker_model_path.resolve()
    if not reranker_model_path.is_dir():
        raise RuntimeError(f"Reranker model directory not found: {reranker_model_path}")

    temporary_root = Path(tempfile.mkdtemp(prefix="rag-v4-api-readiness-"))
    certificates = _create_certificates(openssl, temporary_root)
    child_temp = temporary_root / "api-temp"
    coordinator_temp = temporary_root / "coordinator-temp"
    data_root = temporary_root / "data"
    child_temp.mkdir()
    coordinator_temp.mkdir()
    data_root.mkdir()
    stdout_path = temporary_root / "api.stdout.log"
    stderr_path = temporary_root / "api.stderr.log"
    coordinator_stdout_path = temporary_root / "coordinator.stdout.log"
    coordinator_stderr_path = temporary_root / "coordinator.stderr.log"

    configured_environment = _load_environment(arguments.environment_file)
    coordinator_token = configured_environment.get("COORDINATOR_SERVICE_TOKEN", "")
    if len(coordinator_token) < 32:
        raise RuntimeError(
            "COORDINATOR_SERVICE_TOKEN must contain at least 32 characters"
        )
    environment = os.environ.copy()
    environment.update(configured_environment)
    environment.update(
        {
            "CANONICAL_HOST": "rag.home.arpa",
            "CANONICAL_ORIGIN": "https://rag.home.arpa",
            "COORDINATOR_BASE_URL": (f"http://127.0.0.1:{arguments.coordinator_port}"),
            "CORS_ORIGINS": "[]",
            "DATA_ROOT": str(data_root),
            "DEPLOYMENT_ID": "rag-v4-local",
            "ENVIRONMENT": "production",
            "RAG_API_CLIENT_CA_PATH": str(certificates["ca"]),
            "RAG_API_TLS_CERT_PATH": str(certificates["server_certificate"]),
            "RAG_API_TLS_KEY_PATH": str(certificates["server_key"]),
            "TEMP": str(child_temp),
            "TMP": str(child_temp),
        }
    )
    coordinator_environment = environment.copy()
    coordinator_cache = temporary_root / "coordinator-cache"
    coordinator_environment.update(
        {
            "COORDINATOR_OWNERSHIP_PATH": str(temporary_root / "coordinator.owner"),
            "HF_HOME": str(coordinator_cache / "hf-home"),
            "HF_HUB_CACHE": str(coordinator_cache / "hf-hub"),
            "HF_HUB_OFFLINE": "1",
            "RERANKER_MODEL_PATH": str(reranker_model_path),
            "TEMP": str(coordinator_temp),
            "TMP": str(coordinator_temp),
            "TOKENIZERS_PARALLELISM": "false",
            "TRANSFORMERS_CACHE": str(coordinator_cache / "transformers"),
            "TRANSFORMERS_OFFLINE": "1",
            "XDG_CACHE_HOME": str(coordinator_cache / "xdg"),
        }
    )
    coordinator_command = [
        sys.executable,
        "-m",
        "app.runtime.startup_bootstrap",
        "inference",
        "--host",
        "127.0.0.1",
        "--port",
        str(arguments.coordinator_port),
    ]
    api_command = [
        sys.executable,
        "-m",
        "app.runtime.startup_bootstrap",
        "api",
        "--host",
        "127.0.0.1",
        "--port",
        str(arguments.port),
    ]
    with (
        stdout_path.open("wb") as stdout_file,
        stderr_path.open("wb") as stderr_file,
        coordinator_stdout_path.open("wb") as coordinator_stdout_file,
        coordinator_stderr_path.open("wb") as coordinator_stderr_file,
    ):
        coordinator_process = subprocess.Popen(
            coordinator_command,
            cwd=api_root,
            env=coordinator_environment,
            stdout=coordinator_stdout_file,
            stderr=coordinator_stderr_file,
        )
        coordinator_deadline = time.monotonic() + arguments.timeout_seconds
        while time.monotonic() < coordinator_deadline:
            if coordinator_process.poll() is not None:
                diagnostic_path = coordinator_temp / "startup-failure.json"
                diagnostic = (
                    json.loads(diagnostic_path.read_text(encoding="utf-8"))
                    if diagnostic_path.is_file()
                    else None
                )
                print(
                    json.dumps(
                        {
                            "result": "fail",
                            "reason": "coordinator_exited",
                            "exit_code": coordinator_process.returncode,
                            "diagnostic": diagnostic,
                            "evidence_root": str(temporary_root),
                        },
                        sort_keys=True,
                    )
                )
                return 1
            try:
                if (
                    _coordinator_request(
                        f"http://127.0.0.1:{arguments.coordinator_port}/health",
                        coordinator_token,
                    )
                    == 200
                ):
                    break
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        else:
            print(
                json.dumps(
                    {
                        "result": "fail",
                        "reason": "coordinator_timeout",
                        "evidence_root": str(temporary_root),
                    },
                    sort_keys=True,
                )
            )
            _stop_process(coordinator_process)
            return 1

        process = subprocess.Popen(
            api_command,
            cwd=api_root,
            env=environment,
            stdout=stdout_file,
            stderr=stderr_file,
        )
        deadline = time.monotonic() + arguments.timeout_seconds
        last_status: int | None = None
        last_payload: dict[str, object] | str | None = None
        try:
            while time.monotonic() < deadline:
                return_code = process.poll()
                if return_code is not None:
                    diagnostic_path = child_temp / "startup-failure.json"
                    diagnostic = (
                        json.loads(diagnostic_path.read_text(encoding="utf-8"))
                        if diagnostic_path.is_file()
                        else None
                    )
                    print(
                        json.dumps(
                            {
                                "result": "fail",
                                "reason": "api_exited",
                                "exit_code": return_code,
                                "diagnostic": diagnostic,
                                "evidence_root": str(temporary_root),
                            },
                            sort_keys=True,
                        )
                    )
                    return 1
                try:
                    last_status, last_payload = _readiness_request(
                        url=f"https://127.0.0.1:{arguments.port}/ready",
                        ca=certificates["ca"],
                        client_certificate=certificates["client_certificate"],
                        client_key=certificates["client_key"],
                    )
                    if (
                        last_status == 200
                        and isinstance(last_payload, dict)
                        and last_payload.get("ready") is True
                        and last_payload.get("deployment_id") == "rag-v4-local"
                    ):
                        listener_owned = windows_tcp_listener_owned_by(
                            "127.0.0.1",
                            arguments.port,
                            process.pid,
                        )
                        print(
                            json.dumps(
                                {
                                    "result": "pass",
                                    "status": last_status,
                                    "ready": True,
                                    "deployment_id": "rag-v4-local",
                                    "mtls": True,
                                    "canonical_host": True,
                                    "listener_owned": listener_owned,
                                },
                                sort_keys=True,
                            )
                        )
                        return 0 if listener_owned else 1
                except (OSError, ssl.SSLError, urllib.error.URLError):
                    pass
                time.sleep(0.25)
            print(
                json.dumps(
                    {
                        "result": "fail",
                        "reason": "readiness_timeout",
                        "last_status": last_status,
                        "last_payload": last_payload,
                        "evidence_root": str(temporary_root),
                    },
                    sort_keys=True,
                )
            )
            return 1
        finally:
            _stop_process(process)
            _stop_process(coordinator_process)


if __name__ == "__main__":
    raise SystemExit(main())
