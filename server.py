"""Calculadora con historial JSON: SQLite local o Azure Cosmos DB en producción.

No existe endpoint público para leer los registros con direcciones IP.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import math
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent
MAX_BODY_BYTES = 2048
MAX_ABS_NUMBER = Decimal("1000000000000")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class DatabaseError(Exception):
    """No se ha podido persistir el registro."""


class SQLiteStore:
    """Modo de desarrollo: documentos JSON en una base SQLite persistente."""

    def __init__(self, path: str):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sumas ("
                "id TEXT PRIMARY KEY, fecha_hora_utc TEXT NOT NULL, "
                "documento_json TEXT NOT NULL)"
            )

    def save(self, record: dict) -> None:
        try:
            with self._lock, sqlite3.connect(self.path, timeout=10) as conn:
                conn.execute(
                    "INSERT INTO sumas (id, fecha_hora_utc, documento_json) VALUES (?, ?, ?)",
                    (record["id"], record["fecha_hora_utc"],
                     json.dumps(record, ensure_ascii=False, allow_nan=False)),
                )
        except sqlite3.Error as exc:
            raise DatabaseError("No se pudo guardar el registro local") from exc


class CosmosStore:
    """Modo de Azure: guarda cada suma como documento en Cosmos DB for NoSQL."""

    def __init__(self, endpoint: str, key: str, database: str, container: str):
        if not endpoint or not key:
            raise ValueError("Faltan COSMOS_ENDPOINT o COSMOS_KEY en Azure")
        from azure.cosmos import CosmosClient  # Instalado por requirements.txt

        self.client = CosmosClient(endpoint, credential=key)
        self.container = self.client.get_database_client(database).get_container_client(container)

    def save(self, record: dict) -> None:
        try:
            self.container.create_item(body=record)
        except Exception as exc:
            # No enviar detalles del SDK ni claves al usuario público.
            raise DatabaseError("No se pudo guardar el registro en Cosmos DB") from exc


def create_store():
    mode = os.getenv("DB_BACKEND") or ("cosmos" if os.getenv("WEBSITE_SITE_NAME") else "sqlite")
    if mode == "sqlite":
        if os.getenv("WEBSITE_SITE_NAME"):
            raise ValueError("SQLite no se permite en App Service: utiliza DB_BACKEND=cosmos")
        return SQLiteStore(os.getenv("SQLITE_PATH", str(BASE_DIR / "data" / "sumas.sqlite3")))
    if mode == "cosmos":
        return CosmosStore(
            os.getenv("COSMOS_ENDPOINT", ""), os.getenv("COSMOS_KEY", ""),
            os.getenv("COSMOS_DATABASE", "CalculadoraDB"),
            os.getenv("COSMOS_CONTAINER", "sumas"),
        )
    raise ValueError("DB_BACKEND debe ser 'sqlite' o 'cosmos'")


def parse_forwarded_ip(value: str):
    """Acepta IP, IPv4:puerto y [IPv6]:puerto de X-Forwarded-For."""
    address = value.strip().strip('"').strip()
    try:
        return ipaddress.ip_address(address)
    except ValueError:
        pass

    if address.startswith("[") and "]" in address:
        host, suffix = address[1:].split("]", 1)
        if not suffix or (suffix.startswith(":") and suffix[1:].isascii()
                          and suffix[1:].isdigit() and 0 <= int(suffix[1:]) <= 65535):
            try:
                return ipaddress.IPv6Address(host)
            except ValueError:
                return None
        return None

    host, separator, port = address.rpartition(":")
    if separator and port.isascii() and port.isdigit() and 0 <= int(port) <= 65535:
        try:
            return ipaddress.IPv4Address(host)
        except ValueError:
            pass
    return None


def first_valid_ip(value: str) -> str | None:
    """Obtiene la IP pública más próxima al proxy en X-Forwarded-For.

    Se inspecciona de derecha a izquierda para no privilegiar prefijos
    posiblemente inyectados por el cliente. No se usan IP internas.
    No debe considerarse prueba de identidad del visitante.
    """
    for candidate in reversed(value.split(",")[-10:]):
        ip = parse_forwarded_ip(candidate)
        if ip is not None and ip.is_global:
            return str(ip)
    return None


def client_ip(handler: BaseHTTPRequestHandler) -> str:
    peer = handler.client_address[0]
    if os.getenv("TRUST_PROXY_HEADERS") != "1":
        return peer

    # En Azure App Service, el contenedor recibe conexiones del proxy
    # interno 169.254.0.0/16. No confiar en cabeceras de clientes directos.
    try:
        is_azure_internal_proxy = ipaddress.ip_address(peer).is_link_local
    except ValueError:
        return "no disponible"
    if not is_azure_internal_proxy:
        return peer

    # Azure puede enviar IP:puerto. Nunca guardar la IP del proxy
    # como si fuera la del visitante cuando falta una IP pública.
    forwarded_values = handler.headers.get_all("X-Forwarded-For", [])
    return first_valid_ip(",".join(forwarded_values)) or "no disponible"


def validate_numbers(data) -> tuple[Decimal, Decimal]:
    if not isinstance(data, dict) or set(data) != {"a", "b"}:
        raise ValueError("Envía exactamente los campos a y b")
    result = []
    for key in ("a", "b"):
        value = data[key]
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("Ambas celdillas deben contener números")
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError("Número inválido") from exc
        if not number.is_finite() or abs(number) > MAX_ABS_NUMBER:
            raise ValueError("Cada número debe estar entre -10^12 y 10^12")
        result.append(number)
    return result[0], result[1]


def as_json_number(number: Decimal) -> int | float:
    if number == number.to_integral_value():
        return int(number)
    value = float(number)
    if not math.isfinite(value):
        raise ValueError("Número fuera de rango")
    return value


def make_record(data, ip: str) -> dict:
    a, b = validate_numbers(data)
    instant = datetime.now(timezone.utc)
    return {
        "id": str(uuid.uuid4()),
        "tipo": "suma",  # Clave de partición en Cosmos DB: /tipo
        "fecha_hora_utc": instant.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "fecha_utc": instant.strftime("%Y-%m-%d"),
        "hora_utc": instant.strftime("%H:%M:%S.%f")[:-3],
        "ip_cliente": ip,
        "primera_celdilla": as_json_number(a),
        "segunda_celdilla": as_json_number(b),
        "resultado": as_json_number(a + b),
    }


class AppHandler(BaseHTTPRequestHandler):
    store = None

    def respond(self, status: int, content: bytes, mime: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def respond_json(self, status: int, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.respond(status, data, "application/json; charset=utf-8")

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/health":
            self.respond_json(200, {"estado": "ok"})
        elif route in STATIC_FILES:
            file_name, mime = STATIC_FILES[route]
            self.respond(200, (BASE_DIR / file_name).read_bytes(), mime)
        else:
            self.respond_json(404, {"error": "Ruta no encontrada"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/sumar":
            self.respond_json(404, {"error": "Ruta no encontrada"})
            return
        if self.headers.get_content_type() != "application/json":
            self.respond_json(415, {"error": "Se requiere Content-Type: application/json"})
            return
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.respond_json(411, {"error": "Content-Length inválido"})
            return
        if not (1 <= n <= MAX_BODY_BYTES):
            self.respond_json(413, {"error": "Tamaño de solicitud inválido"})
            return
        try:
            data = json.loads(self.rfile.read(n), parse_float=Decimal,
                              parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
            record = make_record(data, client_ip(self))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.respond_json(400, {"error": "Datos inválidos. Comprueba los dos números."})
            return
        try:
            self.store.save(record)
        except DatabaseError:
            logging.exception("Falló la escritura en la base de datos")
            self.respond_json(503, {"error": "La base de datos no está disponible. La suma no se guardó."})
            return
        self.respond_json(200, {
            "resultado": record["resultado"],
            "fecha_hora_utc": record["fecha_hora_utc"],
            "guardado": True,
        })


def make_handler(store):
    return type("ConfiguredAppHandler", (AppHandler,), {"store": store})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = create_store()
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(store))
    logging.info("Servidor iniciado en 0.0.0.0:%s con DB_BACKEND=%s", port, type(store).__name__)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
