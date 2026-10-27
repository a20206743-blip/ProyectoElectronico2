# ====================================================
# PE2 / CULTIVA — Estación terrena (Flask)
# Recibe por Wi-Fi las imágenes RedEdge enviadas por el
# módulo ESP32-C5 y las organiza en campañas.
# ====================================================
from flask import (
    Flask,
    render_template,
    request,
    redirect, url_for,
    session,
    jsonify,
    Response,
)

import socket
import time
import json
import logging
import secrets
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4
from config import Config
from models import CaptureRole, TelemetrySource

from services.flight_service import (
    FlightService,
    FlightPersistenceError,
)

from services.telemetry_service import (
    TelemetryService,
    TelemetryPersistenceError,
)

from services.route_service import (
    RouteService,
    RoutePersistenceError,
)

from services.mission_service import (
    MissionService,
    MissionPersistenceError,
    MissionNotFoundError,
)

from services.drone_service import (
    DroneService,
    DroneServiceError,
    DroneProviderNotConfiguredError,
)

from services.campaign_service import (
    CampaignPersistenceError,
    CampaignService,
    InvalidCampaignError,
)

from services.capture_service import (
    CapturePersistenceError,
    CaptureService,
    InvalidCaptureError,
)

from services.processing_job_service import (
    InvalidProcessingJobError,
    ProcessingJobPersistenceError,
    ProcessingJobService,
)

from services.geospatial_product_service import (
    GeospatialProductPersistenceError,
    GeospatialProductService,
    InvalidGeospatialProductError,
)

from services.processing_service import (
    InvalidCampaignProcessingError,
    ProcessingExecutionError,
    ProcessingService,
)

from services.photogrammetry_backend_factory import (
    PhotogrammetryBackendConfigurationError,
    PhotogrammetryBackendFactory,
)

from services.rededge_import_service import (
    InvalidRedEdgeCaptureRoleError,
    IncompleteRedEdgeFolderError,
    InvalidRedEdgeCampaignError,
    InvalidRedEdgeFolderError,
    RedEdgeImportError,
    RedEdgeImportExecutionError,
    RedEdgeImportService,
)

from services.rededge_staging_service import (
    InvalidRedEdgeStageError,
    RedEdgeStagingError,
    RedEdgeStagingService,
)

from services.esp32_reception_service import (
    Esp32ReceptionService,
    InvalidEsp32UploadError,
    InvalidReceptionCampaignError,
    NoActiveReceptionError,
    ReceptionAlreadyActiveError,
)

from services.esp32_simulator_service import (
    Esp32SimulatorError,
    Esp32SimulatorService,
)

from services.raster_preview_service import (
    InvalidRasterPreviewError,
    RasterPreviewError,
    RasterPreviewService,
)

from services.image_ingestion_service import (
    ImageIngestionService,
)

from services.spectral_index_product_service import (
    InvalidSpectralIndexSourceError,
    SpectralIndexProductAlreadyExistsError,
    SpectralIndexProductError,
    SpectralIndexProductService,
)

from services.spectral_index_visualization_service import (
    InvalidSpectralIndexVisualizationError,
    SpectralIndexVisualizationError,
    SpectralIndexVisualizationService,
)

from services.processing_executor import (
    ProcessingExecutor,
    ProcessingExecutorError,
)

from datetime import datetime, timezone
from ipaddress import ip_address

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY

app.logger.setLevel(logging.INFO)

processing_executor = (
    ProcessingExecutor(
        max_workers=1
    )
)

# ---------------- RUTAS ----------------
@app.route("/")
def index():
    return render_template(
        "pe2_home.html",
        pe2_demo_mode=Config.PE2_DEMO_MODE,
        processing_enabled=Config.PROCESSING_ENABLED,
        photogrammetry_backend=Config.PHOTOGRAMMETRY_BACKEND,
        mqtt_diagnostics_local=(
            request.remote_addr in ("127.0.0.1", "::1")
        ),
    )

@app.route("/api/flights", methods=["GET"])
def api_list_flights():
    try:
        limit = request.args.get(
            "limit",
            default=50,
            type=int
        )

        flights = FlightService.list_flights_summary(
            limit=limit
        )

        return jsonify({
            "flights": flights,
            "count": len(flights)
        })

    except FlightPersistenceError as e:
        return jsonify({
            "error": True,
            "msg": str(e)
        }), 500

@app.route("/api/flights/<int:flight_id>", methods=["GET"])
def api_get_flight(flight_id):
    try:
        flight = FlightService.get_flight(
            flight_id
        )

        if flight is None:
            return jsonify({
                "error": True,
                "msg": "Vuelo no encontrado."
            }), 404

        points = TelemetryService.get_flight_points(
            flight_id
        )

        return jsonify({
            "flight": flight,
            "telemetry_count": len(points)
        })

    except (
        FlightPersistenceError,
        TelemetryPersistenceError
    ) as e:
        return jsonify({
            "error": True,
            "msg": str(e)
        }), 500

@app.route(
    "/api/flights/<int:flight_id>/trajectory",
    methods=["GET"]
)
def api_get_flight_trajectory(flight_id):
    try:
        flight = FlightService.get_flight(
            flight_id
        )

        if flight is None:
            return jsonify({
                "error": True,
                "msg": "Vuelo no encontrado."
            }), 404

        points = TelemetryService.get_flight_points(
            flight_id
        )

        trajectory = [
            {
                "latitude": point["latitude"],
                "longitude": point["longitude"],
                "altitude": point["altitude"],
                "recorded_at": point["recorded_at"]
            }
            for point in points
            if (
                point["latitude"] is not None
                and point["longitude"] is not None
            )
        ]

        return jsonify({
            "flight_id": flight_id,
            "flight_code": flight["flight_code"],
            "points": trajectory,
            "count": len(trajectory)
        })

    except (
        FlightPersistenceError,
        TelemetryPersistenceError
    ) as e:
        return jsonify({
            "error": True,
            "msg": str(e)
        }), 500

def _execute_processing_job_background(
    *,
    job_id: int,
    backend,
) -> None:

    try:

        ProcessingService.execute_job(
            job_id=job_id,
            backend=backend,
        )

    except Exception:

        app.logger.exception(
            "Falló el trabajo geoespacial "
            "en segundo plano. job_id=%s",
            job_id,
        )

@app.route("/flight_history")
def flight_history():
    return render_template(
        "flight_history.html"
    )

@app.route("/campaigns")
def campaigns_view():
    return render_template(
        "campaigns.html",
        pe2_demo_mode=Config.PE2_DEMO_MODE,
    )


@app.route("/campaigns/<int:campaign_id>")
def campaign_detail_view(
    campaign_id
):
    return render_template(
        "campaign_detail.html",
        campaign_id=campaign_id,
        processing_enabled=(
            Config.PROCESSING_ENABLED
        ),
        pe2_demo_mode=Config.PE2_DEMO_MODE,
    )

@app.route("/flight_history/<int:flight_id>")
def flight_detail(flight_id):
    return render_template(
        "flight_detail.html",
        flight_id=flight_id
    )

@app.route("/api/routes", methods=["GET"])
def api_list_routes():
    try:
        limit = request.args.get(
            "limit",
            default=50,
            type=int
        )

        routes = RouteService.list_routes(
            limit=limit
        )

        return jsonify({
            "routes": routes,
            "count": len(routes)
        })

    except RoutePersistenceError as e:
        return jsonify({
            "error": True,
            "msg": str(e)
        }), 500

@app.route(
    "/api/routes/<int:route_id>",
    methods=["GET"]
)
def api_get_route(route_id):
    try:
        route = RouteService.get_route(
            route_id
        )

        if route is None:
            return jsonify({
                "error": True,
                "msg": "Ruta no encontrada."
            }), 404

        return jsonify({
            "route": route
        })

    except RoutePersistenceError as e:
        return jsonify({
            "error": True,
            "msg": str(e)
        }), 500

@app.route("/mission_routes")
def mission_routes():
    return render_template(
        "mission_routes.html"
    )

@app.route("/mission_routes/<int:route_id>")
def mission_route_detail(route_id):
    return render_template(
        "mission_route_detail.html",
        route_id=route_id
    )

@app.route(
    "/api/flights/<int:flight_id>/mission",
    methods=["GET"]
)
def api_get_flight_mission(flight_id):

    try:

        mission = (
            MissionService.get_mission(
                flight_id
            )
        )

        return jsonify(
            mission
        )


    except MissionNotFoundError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 404


    except MissionPersistenceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 500

@app.route(
    "/api/drone/state",
    methods=["GET"]
)
def api_drone_state():

    try:

        state = (
            DroneService
            .get_state_dict()
        )

        return jsonify({
            "configured": True,
            "state": state
        })


    except DroneProviderNotConfiguredError:

        return jsonify({
            "configured": False,
            "state": None
        })

@app.route(
    "/api/drone/simulator/setup",
    methods=["POST"]
)
def api_drone_simulator_setup():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )


    route_id = data.get(
        "route_id"
    )


    if route_id is None:

        return jsonify({
            "error": True,
            "msg": "route_id es obligatorio."
        }), 400


    try:

        route_id = int(
            route_id
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "error": True,
            "msg": "route_id debe ser un entero."
        }), 400


    step_fraction = data.get(
        "step_fraction",
        0.20
    )


    initial_battery = data.get(
        "initial_battery",
        100.0
    )


    try:

        step_fraction = float(
            step_fraction
        )

        initial_battery = float(
            initial_battery
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "error": True,
            "msg": (
                "step_fraction e initial_battery "
                "deben ser valores numéricos."
            )
        }), 400


    try:

        state = (
            DroneService
            .configure_simulator(
                route_id,
                step_fraction=step_fraction,
                initial_battery=initial_battery
            )
        )


        return jsonify({
            "status": "configured",

            "provider":
                DroneService
                .get_provider_name(),

            "route_id":
                route_id,

            "state":
                state.to_dict()
        })


    except (
        DroneServiceError,
        ValueError
    ) as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/simulator/connect",
    methods=["POST"]
)
def api_drone_simulator_connect():

    try:

        state = (
            DroneService
            .simulator_connect()
        )

        return jsonify({
            "status": "connected",
            "state": state.to_dict()
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/simulator/start",
    methods=["POST"]
)
def api_drone_simulator_start():

    try:

        state = (
            DroneService
            .simulator_start()
        )

        return jsonify({
            "status": "running",
            "state": state.to_dict()
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/simulator/advance",
    methods=["POST"]
)
def api_drone_simulator_advance():

    try:

        state = (
            DroneService
            .simulator_advance()
        )

        return jsonify({
            "status": (
                "running"
                if DroneService
                .simulator_is_running()
                else "completed"
            ),

            "state":
                state.to_dict()
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/simulator/reset",
    methods=["POST"]
)
def api_drone_simulator_reset():

    try:

        state = (
            DroneService
            .simulator_reset()
        )

        return jsonify({
            "status": "reset",
            "state": state.to_dict()
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/simulator/disconnect",
    methods=["POST"]
)
def api_drone_simulator_disconnect():

    try:

        state = (
            DroneService
            .simulator_disconnect()
        )

        return jsonify({
            "status": "disconnected",
            "state": state.to_dict()
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route("/dji-cloud")
def dji_cloud_probe():
    """
    Página mínima para verificar que DJI Pilot 2 puede abrir
    la plataforma PE2 desde Servicios en la nube / Plataformas abiertas.
    """

    client_ip = request.remote_addr

    user_agent = request.headers.get(
        "User-Agent",
        "",
    )[:300]

    try:
        from dji_cloud_diagnostics import (
            record_dji_cloud_contact,
        )

        record_dji_cloud_contact(
            event_type="page_open",
            remote_addr=client_ip,
            user_agent=user_agent,
        )

    except (OSError, ValueError) as exc:
        app.logger.warning(
            "No se pudo registrar contacto DJI Cloud: %s",
            exc,
        )

    app.logger.info(
        "DJI Cloud probe abierto desde ip=%s user_agent=%s",
        client_ip,
        user_agent,
    )

    return render_template(
        "dji_cloud_probe.html",
        client_ip=client_ip,
        user_agent=user_agent,
        server_time=datetime.now(
            timezone.utc
        ).isoformat(),
    )

@app.route(
    "/api/dji-cloud/ping",
    methods=["GET", "POST"],
)
def api_dji_cloud_ping():
    """Diagnóstico HTTP de comunicación con PE2."""

    payload = (
        request.get_json(silent=True)
        if request.method == "POST"
        else {}
    )

    if not isinstance(payload, dict):
        return jsonify({
            "ok": False,
            "error": "Se esperaba un objeto JSON.",
        }), 400

    request_id = uuid4().hex[:12]

    client_request_id = str(
        payload.get(
            "client_request_id",
            "",
        )
    )[:64]

    client_ip = request.remote_addr

    user_agent = request.headers.get(
        "User-Agent",
        "",
    )[:200]

    server_time = datetime.now(
        timezone.utc
    ).isoformat()

    app.logger.info(
        "DJI_PROBE_PING "
        "request_id=%s "
        "client_request_id=%s "
        "method=%s "
        "ip=%s "
        "user_agent=%r",
        request_id,
        client_request_id,
        request.method,
        client_ip,
        user_agent,
    )

    try:
        from dji_cloud_diagnostics import (
            record_dji_cloud_contact,
        )

        record_dji_cloud_contact(
            event_type="ping",
            remote_addr=client_ip,
            client_request_id=(
                client_request_id
            ),
            user_agent=user_agent,
        )

    except (OSError, ValueError) as exc:
        app.logger.warning(
            "No se pudo registrar ping DJI Cloud: %s",
            exc,
        )

    return jsonify({
        "ok": True,
        "source": "pe2-dji-cloud-probe",
        "request_id": request_id,
        "client_request_id": (
            client_request_id
        ),
        "remote_addr": client_ip,
        "user_agent": user_agent,
        "received": payload,
        "server_time": server_time,
    })

@app.route(
    "/api/dji-cloud/client-state",
    methods=["POST"],
)
def api_dji_cloud_client_state():
    """
    Recibe únicamente estados booleanos de diagnóstico
    reportados por la página ejecutada en Pilot 2.
    """

    client_ip = (
        request.remote_addr
        or ""
    )

    try:
        parsed_ip = ip_address(
            client_ip
        )

    except ValueError:
        return jsonify({
            "ok": False,
            "error":
                "Dirección cliente no válida.",
        }), 403

    if not (
        parsed_ip.is_private
        or parsed_ip.is_loopback
    ):
        return jsonify({
            "ok": False,
            "error":
                "Reporte remoto no habilitado.",
        }), 403

    payload = request.get_json(
        silent=True
    )

    if not isinstance(
        payload,
        dict,
    ):
        return jsonify({
            "ok": False,
            "error":
                "Se esperaba un objeto JSON.",
        }), 400

    try:
        from dji_cloud_client_state import (
            update_client_state,
        )

        state = update_client_state(
            payload,
            remote_addr=client_ip,
        )

    except ValueError as exc:
        return jsonify({
            "ok": False,
            "error": str(exc),
        }), 400

    except OSError as exc:
        app.logger.warning(
            "No se pudo registrar "
            "estado cliente DJI: %s",
            exc,
        )

        return jsonify({
            "ok": False,
            "error":
                "No se pudo persistir "
                "el estado.",
        }), 500

    return jsonify({
        "ok": True,
        "source":
            "pe2-dji-cloud-client-state",
        "state": state,
    })

@app.route(
    "/api/dji-cloud/readiness",
    methods=["GET"],
)
def api_dji_cloud_readiness():
    """
    Estado mínimo y no sensible necesario para
    preparar una sesión DJI Pilot 2.
    """

    client_ip = (
        request.remote_addr
        or ""
    )

    try:
        parsed_ip = ip_address(
            client_ip
        )

    except ValueError:
        return jsonify({
            "ok": False,
            "error":
                "Dirección cliente no válida.",
        }), 403

    if not (
        parsed_ip.is_private
        or parsed_ip.is_loopback
    ):
        return jsonify({
            "ok": False,
            "error":
                "Readiness remoto no habilitado.",
        }), 403

    try:
        from dji_cloud_state import (
            build_runtime_summary,
            read_dji_cloud_receiver_status,
            read_dji_cloud_state,
            summarize_receiver_status,
        )

        runtime = (
            build_runtime_summary(
                read_dji_cloud_state()
            )
        )

        receiver = (
            summarize_receiver_status(
                read_dji_cloud_receiver_status()
            )
        )

    except (
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        app.logger.warning(
            "No se pudo construir "
            "DJI Cloud readiness: %s",
            exc,
        )

        return jsonify({
            "ok": False,
            "error":
                "No se pudo leer el estado "
                "DJI Cloud local.",
        }), 500

    gateway_detected = bool(
        runtime.get(
            "gateway_detected"
        )
    )

    simulated = bool(
        runtime.get(
            "simulated"
        )
    )

    real_gateway_observed = (
        gateway_detected
        and not simulated
    )

    return jsonify({
        "ok": True,
        "source":
            "pe2-dji-cloud-readiness",

        "mqtt": {
            "port": 1884,
            "receiver_state":
                receiver.get(
                    "state"
                ),
            "heartbeat_age_seconds":
                receiver.get(
                    "heartbeat_age_seconds"
                ),
            "ack_topology": True,
        },

        "gateway": {
            "detected":
                gateway_detected,
            "simulated":
                simulated,
            "real_observed":
                real_gateway_observed,
            "gateway_id":
                runtime.get(
                    "gateway_id"
                ),
        },

        "runtime": {
            "device_count":
                runtime.get(
                    "device_count",
                    0,
                ),
            "last_message_at":
                runtime.get(
                    "last_message_at"
                ),
            "last_channel":
                runtime.get(
                    "last_channel"
                ),
        },
    })

@app.route("/drone_operation")
def drone_operation():
    return render_template(
        "drone_operation.html"
    )

@app.route("/adquisicion_datos")
def data_acquisition_view():
    return render_template("data_acquisition.html")


# ---------------- RECEPCIÓN WI-FI ESP32 ----------------

def _local_ipv4_addresses() -> list[str]:
    """
    Direcciones IPv4 de esta laptop en la red local,
    para indicarle al ESP32 a dónde enviar las fotos.
    """

    addresses: list[str] = []

    try:
        # No envía paquetes: solo pregunta al sistema
        # qué interfaz usaría para salir a la red.
        probe = socket.socket(
            socket.AF_INET,
            socket.SOCK_DGRAM,
        )

        try:
            probe.connect(("8.8.8.8", 80))
            addresses.append(probe.getsockname()[0])
        finally:
            probe.close()

    except OSError:
        pass

    try:
        _, _, host_addresses = socket.gethostbyname_ex(
            socket.gethostname()
        )
        addresses.extend(host_addresses)
    except OSError:
        pass

    unique = []

    for address in addresses:
        if (
            address
            and not address.startswith("127.")
            and address not in unique
        ):
            unique.append(address)

    return unique


def _esp32_key_is_valid() -> bool:

    expected = Config.ESP32_DEVICE_KEY

    if not expected:
        return True

    received = request.headers.get(
        "X-Device-Key",
        "",
    )

    return secrets.compare_digest(
        received,
        expected,
    )


@app.route(
    "/api/esp32/ping",
    methods=["GET", "POST"],
)
def api_esp32_ping():
    """
    Señal de vida del ESP32. Le indica además si la
    laptop está lista para recibir imágenes.
    """

    if not _esp32_key_is_valid():
        return jsonify({
            "error": True,
            "msg": "Clave de dispositivo inválida.",
        }), 401

    result = Esp32ReceptionService.register_heartbeat(
        address=request.remote_addr,
        device_id=request.headers.get("X-Device-Id"),
    )

    return jsonify({
        "ok": True,
        **result,
    })


@app.route(
    "/api/esp32/upload",
    methods=["POST"],
)
def api_esp32_upload():
    """
    Recibe una banda TIFF enviada por el ESP32.

    Formas aceptadas:

    1. Cuerpo binario (recomendado para el ESP32):
       POST /api/esp32/upload?filename=IMG_0142_1.tif
       Content-Type: application/octet-stream
       (el nombre también puede ir en la cabecera
       X-Filename, y la ruta SET opcional en X-Path
       o en ?path=0001SET/000/IMG_0142_1.tif)

    2. multipart/form-data con el campo "file".
    """

    if not _esp32_key_is_valid():
        return jsonify({
            "error": True,
            "msg": "Clave de dispositivo inválida.",
        }), 401

    relative_path = (
        request.args.get("path")
        or request.headers.get("X-Path")
    )

    uploaded = request.files.get("file")

    if uploaded is not None:
        stream = uploaded.stream
        filename = uploaded.filename
        relative_path = (
            relative_path
            or request.form.get("path")
        )
        content_length = None
    else:
        stream = request.stream
        filename = (
            request.args.get("filename")
            or request.headers.get("X-Filename")
        )
        content_length = request.content_length

    try:
        result = Esp32ReceptionService.receive_file(
            stream=stream,
            filename=filename,
            relative_path=relative_path,
            content_length=content_length,
            address=request.remote_addr,
            device_id=request.headers.get("X-Device-Id"),
        )

    except NoActiveReceptionError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 409

    except InvalidEsp32UploadError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except OSError as exc:
        app.logger.exception(
            "Error guardando archivo del ESP32"
        )
        return jsonify({
            "error": True,
            "msg": f"No se pudo guardar el archivo: {exc}",
        }), 500

    return jsonify(result), 200


@app.route(
    "/api/reception/status",
    methods=["GET"],
)
def api_reception_status():

    status = Esp32ReceptionService.get_status()

    status["simulator"] = Esp32SimulatorService.status()

    status["server"] = {
        "addresses": _local_ipv4_addresses(),
        "port": Config.FLASK_PORT,
        "host_binding": Config.FLASK_HOST,
        "reachable_from_network": (
            Config.FLASK_HOST
            not in ("127.0.0.1", "localhost")
        ),
    }

    return jsonify(status)


@app.route(
    "/api/reception/start",
    methods=["POST"],
)
def api_reception_start():

    data = request.get_json(silent=True) or {}

    try:
        status = Esp32ReceptionService.start_session(
            campaign_id=data.get("campaign_id"),
            capture_role=data.get(
                "capture_role",
                "FLIGHT",
            ),
        )

    except ReceptionAlreadyActiveError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 409

    except (
        InvalidReceptionCampaignError,
        InvalidEsp32UploadError,
    ) as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    return jsonify(status), 201


@app.route(
    "/api/esp32/simulator/start",
    methods=["POST"],
)
def api_esp32_simulator_start():
    """
    Simula al ESP32 mientras no hay hardware: genera capturas
    RedEdge falsas y las entrega a la recepción activa.
    """

    data = request.get_json(silent=True) or {}

    try:
        state = Esp32SimulatorService.start(
            captures=data.get("captures", 12),
            band_delay_s=data.get("band_delay_s", 0.3),
            drop_last_band=data.get("drop_last_band", False),
        )

    except Esp32SimulatorError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    return jsonify(state), 201


@app.route(
    "/api/esp32/simulator/stop",
    methods=["POST"],
)
def api_esp32_simulator_stop():
    return jsonify(Esp32SimulatorService.stop())


@app.route(
    "/api/reception/stop",
    methods=["POST"],
)
def api_reception_stop():

    # Si el simulador está enviando, se detiene también.
    Esp32SimulatorService.stop()

    try:
        status = Esp32ReceptionService.stop_session()

    except NoActiveReceptionError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 409

    return jsonify(status)

@app.route(
    "/api/drone/simulator/auto/status",
    methods=["GET"]
)
def api_drone_simulator_auto_status():

    try:

        return jsonify(
            DroneService
            .simulator_auto_status()
        )


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/simulator/auto/start",
    methods=["POST"]
)
def api_drone_simulator_auto_start():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )


    interval_seconds = data.get(
        "interval_seconds",
        1.0
    )


    try:

        result = (
            DroneService
            .simulator_start_auto(
                interval_seconds
            )
        )


        return jsonify({
            "status": "auto_started",
            "runtime": result
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/simulator/auto/stop",
    methods=["POST"]
)
def api_drone_simulator_auto_stop():

    try:

        result = (
            DroneService
            .simulator_stop_auto()
        )


        return jsonify({
            "status": "auto_stopped",
            "runtime": result
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/operation/prepare",
    methods=["POST"]
)
def api_drone_operation_prepare():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )


    route_id = data.get(
        "route_id"
    )


    if route_id is None:

        return jsonify({
            "error": True,
            "msg": "route_id es obligatorio."
        }), 400


    try:

        route_id = int(
            route_id
        )

    except (
        TypeError,
        ValueError
    ):

        return jsonify({
            "error": True,
            "msg": "route_id debe ser un entero."
        }), 400


    name = (
        str(
            data.get(
                "name",
                "Vuelo simulado"
            )
        )
        .strip()
    )


    if not name:
        name = "Vuelo simulado"


    try:

        result = (
            DroneService
            .prepare_simulated_flight(
                route_id,
                name=name,
            )
        )


        return jsonify({
            "status": "prepared",
            "operation": result
        })


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/operation/context",
    methods=["GET"]
)
def api_drone_operation_context():

    try:

        return jsonify(
            DroneService
            .get_operation_context()
        )


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400


def _mqtt_diagnostics_is_local():
    """Restringir temporalmente el diagnóstico a localhost."""
    return request.remote_addr in {
        "127.0.0.1",
        "::1",
    }


@app.route(
    "/diagnostics/login",
    methods=["GET", "POST"],
)
def mqtt_diagnostics_login():
    if not _mqtt_diagnostics_is_local():
        return "Acceso remoto no habilitado.", 403

    diagnostic_code = Config.PE2_MQTT_DIAG_ACCESS_CODE

    if not diagnostic_code:
        return "El acceso MQTT no está configurado.", 503

    if session.get("mqtt_diag_access"):
        return redirect(
            url_for("mqtt_diagnostics_view")
        )

    error = None

    if request.method == "POST":
        submitted_code = request.form.get("code", "")

        if secrets.compare_digest(
            submitted_code,
            diagnostic_code,
        ):
            session["mqtt_diag_access"] = True

            return redirect(
                url_for("mqtt_diagnostics_view")
            )

        error = "Código incorrecto."

    return render_template(
        "mqtt_diagnostics_login.html",
        error=error,
    )

@app.route(
    "/diagnostics/mqtt",
    methods=["GET"],
)
def mqtt_diagnostics_view():
    if not _mqtt_diagnostics_is_local():
        return "Acceso remoto no habilitado.", 403

    if not session.get("mqtt_diag_access"):
        return redirect(
            url_for("mqtt_diagnostics_login")
        )

    return render_template(
        "mqtt_diagnostics.html"
    )

@app.route(
    "/api/diagnostics/mqtt",
    methods=["GET"],
)
def api_mqtt_diagnostics():
    if not _mqtt_diagnostics_is_local():
        return jsonify({
            "error": True,
            "msg": "Acceso remoto no habilitado.",
        }), 403

    if not session.get("mqtt_diag_access"):
        return jsonify({
            "error": True,
            "msg": "Autenticación requerida.",
        }), 401

    from mqtt_diagnostics import (
        read_mqtt_events,
        read_receiver_status,
    )

    result = read_mqtt_events()
    receiver = read_receiver_status()

    result["receiver"] = receiver
    result["receiver_connected"] = (
        receiver["connected"]
    )

    return jsonify(result)

@app.route(
    "/api/diagnostics/integration",
    methods=["GET"],
)
def api_integration_diagnostics():
    from dji_cloud_diagnostics import (
        read_dji_cloud_status,
    )

    if not _mqtt_diagnostics_is_local():
        return jsonify({
            "error": True,
            "msg": "Acceso remoto no habilitado.",
        }), 403

    if not session.get("mqtt_diag_access"):
        return jsonify({
            "error": True,
            "msg": "Autenticación requerida.",
        }), 401

    from mqtt_diagnostics import (
        read_mqtt_events,
        read_receiver_status,
    )

    mqtt_events = read_mqtt_events()
    mqtt_receiver = read_receiver_status()

    try:
        dji_status = (
            DroneService
            .get_integration_status()
            .to_dict()
        )

        dji_backend = {
            "available": True,
            "status": dji_status,
            "error": None,
        }

    except DroneServiceError as exc:
        dji_backend = {
            "available": False,
            "status": None,
            "error": str(exc),
        }

    dji_cloud_status = (
        read_dji_cloud_status()
    )

    try:
        from dji_cloud_state import (
            build_runtime_summary,
            read_dji_cloud_receiver_status,
            read_dji_cloud_state,
            summarize_receiver_status,
        )

        dji_cloud_mqtt = (
            build_runtime_summary(
                read_dji_cloud_state()
            )
        )

        dji_cloud_mqtt["receiver"] = (
            summarize_receiver_status(
                read_dji_cloud_receiver_status()
            )
        )

    except (OSError, ValueError) as exc:
        dji_cloud_mqtt = {
            "available": False,
            "error": str(exc),
        }

    try:
        from dji_cloud_client_state import (
            read_client_state,
            summarize_client_state,
        )

        dji_cloud_client = (
            summarize_client_state(
                read_client_state()
            )
        )

    except (OSError, ValueError) as exc:
        dji_cloud_client = {
            "reported": False,
            "fresh": False,
            "error": str(exc),
        }

    return jsonify({
        "source": "pe2-integration-diagnostics",

        "mqtt": {
            "receiver": mqtt_receiver,
            "last_event": (
                mqtt_events.get("last_event")
            ),
            "log_exists": (
                mqtt_events.get(
                    "log_exists",
                    False,
                )
            ),
        },

        "dji_cloud_probe": {
            "page_available": True,
            "page_path": "/dji-cloud",
            "ping_path": "/api/dji-cloud/ping",
            "contact_tracking": True,
            **dji_cloud_status,
        },

        "dji_cloud_client": (
            dji_cloud_client
        ),

        "dji_cloud_mqtt": (
            dji_cloud_mqtt
        ),

        "dji_backend": (
            dji_backend
        ),
    })

@app.route("/diagnostics/logout", methods=["POST"])
def mqtt_diagnostics_logout():
    if not _mqtt_diagnostics_is_local():
        return "Acceso remoto no habilitado.", 403

    session.pop("mqtt_diag_access", None)

    return redirect(
        url_for("mqtt_diagnostics_login")
    )

@app.route(
    "/diagnostics/integration",
    methods=["GET"],
)
def integration_diagnostics_view():
    if not _mqtt_diagnostics_is_local():
        return "Acceso remoto no habilitado.", 403

    if not session.get("mqtt_diag_access"):
        return redirect(
            url_for("mqtt_diagnostics_login")
        )

    return render_template(
        "integration_diagnostics.html"
    )

@app.route(
    "/api/drone/operation/integration-status",
    methods=["GET"]
)
def api_drone_operation_integration_status():
    """
    Devuelve el estado técnico normalizado
    de la integración activa con el dron.
    """

    try:

        status = (
            DroneService
            .get_integration_status()
        )

        return jsonify(
            status.to_dict()
        )


    except DroneServiceError as exc:

        return jsonify({
            "status": "error",
            "message": str(exc),
        }), 400


@app.route(
    "/api/drone/operation/cancel",
    methods=["POST"]
)
def api_drone_operation_cancel():

    try:

        result = (
            DroneService
            .cancel_active_operation()
        )


        return jsonify(
            result
        )


    except DroneServiceError as e:

        return jsonify({
            "error": True,
            "msg": str(e)
        }), 400

@app.route(
    "/api/drone/operation/connect",
    methods=["POST"]
)
def api_drone_operation_connect():

    try:

        return jsonify(
            DroneService.connect_operation()
        )

    except DroneServiceError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

@app.route(
    "/api/drone/operation/start",
    methods=["POST"]
)
def api_drone_operation_start():

    try:

        data = request.get_json(
            silent=True
        ) or {}


        interval_seconds = (
            data.get(
                "interval_seconds",
                0.5
            )
        )


        return jsonify(
            DroneService.start_operation(
                interval_seconds
            )
        )

    except DroneServiceError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

@app.route(
    "/api/drone/operation/pause",
    methods=["POST"]
)
def api_drone_operation_pause():

    try:

        return jsonify(
            DroneService.pause_operation()
        )

    except DroneServiceError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

@app.route(
    "/api/drone/operation/resume",
    methods=["POST"]
)
def api_drone_operation_resume():

    try:

        data = request.get_json(
            silent=True
        ) or {}


        return jsonify(
            DroneService.resume_operation(
                data.get(
                    "interval_seconds",
                    0.5
                )
            )
        )

    except DroneServiceError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

@app.route(
    "/api/drone/operation/disconnect",
    methods=["POST"]
)
def api_drone_operation_disconnect():

    try:

        return jsonify(
            DroneService.disconnect_operation()
        )

    except DroneServiceError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

@app.route(
    "/api/campaigns",
    methods=["GET"],
)
def api_list_campaigns():
    try:
        limit = request.args.get(
            "limit",
            default=50,
            type=int,
        )

        campaigns = (
            CampaignService.list_campaigns(
                limit=limit,
            )
        )

        return jsonify({
            "campaigns": campaigns,
            "count": len(campaigns),
        })

    except CampaignPersistenceError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500


@app.route(
    "/api/campaigns",
    methods=["POST"],
)
def api_create_campaign():
    data = request.get_json(
        silent=True
    ) or {}

    try:
        campaign = (
            CampaignService.create_campaign(
                name=data.get("name"),
                sensor_type=data.get(
                    "sensor_type"
                ),
                sensor_model=data.get(
                    "sensor_model"
                ),
                flight_id=data.get(
                    "flight_id"
                ),
                description=data.get(
                    "description"
                ),
                notes=data.get(
                    "notes"
                ),
            )
        )

        return jsonify(
            campaign
        ), 201

    except InvalidCampaignError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except CampaignPersistenceError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500


@app.route(
    "/api/campaigns/<int:campaign_id>",
    methods=["GET"],
)
def api_get_campaign(campaign_id):
    try:
        campaign = (
            CampaignService.get_campaign(
                campaign_id
            )
        )

        if campaign is None:
            return jsonify({
                "error": True,
                "msg": (
                    "La campaña con ID "
                    f"{campaign_id} no existe."
                ),
            }), 404

        return jsonify(
            campaign
        )

    except CampaignPersistenceError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    "/api/campaigns/<int:campaign_id>/captures",
    methods=["GET"],
)
def api_list_campaign_captures(
    campaign_id
):
    try:
        campaign = (
            CampaignService.get_campaign(
                campaign_id
            )
        )

        if campaign is None:
            return jsonify({
                "error": True,
                "msg": (
                    "La campaña con ID "
                    f"{campaign_id} no existe."
                ),
            }), 404

        captures = (
            CaptureService.list_captures(
                campaign_id
            )
        )

        return jsonify({
            "campaign_id": campaign_id,
            "captures": captures,
            "count": len(captures),
            "role_counts": {
                role.value: sum(capture["role"] == role.value for capture in captures)
                for role in CaptureRole
            },
        })

    except (
        InvalidCaptureError,
        CapturePersistenceError,
    ) as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    "/api/campaigns/<int:campaign_id>/processing-jobs",
    methods=["GET"],
)
def api_list_campaign_processing_jobs(
    campaign_id
):
    try:
        campaign = (
            CampaignService.get_campaign(
                campaign_id
            )
        )

        if campaign is None:
            return jsonify({
                "error": True,
                "msg": (
                    "La campaña con ID "
                    f"{campaign_id} no existe."
                ),
            }), 404

        jobs = (
            ProcessingJobService.list_jobs(
                campaign_id
            )
        )

        return jsonify({
            "campaign_id": campaign_id,
            "jobs": jobs,
            "count": len(jobs),
        })

    except (
        InvalidProcessingJobError,
        ProcessingJobPersistenceError,
    ) as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    "/api/campaigns/<int:campaign_id>/products",
    methods=["GET"],
)
def api_list_campaign_products(
    campaign_id
):
    try:
        campaign = (
            CampaignService.get_campaign(
                campaign_id
            )
        )

        if campaign is None:
            return jsonify({
                "error": True,
                "msg": (
                    "La campaña con ID "
                    f"{campaign_id} no existe."
                ),
            }), 404

        product_type = request.args.get(
            "type"
        )

        products = (
            GeospatialProductService
            .list_products(
                campaign_id,
                product_type=product_type,
            )
        )

        return jsonify({
            "campaign_id": campaign_id,
            "products": products,
            "count": len(products),
        })

    except InvalidGeospatialProductError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except GeospatialProductPersistenceError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500


@app.route(
    "/api/geospatial-products/<int:product_id>",
    methods=["GET"],
)
def api_get_geospatial_product(
    product_id
):
    try:
        product = (
            GeospatialProductService
            .get_product(
                product_id
            )
        )

        if product is None:
            return jsonify({
                "error": True,
                "msg": (
                    "El producto geoespacial "
                    f"con ID {product_id} "
                    "no existe."
                ),
            }), 404

        return jsonify(
            product
        )

    except GeospatialProductPersistenceError as exc:
        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    (
        "/api/geospatial-products/"
        "<int:product_id>/preview.png"
    ),
    methods=["GET"],
)
def api_geospatial_product_preview(
    product_id
):

    try:
        product = (
            GeospatialProductService
            .get_product(
                product_id
            )
        )

        if product is None:
            return jsonify({
                "error": True,
                "msg": (
                    "El producto geoespacial "
                    f"con ID {product_id} "
                    "no existe."
                ),
            }), 404

        source_path = (
            ImageIngestionService
            .resolve_managed_path(
                product["storage_path"]
            )
        )

        product_type = (
            product.get(
                "product_type"
            )
            or product.get(
                "type"
            )
        )

        normalized_product_type = (
            str(
                product_type or ""
            )
            .strip()
            .upper()
        )

        display_mode = (
            request.args.get(
                "mode",
                "natural",
            )
            .strip()
            .upper()
        )

        band_palette = request.args.get(
            "band_palette",
            "gray",
        ).strip().upper()

        if (
            normalized_product_type
            in (
                SpectralIndexVisualizationService
                .SUPPORTED_INDEXES
            )
        ):

            png_bytes = (
                SpectralIndexVisualizationService
                .render_png(
                    source_path=source_path,
                    index_name=(
                        normalized_product_type
                    ),
                )
            )

        else:

            png_bytes = (
                RasterPreviewService
                .render_png(
                    source_path=source_path,
                    product_type=product_type,
                    display_mode=display_mode,
                    band_palette=band_palette,
                )
            )

        response = Response(
            png_bytes,
            mimetype="image/png",
        )

        response.headers[
            "Cache-Control"
        ] = "no-store"

        return response

    except InvalidRasterPreviewError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 422

    except RasterPreviewError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

    except (
        InvalidSpectralIndexVisualizationError
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 422

    except SpectralIndexVisualizationError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500


@app.route(
    "/api/campaigns/<int:campaign_id>/process",
    methods=["POST"],
)
def api_process_campaign(
    campaign_id
):

    try:

        if not Config.PROCESSING_ENABLED:

            return jsonify({
                "error": True,
                "msg": (
                    "El procesamiento está deshabilitado "
                    "por configuración operativa."
                ),
            }), 409

        campaign = (
            CampaignService.get_campaign(
                campaign_id
            )
        )

        if campaign is None:

            return jsonify({
                "error": True,
                "msg": (
                    "La campaña con ID "
                    f"{campaign_id} no existe."
                ),
            }), 404

        backend = (
            PhotogrammetryBackendFactory
            .create()
        )

        job = (
            ProcessingService.prepare_campaign(
                campaign_id=campaign_id,
                backend=backend,
            )
        )

        try:

            processing_executor.submit(
                job_id=job["id"],
                task=lambda: (
                    _execute_processing_job_background(
                        job_id=job["id"],
                        backend=backend,
                    )
                ),
            )

        except (
            ProcessingExecutorError,
            RuntimeError,
        ) as exc:

            failure_message = (
                "No fue posible encolar "
                "el trabajo de procesamiento: "
                f"{exc}"
            )

            try:

                ProcessingJobService.fail_job(
                    job["id"],
                    error_message=(
                        failure_message
                    ),
                )

            except Exception:

                app.logger.exception(
                    "No fue posible marcar "
                    "FAILED el job que no pudo "
                    "encolarse. job_id=%s",
                    job["id"],
                )

            return jsonify({
                "error": True,
                "msg": failure_message,
                "job": job,
            }), 500

        return jsonify({
            "accepted": True,
            "job": job,
            "msg": (
                "El procesamiento fue "
                "aceptado y continuará "
                "en segundo plano."
            ),
        }), 202

    except (
        InvalidCampaignProcessingError,
        InvalidCampaignError,
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except (
        ProcessingExecutionError,
        PhotogrammetryBackendConfigurationError,
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

    except (
        CampaignPersistenceError,
        ProcessingJobPersistenceError,
        GeospatialProductPersistenceError,
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500


@app.route(
    (
        "/api/geospatial-products/"
        "<int:product_id>/spectral-indices/"
        "<string:index_name>"
    ),
    methods=["POST"],
)
def api_generate_spectral_index(
    product_id,
    index_name,
):

    try:
        product = (
            SpectralIndexProductService
            .generate_index(
                source_product_id=product_id,
                index_name=index_name,
            )
        )

        return jsonify({
            "ok": True,
            "product": product,
            "msg": (
                f"{index_name.upper()} "
                "generado correctamente."
            ),
        }), 201

    except (
        SpectralIndexProductAlreadyExistsError
    ) as exc:

        return jsonify({
            "ok": False,
            "msg": str(exc),
        }), 409

    except InvalidSpectralIndexSourceError as exc:

        return jsonify({
            "ok": False,
            "msg": str(exc),
        }), 400

    except SpectralIndexProductError as exc:

        return jsonify({
            "ok": False,
            "msg": str(exc),
        }), 422

@app.route(
    (
        "/api/geospatial-products/"
        "<int:product_id>/statistics"
    ),
    methods=["GET"],
)
def api_geospatial_product_statistics(
    product_id
):

    try:

        product = (
            GeospatialProductService
            .get_product(
                product_id
            )
        )

        if product is None:

            return jsonify({
                "error": True,
                "msg": (
                    "El producto "
                    "geoespacial no existe."
                ),
            }), 404

        product_type = (
            str(
                product.get(
                    "product_type"
                )
                or product.get(
                    "type"
                )
                or ""
            )
            .strip()
            .upper()
        )

        if (
            product_type
            not in (
                SpectralIndexVisualizationService
                .SUPPORTED_INDEXES
            )
        ):

            return jsonify({
                "error": True,
                "msg": (
                    "Las estadísticas "
                    "espectrales están "
                    "disponibles únicamente "
                    "para NDVI, NDRE y GNDVI."
                ),
            }), 400

        source_path = (
            ImageIngestionService
            .resolve_managed_path(
                product[
                    "storage_path"
                ]
            )
        )

        statistics = (
            SpectralIndexVisualizationService
            .calculate_statistics(
                source_path=source_path,
                index_name=product_type,
            )
        )

        return jsonify({
            "ok": True,
            "product_id": product_id,
            "statistics": statistics,
            "legend": (
                SpectralIndexVisualizationService
                .get_legend()
            ),
        })

    except (
        InvalidSpectralIndexVisualizationError
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 422

    except SpectralIndexVisualizationError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500


@app.route(
    "/api/campaigns/<int:campaign_id>/rededge/inspect",
    methods=["POST"],
)
def api_inspect_rededge_upload(
    campaign_id
):

    token = None

    try:
        campaign = (
            CampaignService.get_campaign(
                campaign_id
            )
        )

        if campaign is None:
            return jsonify({
                "error": True,
                "msg": (
                    "La campaña con ID "
                    f"{campaign_id} no existe."
                ),
            }), 404

        if (
            campaign["sensor_type"]
            != "MULTISPECTRAL"
        ):
            return jsonify({
                "error": True,
                "msg": (
                    "La importación RedEdge "
                    "requiere una campaña "
                    "MULTISPECTRAL."
                ),
            }), 400

        uploads = (
            request.files.getlist(
                "files"
            )
        )

        if not uploads:
            return jsonify({
                "error": True,
                "msg": (
                    "No se recibieron archivos."
                ),
            }), 400

        relative_paths_raw = (
            request.form.get(
                "relative_paths"
            )
        )

        if not relative_paths_raw:
            return jsonify({
                "error": True,
                "msg": (
                    "No se recibieron "
                    "las rutas relativas."
                ),
            }), 400

        try:
            relative_paths = (
                json.loads(
                    relative_paths_raw
                )
            )

        except json.JSONDecodeError:
            return jsonify({
                "error": True,
                "msg": (
                    "relative_paths "
                    "no contiene JSON válido."
                ),
            }), 400

        if not isinstance(
            relative_paths,
            list,
        ):
            return jsonify({
                "error": True,
                "msg": (
                    "relative_paths debe "
                    "ser una lista."
                ),
            }), 400

        if (
            len(relative_paths)
            != len(uploads)
        ):
            return jsonify({
                "error": True,
                "msg": (
                    "La cantidad de archivos "
                    "no coincide con las "
                    "rutas relativas."
                ),
            }), 400

        token = (
            RedEdgeStagingService
            .create_stage(
                campaign_id=campaign_id
            )
        )

        for (
            upload,
            relative_path,
        ) in zip(
            uploads,
            relative_paths,
        ):

            (
                RedEdgeStagingService
                .stage_file(
                    token=token,
                    campaign_id=campaign_id,
                    relative_path=(
                        relative_path
                    ),
                    source_stream=(
                        upload.stream
                    ),
                )
            )

        inspection = (
            RedEdgeStagingService
            .inspect_stage(
                token=token,
                campaign_id=campaign_id,
            )
        )

        return jsonify({
            "staging_token": token,
            "uploaded_file_count": (
                len(uploads)
            ),
            "inspection": inspection,
        }), 201

    except (
        InvalidRedEdgeStageError,
        InvalidRedEdgeFolderError,
        RedEdgeImportError,
    ) as exc:

        if token is not None:
            try:
                (
                    RedEdgeStagingService
                    .delete_stage(
                        token=token,
                        campaign_id=campaign_id,
                    )
                )
            except Exception:
                pass

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except RedEdgeStagingError as exc:

        if token is not None:
            try:
                (
                    RedEdgeStagingService
                    .delete_stage(
                        token=token,
                        campaign_id=campaign_id,
                    )
                )
            except Exception:
                pass

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    "/api/campaigns/<int:campaign_id>/rededge/import",
    methods=["POST"],
)
def api_import_rededge_upload(
    campaign_id
):

    data = request.get_json(
        silent=True
    ) or {}

    token = data.get(
        "staging_token"
    )

    skip_incomplete = data.get(
        "skip_incomplete",
        False,
    )

    if not isinstance(
        skip_incomplete,
        bool,
    ):
        return jsonify({
            "error": True,
            "msg": (
                "skip_incomplete "
                "debe ser booleano."
            ),
        }), 400

    try:
        stage_directory = (
            RedEdgeStagingService
            .get_stage_directory(
                token=token,
                campaign_id=campaign_id,
            )
        )

        result = (
            RedEdgeImportService
            .import_folder(
                campaign_id=campaign_id,
                source_dir=stage_directory,
                capture_role=data.get("capture_role", "UNKNOWN"),
                skip_incomplete=(
                    skip_incomplete
                ),
            )
        )

        (
            RedEdgeStagingService
            .delete_stage(
                token=token,
                campaign_id=campaign_id,
            )
        )

        return jsonify(
            result
        ), 201

    except (
        InvalidRedEdgeStageError,
        InvalidRedEdgeCampaignError,
        InvalidRedEdgeFolderError,
        IncompleteRedEdgeFolderError,
        InvalidRedEdgeCaptureRoleError,
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except (
        RedEdgeImportExecutionError,
        RedEdgeStagingError,
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    (
        "/api/campaigns/"
        "<int:campaign_id>/"
        "rededge/staging/"
        "<string:token>"
    ),
    methods=["DELETE"],
)
def api_delete_rededge_staging(
    campaign_id,
    token,
):

    try:
        deleted = (
            RedEdgeStagingService
            .delete_stage(
                token=token,
                campaign_id=campaign_id,
            )
        )

        return jsonify({
            "deleted": deleted,
        })

    except InvalidRedEdgeStageError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 404

    except RedEdgeStagingError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    "/api/campaigns/<int:campaign_id>/rededge/staging",
    methods=["POST"],
)
def api_create_rededge_staging(
    campaign_id
):

    try:
        campaign = (
            CampaignService.get_campaign(
                campaign_id
            )
        )

        if campaign is None:
            return jsonify({
                "error": True,
                "msg": (
                    "La campaña con ID "
                    f"{campaign_id} no existe."
                ),
            }), 404

        if (
            campaign["sensor_type"]
            != "MULTISPECTRAL"
        ):
            return jsonify({
                "error": True,
                "msg": (
                    "La importación RedEdge "
                    "requiere una campaña "
                    "MULTISPECTRAL."
                ),
            }), 400

        token = (
            RedEdgeStagingService
            .create_stage(
                campaign_id=campaign_id
            )
        )

        return jsonify({
            "staging_token": token,
        }), 201

    except RedEdgeStagingError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    (
        "/api/campaigns/"
        "<int:campaign_id>/"
        "rededge/staging/"
        "<string:token>/files"
    ),
    methods=["POST"],
)
def api_upload_rededge_stage_files(
    campaign_id,
    token,
):

    stored_paths = []

    try:
        (
            RedEdgeStagingService
            .get_stage_directory(
                token=token,
                campaign_id=campaign_id,
            )
        )

        uploads = (
            request.files.getlist(
                "files"
            )
        )

        if not uploads:
            return jsonify({
                "error": True,
                "msg": (
                    "No se recibieron archivos."
                ),
            }), 400

        max_files = (
            Config
            .RED_EDGE_UPLOAD_BATCH_MAX_FILES
        )

        if len(uploads) > max_files:
            return jsonify({
                "error": True,
                "msg": (
                    "El lote supera el máximo "
                    f"de {max_files} archivos."
                ),
            }), 400

        relative_paths_raw = (
            request.form.get(
                "relative_paths"
            )
        )

        if not relative_paths_raw:
            return jsonify({
                "error": True,
                "msg": (
                    "No se recibieron "
                    "las rutas relativas."
                ),
            }), 400

        try:
            relative_paths = (
                json.loads(
                    relative_paths_raw
                )
            )

        except json.JSONDecodeError:
            return jsonify({
                "error": True,
                "msg": (
                    "relative_paths "
                    "no contiene JSON válido."
                ),
            }), 400

        if not isinstance(
            relative_paths,
            list,
        ):
            return jsonify({
                "error": True,
                "msg": (
                    "relative_paths debe "
                    "ser una lista."
                ),
            }), 400

        if (
            len(relative_paths)
            != len(uploads)
        ):
            return jsonify({
                "error": True,
                "msg": (
                    "La cantidad de archivos "
                    "no coincide con las "
                    "rutas relativas."
                ),
            }), 400

        for (
            upload,
            relative_path,
        ) in zip(
            uploads,
            relative_paths,
        ):

            stored = (
                RedEdgeStagingService
                .stage_file(
                    token=token,
                    campaign_id=campaign_id,
                    relative_path=(
                        relative_path
                    ),
                    source_stream=(
                        upload.stream
                    ),
                )
            )

            stored_paths.append(
                stored
            )

        return jsonify({
            "stored_count": (
                len(stored_paths)
            ),
        }), 201

    except InvalidRedEdgeStageError as exc:

        # Revertimos únicamente los archivos
        # incorporados por este lote.
        for path in reversed(
            stored_paths
        ):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except RedEdgeStagingError as exc:

        for path in reversed(
            stored_paths
        ):
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500

@app.route(
    (
        "/api/campaigns/"
        "<int:campaign_id>/"
        "rededge/staging/"
        "<string:token>/inspect"
    ),
    methods=["POST"],
)
def api_inspect_rededge_stage(
    campaign_id,
    token,
):

    try:
        inspection = (
            RedEdgeStagingService
            .inspect_stage(
                token=token,
                campaign_id=campaign_id,
            )
        )

        if (
            inspection["group_count"]
            == 0
        ):
            return jsonify({
                "error": True,
                "msg": (
                    "No se detectaron "
                    "capturas RedEdge."
                ),
            }), 400

        return jsonify({
            "staging_token": token,
            "inspection": inspection,
        })

    except (
        InvalidRedEdgeStageError,
        InvalidRedEdgeFolderError,
        RedEdgeImportError,
    ) as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 400

    except RedEdgeStagingError as exc:

        return jsonify({
            "error": True,
            "msg": str(exc),
        }), 500


if __name__ == "__main__":

    # Crea las tablas que falten (necesario en una
    # instalación nueva; no modifica datos existentes).
    from database import init_database

    init_database()

    log_dir = (
        Path(__file__).resolve().parent
        / "diagnosticos_smart_controller"
    )

    log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_handler = RotatingFileHandler(
        log_dir / "flask.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )

    log_handler.setLevel(logging.INFO)

    log_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        )
    )

    app.logger.addHandler(log_handler)
    app.logger.setLevel(logging.INFO)

    app.logger.info(
        "PE2_DIAGNOSTIC_START host=%s port=%s",
        Config.FLASK_HOST,
        Config.FLASK_PORT,
    )

    app.run(
        debug=Config.FLASK_DEBUG,
        host=Config.FLASK_HOST,
        port=Config.FLASK_PORT,
    )

