from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class DroneState:
    """
    Representación normalizada del estado actual de un dron.

    Esta clase es independiente de la fuente de datos.
    El estado podría provenir de:

    - simulador;
    - DJI Cloud API;
    - otra integración futura.
    """

    provider: str

    connected: bool
    aircraft_online: bool

    latitude: float | None = None
    longitude: float | None = None

    altitude: float | None = None
    speed: float | None = None
    heading: float | None = None

    battery: float | None = None
    gps_sats: int | None = None

    flight_mode: str | None = None

    recorded_at: datetime = field(
        default_factory=lambda: datetime.now(
            timezone.utc
        )
    )


    def __post_init__(self) -> None:
        """
        Valida valores básicos independientemente
        del proveedor que haya generado el estado.
        """

        if self.latitude is not None:
            if not (-90 <= self.latitude <= 90):
                raise ValueError(
                    "La latitud debe estar entre -90 y 90."
                )


        if self.longitude is not None:
            if not (-180 <= self.longitude <= 180):
                raise ValueError(
                    "La longitud debe estar entre -180 y 180."
                )


        if self.speed is not None:
            if self.speed < 0:
                raise ValueError(
                    "La velocidad no puede ser negativa."
                )


        if self.heading is not None:
            if not (0 <= self.heading <= 360):
                raise ValueError(
                    "El heading debe estar entre 0 y 360 grados."
                )


        if self.battery is not None:
            if not (0 <= self.battery <= 100):
                raise ValueError(
                    "La batería debe estar entre 0 y 100."
                )


        if self.gps_sats is not None:
            if self.gps_sats < 0:
                raise ValueError(
                    "La cantidad de satélites GPS "
                    "no puede ser negativa."
                )


    def to_dict(self) -> dict:
        """
        Convierte el estado a una estructura serializable
        para API/JSON.
        """

        return {
            "provider": self.provider,

            "connected": self.connected,
            "aircraft_online": self.aircraft_online,

            "latitude": self.latitude,
            "longitude": self.longitude,

            "altitude": self.altitude,
            "speed": self.speed,
            "heading": self.heading,

            "battery": self.battery,
            "gps_sats": self.gps_sats,

            "flight_mode": self.flight_mode,

            "recorded_at": (
                self.recorded_at.isoformat()
                if self.recorded_at
                else None
            ),
        }