"""
Recepción de imágenes MicaSense RedEdge enviadas por Wi-Fi
desde el módulo de comunicación ESP32-C5 (proyecto CULTIVA).

Flujo:

1. Desde la interfaz se inicia una sesión de recepción
   asociada a una campaña MULTISPECTRAL.
2. El ESP32 envía cada TIFF por HTTP POST, una banda
   por petición (IMG_0142_1.tif ... IMG_0142_5.tif).
3. Los archivos se guardan en una carpeta temporal agrupados
   por captura. Cuando una captura reúne sus 5 bandas, se
   registra en la campaña con RedEdgeImportService (que lee
   los metadatos GPS/fecha de MicaSense y copia los archivos
   al almacenamiento de la campaña).
4. La interfaz consulta el estado para mostrar el avance.

El estado se guarda en disco (session.json) para que una
sesión no se pierda si el servidor se reinicia a mitad del
vuelo.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from config import Config
from services.campaign_service import CampaignService
from services.capture_service import (
    CaptureService,
    InvalidCaptureError,
)
from services.rededge_import_service import (
    RedEdgeImportError,
    RedEdgeImportService,
)


class Esp32ReceptionError(Exception):
    """Error general de la recepción ESP32."""


class InvalidEsp32UploadError(Esp32ReceptionError):
    """El archivo o la petición recibida no es válida."""


class NoActiveReceptionError(Esp32ReceptionError):
    """No hay una sesión de recepción activa."""


class ReceptionAlreadyActiveError(Esp32ReceptionError):
    """Ya existe una sesión activa."""


class InvalidReceptionCampaignError(Esp32ReceptionError):
    """La campaña indicada no existe o no es multiespectral."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Esp32ReceptionService:

    FILE_PATTERN = RedEdgeImportService.FILE_PATTERN

    BAND_NAMES = {
        1: "BLUE",
        2: "GREEN",
        3: "RED",
        4: "NIR",
        5: "RED_EDGE",
    }

    TIFF_SIGNATURES = (
        b"II*\x00",   # TIFF little-endian
        b"MM\x00*",   # TIFF big-endian
        b"II+\x00",   # BigTIFF little-endian
        b"MM\x00+",   # BigTIFF big-endian
    )

    MAX_EVENTS = 60

    SESSION_FILENAME = "session.json"

    _lock = threading.RLock()

    _state: dict | None = None

    _loaded_root: Path | None = None

    # -----------------------------------------------------
    # Rutas
    # -----------------------------------------------------

    @staticmethod
    def _root() -> Path:

        configured = Path(
            Config.ESP32_RECEPTION_ROOT
        )

        if configured.is_absolute():
            return configured.resolve()

        project_root = Path(
            __file__
        ).resolve().parents[1]

        return (
            project_root / configured
        ).resolve()

    @classmethod
    def _session_dir(cls, state: dict) -> Path:
        return cls._root() / state["session_id"]

    @classmethod
    def _pending_dir(cls, state: dict) -> Path:
        return cls._session_dir(state) / "pending"

    @classmethod
    def _failed_dir(cls, state: dict) -> Path:
        return cls._session_dir(state) / "failed"

    # -----------------------------------------------------
    # Persistencia del estado
    # -----------------------------------------------------

    @classmethod
    def _empty_state(cls) -> dict:
        return {
            "active": False,
            "session_id": None,
            "campaign_id": None,
            "campaign_code": None,
            "campaign_name": None,
            "capture_role": None,
            "started_at": None,
            "stopped_at": None,
            "files_received": 0,
            "bytes_received": 0,
            "captures_imported": 0,
            "captures_failed": 0,
            "duplicates": 0,
            "last_file": None,
            "last_file_at": None,
            "imported_keys": [],
            "pending": {},
            "last_capture_id": None,
            "events": [],
            "device_last_seen_at": None,
            "device_address": None,
            "device_id": None,
        }

    @classmethod
    def _load(cls) -> dict:
        """
        Devuelve el estado en memoria, cargándolo desde
        disco la primera vez (o si cambió la carpeta raíz,
        lo que ocurre en los tests).
        """

        root = cls._root()

        if (
            cls._state is not None
            and cls._loaded_root == root
        ):
            return cls._state

        state = cls._empty_state()

        pointer = root / cls.SESSION_FILENAME

        if pointer.exists():
            try:
                stored = json.loads(
                    pointer.read_text(
                        encoding="utf-8"
                    )
                )
                state.update(stored)
            except (OSError, json.JSONDecodeError):
                pass

        cls._state = state
        cls._loaded_root = root

        return state

    @classmethod
    def _save(cls, state: dict) -> None:

        root = cls._root()
        root.mkdir(parents=True, exist_ok=True)

        pointer = root / cls.SESSION_FILENAME
        temporary = pointer.with_suffix(".tmp")

        temporary.write_text(
            json.dumps(
                state,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        temporary.replace(pointer)

    @classmethod
    def _add_event(
        cls,
        state: dict,
        level: str,
        message: str,
    ) -> None:

        events = deque(
            state.get("events", []),
            maxlen=cls.MAX_EVENTS,
        )

        events.append({
            "at": _iso(_now()),
            "level": level,
            "message": message,
        })

        state["events"] = list(events)

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._state = None
            cls._loaded_root = None

    # -----------------------------------------------------
    # Sesión
    # -----------------------------------------------------

    @classmethod
    def start_session(
        cls,
        *,
        campaign_id,
        capture_role="FLIGHT",
    ) -> dict:

        try:
            campaign_id = int(campaign_id)
        except (TypeError, ValueError) as exc:
            raise InvalidReceptionCampaignError(
                "Selecciona una campaña válida."
            ) from exc

        try:
            role = CaptureService.normalize_role(
                capture_role or "FLIGHT"
            )
        except InvalidCaptureError as exc:
            raise InvalidEsp32UploadError(
                "El tipo de captura debe ser "
                "FLIGHT, CALIBRATION o UNKNOWN."
            ) from exc

        campaign = CampaignService.get_campaign(
            campaign_id
        )

        if campaign is None:
            raise InvalidReceptionCampaignError(
                f"La campaña con ID {campaign_id} "
                "no existe."
            )

        if campaign["sensor_type"] != "MULTISPECTRAL":
            raise InvalidReceptionCampaignError(
                "La recepción RedEdge requiere una "
                "campaña MULTISPECTRAL."
            )

        with cls._lock:

            state = cls._load()

            if state["active"]:
                raise ReceptionAlreadyActiveError(
                    "Ya hay una recepción activa hacia "
                    f"{state['campaign_code']}. "
                    "Detenla antes de iniciar otra."
                )

            # Conservamos la última actividad del módulo
            # para que la interfaz siga mostrándolo
            # conectado entre sesiones.
            previous = state

            state = cls._empty_state()

            state.update({
                "active": True,
                "session_id": uuid4().hex,
                "campaign_id": campaign_id,
                "campaign_code": campaign["campaign_code"],
                "campaign_name": campaign.get("name"),
                "capture_role": role.value,
                "started_at": _iso(_now()),
                "device_last_seen_at": previous.get(
                    "device_last_seen_at"
                ),
                "device_address": previous.get(
                    "device_address"
                ),
                "device_id": previous.get("device_id"),
            })

            # Si se reanuda la recepción hacia la misma
            # campaña, se recuerdan las capturas ya
            # guardadas para no duplicarlas si el ESP32
            # vuelve a enviarlas.
            if previous.get("campaign_id") == campaign_id:
                state["imported_keys"] = list(
                    previous.get("imported_keys", [])
                )

            # Los temporales de la sesión anterior
            # (bandas sueltas, capturas fallidas) ya
            # no se necesitan.
            if previous.get("session_id"):
                shutil.rmtree(
                    cls._session_dir(previous),
                    ignore_errors=True,
                )

            cls._pending_dir(state).mkdir(
                parents=True,
                exist_ok=True,
            )

            cls._add_event(
                state,
                "info",
                "Recepción iniciada hacia "
                f"{campaign['campaign_code']} "
                f"(tipo {role.value}). "
                "Esperando imágenes del ESP32...",
            )

            cls._state = state
            cls._save(state)

            return cls._public_status(state)

    @classmethod
    def stop_session(cls) -> dict:

        with cls._lock:

            state = cls._load()

            if not state["active"]:
                raise NoActiveReceptionError(
                    "No hay una recepción activa."
                )

            state["active"] = False
            state["stopped_at"] = _iso(_now())

            incomplete = len(state["pending"])

            message = (
                "Recepción detenida. "
                f"{state['captures_imported']} capturas "
                "guardadas en "
                f"{state['campaign_code']}."
            )

            if incomplete:
                message += (
                    f" {incomplete} captura(s) quedaron "
                    "incompletas (faltaron bandas) y no "
                    "se guardaron."
                )

            cls._add_event(
                state,
                "warning" if incomplete else "success",
                message,
            )

            cls._save(state)

            return cls._public_status(state)

    # -----------------------------------------------------
    # Presencia del módulo
    # -----------------------------------------------------

    @classmethod
    def register_heartbeat(
        cls,
        *,
        address: str | None,
        device_id: str | None = None,
    ) -> dict:

        with cls._lock:

            state = cls._load()

            state["device_last_seen_at"] = _iso(_now())
            state["device_address"] = address

            if device_id:
                state["device_id"] = device_id[:64]

            cls._save(state)

            return {
                "receiving": state["active"],
                "campaign_code": (
                    state["campaign_code"]
                    if state["active"]
                    else None
                ),
            }

    # -----------------------------------------------------
    # Recepción de archivos
    # -----------------------------------------------------

    @classmethod
    def _parse_name(
        cls,
        filename: str | None,
        relative_path: str | None,
    ) -> tuple[str, str, int, str]:
        """
        Devuelve (clave_de_captura, nombre_archivo,
        índice_banda, nombre_base).

        La cámara reinicia la numeración IMG_xxxx en cada
        carpeta SET, así que si el ESP32 envía la ruta
        relativa (p. ej. 0001SET/000/IMG_0142_1.tif) se usa
        para distinguir capturas con el mismo nombre.
        """

        raw = (relative_path or filename or "").strip()

        if not raw:
            raise InvalidEsp32UploadError(
                "Falta el nombre del archivo "
                "(parámetro filename)."
            )

        normalized = raw.replace("\\", "/")
        path = PurePosixPath(normalized)

        if path.is_absolute():
            path = PurePosixPath(
                *path.parts[1:]
            )

        for part in path.parts:
            if (
                part in {"", ".", ".."}
                or ":" in part
            ):
                raise InvalidEsp32UploadError(
                    "La ruta del archivo contiene "
                    "segmentos no permitidos."
                )

        name = path.name

        match = cls.FILE_PATTERN.match(name)

        if match is None:
            raise InvalidEsp32UploadError(
                f"Nombre no reconocido: {name}. "
                "Se espera el formato de MicaSense "
                "IMG_0000_N.tif (N = banda 1 a 5)."
            )

        base = match.group("base")
        band = int(match.group("band"))

        parent = path.parent.as_posix()

        key = (
            base
            if parent in {"", "."}
            else f"{parent}/{base}"
        )

        return key, name, band, base

    @staticmethod
    def _safe_dirname(key: str) -> str:
        return re.sub(
            r"[^A-Za-z0-9._-]",
            "_",
            key.replace("/", "__"),
        )

    @classmethod
    def receive_file(
        cls,
        *,
        stream,
        filename: str | None,
        relative_path: str | None = None,
        content_length: int | None = None,
        address: str | None = None,
        device_id: str | None = None,
    ) -> dict:

        max_bytes = int(
            Config.ESP32_MAX_FILE_MB * 1024 * 1024
        )

        if (
            content_length is not None
            and content_length > max_bytes
        ):
            raise InvalidEsp32UploadError(
                "El archivo supera el límite de "
                f"{Config.ESP32_MAX_FILE_MB:g} MB."
            )

        key, name, band, base = cls._parse_name(
            filename,
            relative_path,
        )

        with cls._lock:

            state = cls._load()

            state["device_last_seen_at"] = _iso(_now())
            state["device_address"] = address

            if device_id:
                state["device_id"] = device_id[:64]

            if not state["active"]:
                cls._save(state)
                raise NoActiveReceptionError(
                    "La laptop no está recibiendo. "
                    "Inicia la recepción desde "
                    "'Adquisición de datos'."
                )

            if key in state["imported_keys"]:
                state["duplicates"] += 1
                cls._add_event(
                    state,
                    "info",
                    f"{name} ya estaba guardado "
                    "(reenvío ignorado).",
                )
                cls._save(state)

                return {
                    "status": "duplicate",
                    "file": name,
                    "capture_key": key,
                }

            group_dir = (
                cls._pending_dir(state)
                / cls._safe_dirname(key)
            )

            group_dir.mkdir(parents=True, exist_ok=True)

            destination = group_dir / name
            temporary = destination.with_name(
                destination.name + ".part"
            )

            written = cls._write_stream(
                stream,
                temporary,
                max_bytes,
            )

            # Un reenvío de la misma banda reemplaza
            # a la copia anterior.
            temporary.replace(destination)

            state["files_received"] += 1
            state["bytes_received"] += written
            state["last_file"] = (
                relative_path or name
            )
            state["last_file_at"] = _iso(_now())

            pending = state["pending"].setdefault(
                key,
                {
                    "base_name": base,
                    "dir": group_dir.name,
                    "bands": [],
                    "first_at": _iso(_now()),
                },
            )

            if band not in pending["bands"]:
                pending["bands"].append(band)
                pending["bands"].sort()

            result = {
                "status": "stored",
                "file": name,
                "band": cls.BAND_NAMES[band],
                "capture_key": key,
                "bands_received": len(pending["bands"]),
                "bands_expected": 5,
            }

            if len(pending["bands"]) == 5:
                result.update(
                    cls._import_group(
                        state,
                        key,
                        group_dir,
                    )
                )

            cls._save(state)

            return result

    @classmethod
    def _write_stream(
        cls,
        stream,
        destination: Path,
        max_bytes: int,
    ) -> int:

        written = 0
        header = b""

        try:
            with destination.open("wb") as output:

                while True:
                    chunk = stream.read(64 * 1024)

                    if not chunk:
                        break

                    if len(header) < 4:
                        header += chunk[: 4 - len(header)]

                    written += len(chunk)

                    if written > max_bytes:
                        raise InvalidEsp32UploadError(
                            "El archivo supera el límite "
                            f"de {Config.ESP32_MAX_FILE_MB:g} MB."
                        )

                    output.write(chunk)

        except Exception:
            destination.unlink(missing_ok=True)
            raise

        if written == 0:
            destination.unlink(missing_ok=True)
            raise InvalidEsp32UploadError(
                "El archivo llegó vacío."
            )

        if header not in cls.TIFF_SIGNATURES:
            destination.unlink(missing_ok=True)
            raise InvalidEsp32UploadError(
                "El contenido recibido no es un "
                "archivo TIFF válido."
            )

        return written

    @classmethod
    def _import_group(
        cls,
        state: dict,
        key: str,
        group_dir: Path,
    ) -> dict:

        try:
            imported = RedEdgeImportService.import_folder(
                campaign_id=state["campaign_id"],
                source_dir=group_dir,
                capture_role=state["capture_role"],
            )

        except (RedEdgeImportError, Exception) as exc:

            state["captures_failed"] += 1
            state["pending"].pop(key, None)

            failed_dir = cls._failed_dir(state)
            failed_dir.mkdir(parents=True, exist_ok=True)

            target = failed_dir / group_dir.name

            if target.exists():
                target = failed_dir / (
                    f"{group_dir.name}_{uuid4().hex[:6]}"
                )

            try:
                shutil.move(str(group_dir), str(target))
            except OSError:
                pass

            cls._add_event(
                state,
                "error",
                f"No se pudo guardar la captura {key}: "
                f"{exc}",
            )

            return {
                "capture_status": "error",
                "msg": str(exc),
            }

        state["pending"].pop(key, None)
        state["imported_keys"].append(key)
        state["captures_imported"] += 1

        capture = None

        if imported["imported_captures"]:
            capture = imported["imported_captures"][0][
                "capture"
            ]
            state["last_capture_id"] = capture["id"]

        shutil.rmtree(group_dir, ignore_errors=True)

        location = ""

        if capture and capture.get("latitude") is not None:
            location = (
                f" · GPS {capture['latitude']:.6f}, "
                f"{capture['longitude']:.6f}"
            )

        cls._add_event(
            state,
            "success",
            f"Captura {key} completa (5 bandas) y "
            f"guardada en {state['campaign_code']}"
            f"{location}.",
        )

        return {
            "capture_status": "saved",
            "capture_id": capture["id"] if capture else None,
            "capture_sequence": (
                capture.get("sequence") if capture else None
            ),
        }

    # -----------------------------------------------------
    # Estado para la interfaz
    # -----------------------------------------------------

    @classmethod
    def get_status(cls) -> dict:
        with cls._lock:
            return cls._public_status(cls._load())

    @classmethod
    def _public_status(cls, state: dict) -> dict:

        last_seen = state.get("device_last_seen_at")
        online = False

        if last_seen:
            try:
                age = (
                    _now()
                    - datetime.fromisoformat(last_seen)
                ).total_seconds()
                online = age <= Config.ESP32_ONLINE_SECONDS
            except ValueError:
                online = False

        pending = [
            {
                "capture_key": key,
                "bands_received": len(item["bands"]),
                "missing_bands": [
                    cls.BAND_NAMES[index]
                    for index in range(1, 6)
                    if index not in item["bands"]
                ],
            }
            for key, item in state["pending"].items()
        ]

        return {
            "active": state["active"],
            "campaign_id": state["campaign_id"],
            "campaign_code": state["campaign_code"],
            "campaign_name": state["campaign_name"],
            "capture_role": state["capture_role"],
            "started_at": state["started_at"],
            "stopped_at": state["stopped_at"],
            "files_received": state["files_received"],
            "bytes_received": state["bytes_received"],
            "captures_imported": state["captures_imported"],
            "captures_failed": state["captures_failed"],
            "duplicates": state["duplicates"],
            "pending_captures": pending,
            "last_file": state["last_file"],
            "last_file_at": state["last_file_at"],
            "last_capture_id": state["last_capture_id"],
            "events": list(reversed(state["events"])),
            "device": {
                "online": online,
                "last_seen_at": last_seen,
                "address": state.get("device_address"),
                "device_id": state.get("device_id"),
            },
            "device_key_required": bool(
                Config.ESP32_DEVICE_KEY
            ),
        }
