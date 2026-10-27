"""
Simulador del módulo ESP32-C5 para trabajar sin el hardware.

Genera capturas MicaSense RedEdge falsas (5 bandas TIFF con los
mismos metadatos que la cámara real: GPS, altitud, fecha, banda,
CaptureId y FlightId) y las entrega a la recepción exactamente
como lo haría el ESP32, una banda a la vez.

Las imágenes representan surcos de cultivo sobre suelo, con
algunas plantas con síntomas (clorosis: menos NIR y más rojo y
verde), para que los índices espectrales tengan algo que mostrar.

Uso:
- Desde la interfaz: botón "Simular ESP32" en Adquisición de datos.
- Por Wi-Fi/HTTP: scripts/simular_esp32.py (usa generate_capture).
"""

from __future__ import annotations

import io
import math
import secrets
import string
import threading
from datetime import datetime, timedelta

import numpy as np
from rasterio.io import MemoryFile

from services.esp32_reception_service import (
    Esp32ReceptionError,
    Esp32ReceptionService,
    NoActiveReceptionError,
)


class Esp32SimulatorError(Exception):
    """Error del simulador."""


# Banda (índice MicaSense) -> (nombre XMP, longitud de onda nm)
BANDS = {
    1: ("Blue", 475),
    2: ("Green", 560),
    3: ("Red", 668),
    4: ("NIR", 840),
    5: ("Red edge", 717),
}

# Reflectancia aproximada (0-1) por cobertura y banda.
REFLECTANCE = {
    "soil":     {1: 0.08, 2: 0.10, 3: 0.14, 4: 0.22, 5: 0.18},
    "healthy":  {1: 0.04, 2: 0.09, 3: 0.04, 4: 0.48, 5: 0.22},
    "infected": {1: 0.06, 2: 0.15, 3: 0.11, 4: 0.30, 5: 0.21},
}

# Zona de estudio por defecto (La Molina, Lima).
DEFAULT_CENTER = (-12.0834, -76.9420)
DEFAULT_ALTITUDE_M = 290.0

IMAGE_WIDTH = 640
IMAGE_HEIGHT = 480

DN_SCALE = 40000.0


def _random_id(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(
        secrets.choice(alphabet) for _ in range(length)
    )


def _to_dms(value: float) -> str:
    """Grados decimales -> formato EXIF que usa la cámara."""

    value = abs(value)
    degrees = int(value)
    minutes_full = (value - degrees) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60

    return f"({degrees}) ({minutes}) ({seconds:.5f})"


def capture_position(
    index: int,
    *,
    center: tuple[float, float] = DEFAULT_CENTER,
    per_line: int = 6,
    spacing_m: float = 10.0,
) -> tuple[float, float]:
    """
    Recorrido en zigzag (como una misión de mapeo) alrededor
    del centro de la parcela.
    """

    line = index // per_line
    step = index % per_line

    if line % 2 == 1:
        step = per_line - 1 - step

    east_m = (step - (per_line - 1) / 2) * spacing_m
    north_m = -line * spacing_m

    latitude = center[0] + north_m / 111_320.0
    longitude = center[1] + east_m / (
        111_320.0 * math.cos(math.radians(center[0]))
    )

    return latitude, longitude


def _build_xmp(
    *,
    band_name: str,
    wavelength: int,
    capture_id: str,
    flight_id: str,
) -> str:

    return (
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
        'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:MicaSense="http://micasense.com/MicaSense/1.0/" '
        'xmlns:Camera="http://pix4d.com/1.0">'
        "<rdf:RDF><rdf:Description>"
        f"<MicaSense:FlightId>{flight_id}</MicaSense:FlightId>"
        f"<MicaSense:CaptureId>{capture_id}</MicaSense:CaptureId>"
        f"<Camera:BandName>{band_name}</Camera:BandName>"
        f"<Camera:CentralWavelength>{wavelength}</Camera:CentralWavelength>"
        "</rdf:Description></rdf:RDF></x:xmpmeta>"
    )


def _field_cover(rng: np.random.Generator) -> np.ndarray:
    """
    Mapa de cobertura: 0 suelo, 1 planta sana, 2 planta con
    síntomas. Surcos horizontales con plantas redondeadas.
    """

    height, width = IMAGE_HEIGHT, IMAGE_WIDTH
    cover = np.zeros((height, width), dtype=np.uint8)

    yy, xx = np.mgrid[0:height, 0:width]

    row_spacing = 80
    plant_spacing = 70
    offset_y = rng.integers(20, 60)
    offset_x = rng.integers(0, plant_spacing)

    # Zona con síntomas en esta imagen (puede no haber).
    infected_center = None

    if rng.random() < 0.6:
        infected_center = (
            rng.integers(0, height),
            rng.integers(0, width),
        )
        infected_radius = rng.integers(90, 200)

    for row_y in range(offset_y, height + row_spacing, row_spacing):
        for plant_x in range(
            offset_x - plant_spacing,
            width + plant_spacing,
            plant_spacing,
        ):
            cx = plant_x + rng.integers(-8, 9)
            cy = row_y + rng.integers(-6, 7)
            radius = rng.integers(18, 30)

            mask = (xx - cx) ** 2 + (yy - cy) ** 2 <= radius ** 2

            infected = (
                infected_center is not None
                and (cx - infected_center[1]) ** 2
                + (cy - infected_center[0]) ** 2
                <= infected_radius ** 2
            )

            cover[mask] = 2 if infected else 1

    return cover


def _band_image(
    cover: np.ndarray,
    band: int,
    rng: np.random.Generator,
) -> np.ndarray:

    values = np.zeros(cover.shape, dtype=np.float32)

    for code, name in ((0, "soil"), (1, "healthy"), (2, "infected")):
        values[cover == code] = REFLECTANCE[name][band]

    noise = rng.normal(0, 0.008, cover.shape).astype(np.float32)
    values = np.clip(values + noise, 0.001, 1.0)

    return (values * DN_SCALE).astype(np.uint16)


def _tiff_bytes(
    data: np.ndarray,
    *,
    exif: dict,
    xmp: str,
) -> bytes:

    height, width = data.shape

    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=width,
            height=height,
            count=1,
            dtype="uint16",
        ) as dataset:
            dataset.write(data, 1)
            dataset.update_tags(ns="EXIF", **exif)
            dataset.update_tags(ns="xml:XMP", **{"xml:XMP": xmp})

        return memory_file.read()


def generate_capture(
    index: int,
    *,
    flight_id: str,
    captured_at: datetime,
    center: tuple[float, float] = DEFAULT_CENTER,
    altitude_m: float = DEFAULT_ALTITUDE_M,
    seed: int | None = None,
) -> dict[int, bytes]:
    """
    Devuelve {índice_banda: bytes_tiff} para una captura.
    """

    rng = np.random.default_rng(
        seed if seed is not None else index
    )

    latitude, longitude = capture_position(index, center=center)
    capture_id = _random_id()

    exif = {
        "EXIF_GPSLatitude": _to_dms(latitude),
        "EXIF_GPSLatitudeRef": "S" if latitude < 0 else "N",
        "EXIF_GPSLongitude": _to_dms(longitude),
        "EXIF_GPSLongitudeRef": "W" if longitude < 0 else "E",
        "EXIF_GPSAltitude": f"({altitude_m + rng.normal(0, 0.4):.3f})",
        "EXIF_GPSAltitudeRef": "0x00",
        "EXIF_DateTimeOriginal": captured_at.strftime(
            "%Y:%m:%d %H:%M:%S"
        ),
    }

    cover = _field_cover(rng)

    return {
        band: _tiff_bytes(
            _band_image(cover, band, rng),
            exif=exif,
            xmp=_build_xmp(
                band_name=name,
                wavelength=wavelength,
                capture_id=capture_id,
                flight_id=flight_id,
            ),
        )
        for band, (name, wavelength) in BANDS.items()
    }


def capture_filename(set_name: str, index: int, band: int) -> str:
    return f"{set_name}/000/IMG_{index:04d}_{band}.tif"


class Esp32SimulatorService:
    """
    Ejecuta la simulación en un hilo y entrega cada banda a
    Esp32ReceptionService como si llegara del ESP32.
    """

    DEVICE_ID = "simulador-esp32"

    MAX_CAPTURES = 200

    _lock = threading.Lock()
    _thread: threading.Thread | None = None
    _stop_event = threading.Event()
    _state: dict = {
        "running": False,
        "total": 0,
        "sent_captures": 0,
        "sent_files": 0,
        "error": None,
    }

    @classmethod
    def status(cls) -> dict:
        with cls._lock:
            return dict(cls._state)

    @classmethod
    def start(
        cls,
        *,
        captures=12,
        band_delay_s=0.3,
        drop_last_band=False,
    ) -> dict:

        try:
            captures = int(captures)
            band_delay_s = float(band_delay_s)
        except (TypeError, ValueError) as exc:
            raise Esp32SimulatorError(
                "Parámetros de simulación inválidos."
            ) from exc

        if not 1 <= captures <= cls.MAX_CAPTURES:
            raise Esp32SimulatorError(
                f"El número de capturas debe estar entre 1 y "
                f"{cls.MAX_CAPTURES}."
            )

        band_delay_s = min(max(band_delay_s, 0.0), 5.0)

        if not Esp32ReceptionService.get_status()["active"]:
            raise Esp32SimulatorError(
                "Primero inicia la recepción hacia una campaña."
            )

        with cls._lock:

            if cls._thread is not None and cls._thread.is_alive():
                raise Esp32SimulatorError(
                    "La simulación ya está en curso."
                )

            cls._stop_event = threading.Event()
            cls._state = {
                "running": True,
                "total": captures,
                "sent_captures": 0,
                "sent_files": 0,
                "error": None,
            }

            cls._thread = threading.Thread(
                target=cls._run,
                kwargs={
                    "captures": captures,
                    "band_delay_s": band_delay_s,
                    "drop_last_band": bool(drop_last_band),
                    "stop_event": cls._stop_event,
                },
                name="esp32-simulator",
                daemon=True,
            )
            cls._thread.start()

            return dict(cls._state)

    @classmethod
    def stop(cls) -> dict:
        cls._stop_event.set()
        return cls.status()

    @classmethod
    def wait(cls, timeout: float | None = None) -> None:
        """Espera a que termine (útil en pruebas)."""
        thread = cls._thread
        if thread is not None:
            thread.join(timeout)

    @classmethod
    def _update(cls, **changes) -> None:
        with cls._lock:
            cls._state.update(changes)

    @classmethod
    def _run(
        cls,
        *,
        captures: int,
        band_delay_s: float,
        drop_last_band: bool,
        stop_event: threading.Event,
    ) -> None:

        flight_id = _random_id()
        started = datetime.now()
        # Carpeta SET única por simulación, para que dos
        # simulaciones no se confundan como reenvíos.
        set_name = "SIM" + started.strftime("%Y%m%d%H%M%S") + "SET"

        sent_files = 0

        try:
            Esp32ReceptionService.register_heartbeat(
                address="simulador",
                device_id=cls.DEVICE_ID,
            )

            for index in range(captures):

                if stop_event.is_set():
                    break

                bands = generate_capture(
                    index,
                    flight_id=flight_id,
                    captured_at=started + timedelta(seconds=2 * index),
                    seed=int(started.timestamp()) + index,
                )

                # Opción para ver cómo se muestra una captura
                # incompleta: a la última le falta una banda.
                if drop_last_band and index == captures - 1:
                    bands.pop(5)

                for band, content in bands.items():

                    if stop_event.is_set():
                        break

                    Esp32ReceptionService.receive_file(
                        stream=io.BytesIO(content),
                        filename=f"IMG_{index:04d}_{band}.tif",
                        relative_path=capture_filename(
                            set_name, index, band
                        ),
                        content_length=len(content),
                        address="simulador",
                        device_id=cls.DEVICE_ID,
                    )

                    sent_files += 1
                    cls._update(sent_files=sent_files)

                    if band_delay_s:
                        stop_event.wait(band_delay_s)

                else:
                    cls._update(sent_captures=index + 1)

        except NoActiveReceptionError:
            cls._update(
                error="La recepción se detuvo durante la simulación."
            )

        except (Esp32ReceptionError, OSError) as exc:
            cls._update(error=str(exc))

        except Exception as exc:  # pragma: no cover - defensivo
            cls._update(error=f"Error inesperado: {exc}")

        finally:
            cls._update(running=False)
