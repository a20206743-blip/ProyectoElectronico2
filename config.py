import os
import secrets
from dotenv import load_dotenv


# Cargar variables desde el archivo .env
load_dotenv()


def env_bool(name: str, default: bool = False) -> bool:
    """
    Convierte una variable de entorno de texto a booleano.

    Valores considerados True:
    true, 1, yes, y, on
    """
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"true", "1", "yes", "y", "on"}


class Config:
    # Flask
    SECRET_KEY = (
        os.getenv("FLASK_SECRET_KEY")
        or secrets.token_hex(32)
    )

    FLASK_HOST = os.getenv(
        "FLASK_HOST",
        "0.0.0.0"
    )

    FLASK_PORT = int(
        os.getenv("FLASK_PORT", "5050")
    )

    FLASK_DEBUG = env_bool(
        "FLASK_DEBUG",
        False
    )

    # Recepción Wi-Fi desde el módulo ESP32-C5 (CULTIVA)
    #
    # Carpeta donde se guardan temporalmente los TIFF
    # recibidos hasta completar las 5 bandas de cada captura.
    ESP32_RECEPTION_ROOT = os.getenv(
        "ESP32_RECEPTION_ROOT",
        "data/esp32_reception",
    )

    # Clave opcional compartida con el ESP32. Si se define,
    # cada envío debe incluir la cabecera X-Device-Key.
    ESP32_DEVICE_KEY = os.getenv(
        "ESP32_DEVICE_KEY",
        "",
    ).strip()

    # Tamaño máximo aceptado por archivo (MB).
    ESP32_MAX_FILE_MB = float(
        os.getenv(
            "ESP32_MAX_FILE_MB",
            "50",
        )
    )

    # Segundos sin noticias del ESP32 para considerarlo
    # desconectado en la interfaz.
    ESP32_ONLINE_SECONDS = float(
        os.getenv(
            "ESP32_ONLINE_SECONDS",
            "15",
        )
    )

    # Base de datos
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "sqlite:///data/ground_control.db"
    )

    # Integración de dron
    DRONE_TELEMETRY_STALE_SECONDS = float(
        os.getenv(
            "DRONE_TELEMETRY_STALE_SECONDS",
            "5.0"
        )
    )

    CAMPAIGN_STORAGE_ROOT = os.getenv(
        "CAMPAIGN_STORAGE_ROOT",
        "data/campaigns",
    )

    PHOTOGRAMMETRY_BACKEND = os.getenv(
        "PHOTOGRAMMETRY_BACKEND",
        "MOCK",
    ).strip().upper()

    # Guard operativo para impedir procesamientos accidentales.
    # No sustituye controles de autorización.
    PROCESSING_ENABLED = env_bool(
        "PROCESSING_ENABLED",
        True,
    )

    # Ajusta la navegación para una exposición PE2 sin eliminar rutas legacy.
    PE2_DEMO_MODE = env_bool(
        "PE2_DEMO_MODE",
        False,
    )

    METASHAPE_EXECUTABLE = os.getenv(
        "METASHAPE_EXECUTABLE",
        "",
    ).strip()

    MOCK_PHOTOGRAMMETRY_DELAY_SECONDS = float(
        os.getenv(
            "MOCK_PHOTOGRAMMETRY_DELAY_SECONDS",
            "0",
        )
    )

    RED_EDGE_STAGING_ROOT = os.getenv(
        "RED_EDGE_STAGING_ROOT",
        "data/rededge_staging",
    )

    RED_EDGE_UPLOAD_BATCH_MAX_FILES = int(
        os.getenv(
            "RED_EDGE_UPLOAD_BATCH_MAX_FILES",
            "25",
        )
    )

    RED_EDGE_STAGING_MAX_AGE_HOURS = int(
        os.getenv(
            "RED_EDGE_STAGING_MAX_AGE_HOURS",
            "24",
        )
    )

    PE2_MQTT_DIAG_ACCESS_CODE = os.getenv(
        "PE2_MQTT_DIAG_ACCESS_CODE",
        "",
    )
