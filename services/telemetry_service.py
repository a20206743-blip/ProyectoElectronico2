from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from database import SessionLocal
from models import (
    Flight,
    FlightStatus,
    TelemetryPoint,
    TelemetrySource,
)


class TelemetryPersistenceError(Exception):
    """
    Error producido durante una operación de persistencia
    relacionada con telemetría.
    """
    pass


class InvalidTelemetryError(Exception):
    """
    Error producido cuando los datos de telemetría
    no cumplen las reglas mínimas requeridas.
    """
    pass


class TelemetryService:
    @staticmethod
    def _serialize(point: TelemetryPoint) -> dict:
        return {
            "id": point.id,
            "flight_id": point.flight_id,
            "recorded_at": (
                point.recorded_at.isoformat()
                if point.recorded_at
                else None
            ),
            "latitude": point.latitude,
            "longitude": point.longitude,
            "altitude": point.altitude,
            "speed": point.speed,
            "heading": point.heading,
            "battery": point.battery,
            "gps_sats": point.gps_sats,
            "flight_mode": point.flight_mode,
            "source": point.source.value,
        }

    @staticmethod
    def _validate_coordinates(
        latitude: float | None,
        longitude: float | None
    ) -> None:
        if latitude is not None and not (-90 <= latitude <= 90):
            raise InvalidTelemetryError(
                "La latitud debe estar entre -90 y 90."
            )

        if longitude is not None and not (-180 <= longitude <= 180):
            raise InvalidTelemetryError(
                "La longitud debe estar entre -180 y 180."
            )

    @staticmethod
    def _validate_values(
        battery: float | None,
        heading: float | None,
        gps_sats: int | None
    ) -> None:
        if battery is not None and not (0 <= battery <= 100):
            raise InvalidTelemetryError(
                "La batería debe estar entre 0 y 100."
            )

        if heading is not None and not (0 <= heading <= 360):
            raise InvalidTelemetryError(
                "El heading debe estar entre 0 y 360 grados."
            )

        if gps_sats is not None and gps_sats < 0:
            raise InvalidTelemetryError(
                "gps_sats no puede ser negativo."
            )

    @staticmethod
    def add_point(
        flight_id: int,
        latitude: float | None = None,
        longitude: float | None = None,
        altitude: float | None = None,
        speed: float | None = None,
        heading: float | None = None,
        battery: float | None = None,
        gps_sats: int | None = None,
        flight_mode: str | None = None,
        source: TelemetrySource = TelemetrySource.UNKNOWN,
        recorded_at: datetime | None = None,
    ) -> dict:
        """
        Registra un punto de telemetría asociado a un vuelo activo.
        """

        TelemetryService._validate_coordinates(
            latitude,
            longitude
        )

        TelemetryService._validate_values(
            battery,
            heading,
            gps_sats
        )

        session = SessionLocal()

        try:
            flight = session.get(Flight, flight_id)

            if flight is None:
                raise InvalidTelemetryError(
                    f"El vuelo con ID {flight_id} no existe."
                )

            if flight.status != FlightStatus.ACTIVE:
                raise InvalidTelemetryError(
                    f"No se puede registrar telemetría en el vuelo "
                    f"{flight.flight_code} porque está en estado "
                    f"{flight.status.value}."
                )

            point = TelemetryPoint(
                flight_id=flight_id,
                recorded_at=(
                    recorded_at
                    if recorded_at is not None
                    else datetime.now(timezone.utc)
                ),
                latitude=latitude,
                longitude=longitude,
                altitude=altitude,
                speed=speed,
                heading=heading,
                battery=battery,
                gps_sats=gps_sats,
                flight_mode=flight_mode,
                source=source,
            )

            session.add(point)
            session.commit()
            session.refresh(point)

            return TelemetryService._serialize(point)

        except InvalidTelemetryError:
            session.rollback()
            raise

        except SQLAlchemyError as exc:
            session.rollback()

            raise TelemetryPersistenceError(
                f"No fue posible registrar la telemetría: {exc}"
            ) from exc

        finally:
            session.close()

    @staticmethod
    def get_flight_points(flight_id: int) -> list[dict]:
        """
        Recupera los puntos de un vuelo en orden temporal.
        """
        session = SessionLocal()

        try:
            statement = (
                select(TelemetryPoint)
                .where(TelemetryPoint.flight_id == flight_id)
                .order_by(
                    TelemetryPoint.recorded_at,
                    TelemetryPoint.id
                )
            )

            points = session.scalars(statement).all()

            return [
                TelemetryService._serialize(point)
                for point in points
            ]

        except SQLAlchemyError as exc:
            raise TelemetryPersistenceError(
                f"No fue posible consultar la telemetría: {exc}"
            ) from exc

        finally:
            session.close()
    
    @staticmethod
    def _safe_float(value) -> float | None:
        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    
    @staticmethod
    def _safe_int(value) -> int | None:
        if value is None:
            return None

        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    