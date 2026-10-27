"""
Simula al módulo ESP32-C5 enviando capturas RedEdge por HTTP,
exactamente con el mismo protocolo que usará el firmware.

Sirve para probar la conexión por red sin tener el hardware,
por ejemplo desde otra computadora conectada al mismo Wi-Fi.

Uso (desde la carpeta del proyecto):

    python scripts/simular_esp32.py
    python scripts/simular_esp32.py --url http://192.168.1.50:5050 --capturas 20
    python scripts/simular_esp32.py --incompleta   # a la última captura le falta una banda

Antes, inicia la recepción en la pantalla "Adquisición de datos".
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.esp32_simulator_service import (  # noqa: E402
    capture_filename,
    generate_capture,
)


DEVICE_ID = "simulador-script"


def headers(key: str | None) -> dict:
    result = {"X-Device-Id": DEVICE_ID}
    if key:
        result["X-Device-Key"] = key
    return result


def ping(url: str, key: str | None) -> dict:
    response = requests.get(
        f"{url}/api/esp32/ping",
        headers=headers(key),
        timeout=5,
    )

    if response.status_code == 401:
        raise SystemExit(
            "Clave incorrecta: revisa --clave y ESP32_DEVICE_KEY."
        )

    response.raise_for_status()
    return response.json()


def send_band(
    url: str,
    key: str | None,
    path: str,
    content: bytes,
) -> requests.Response:

    name = path.rsplit("/", 1)[-1]

    return requests.post(
        f"{url}/api/esp32/upload",
        params={"filename": name},
        data=content,
        headers={
            **headers(key),
            "Content-Type": "application/octet-stream",
            "X-Path": path,
        },
        timeout=60,
    )


def main() -> int:

    parser = argparse.ArgumentParser(
        description="Simula al ESP32 enviando capturas RedEdge por HTTP."
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:5050",
        help="Dirección de la laptop (la que muestra la pantalla).",
    )
    parser.add_argument("--capturas", type=int, default=12)
    parser.add_argument(
        "--pausa",
        type=float,
        default=0.3,
        help="Segundos entre bandas.",
    )
    parser.add_argument(
        "--clave",
        default=None,
        help="Valor de ESP32_DEVICE_KEY si la definiste.",
    )
    parser.add_argument(
        "--incompleta",
        action="store_true",
        help="A la última captura le falta la banda 5.",
    )
    args = parser.parse_args()

    url = args.url.rstrip("/")

    try:
        state = ping(url, args.clave)
    except requests.RequestException as exc:
        print(f"No se pudo conectar con {url}: {exc}")
        print(
            "Revisa que Flask esté corriendo, FLASK_HOST=0.0.0.0 "
            "y el Firewall de Windows."
        )
        return 1

    if not state.get("receiving"):
        print(
            "La laptop responde, pero no está recibiendo. "
            "Inicia la recepción en 'Adquisición de datos'."
        )
        return 1

    print(f"Conectado. Enviando a {state.get('campaign_code')}.")

    alphabet = string.ascii_letters + string.digits
    flight_id = "".join(secrets.choice(alphabet) for _ in range(20))
    started = datetime.now()
    set_name = "SIM" + started.strftime("%Y%m%d%H%M%S") + "SET"

    saved = 0

    for index in range(args.capturas):

        bands = generate_capture(
            index,
            flight_id=flight_id,
            captured_at=started + timedelta(seconds=2 * index),
            seed=int(started.timestamp()) + index,
        )

        if args.incompleta and index == args.capturas - 1:
            bands.pop(5)

        for band, content in bands.items():

            path = capture_filename(set_name, index, band)

            try:
                response = send_band(url, args.clave, path, content)
            except requests.RequestException as exc:
                print(f"  {path}: error de red ({exc})")
                return 1

            payload = response.json() if response.content else {}

            if response.status_code != 200:
                print(
                    f"  {path}: {response.status_code} "
                    f"{payload.get('msg', '')}"
                )
                return 1

            if payload.get("capture_status") == "saved":
                saved += 1
                print(
                    f"Captura {index + 1}/{args.capturas} guardada "
                    f"(5 bandas)."
                )
            elif payload.get("capture_status") == "error":
                print(f"Captura {index + 1}: error {payload.get('msg')}")

            if args.pausa:
                time.sleep(args.pausa)

    print(f"Listo: {saved} capturas guardadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
