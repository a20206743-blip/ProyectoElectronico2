import io

import numpy as np
import pytest
from rasterio.io import MemoryFile

from config import Config
from InterfazInit import app
from services.campaign_service import CampaignService
from services.capture_service import CaptureService
from services.esp32_reception_service import (
    Esp32ReceptionService,
)


@pytest.fixture(autouse=True)
def isolated_reception(tmp_path, monkeypatch):

    monkeypatch.setattr(
        Config,
        "ESP32_RECEPTION_ROOT",
        str(tmp_path / "esp32"),
    )
    monkeypatch.setattr(
        Config,
        "CAMPAIGN_STORAGE_ROOT",
        str(tmp_path / "managed"),
    )
    monkeypatch.setattr(Config, "ESP32_DEVICE_KEY", "")

    Esp32ReceptionService.reset_for_tests()

    yield

    Esp32ReceptionService.reset_for_tests()


def client():
    app.config["TESTING"] = True
    return app.test_client()


def create_campaign(sensor_type="MULTISPECTRAL"):
    return CampaignService.create_campaign(
        name="Campaña ESP32",
        sensor_type=sensor_type,
        sensor_model=(
            "MicaSense RedEdge"
            if sensor_type == "MULTISPECTRAL"
            else "RGB"
        ),
    )


def tiff_bytes(value: int) -> bytes:
    data = np.full((8, 8), value, dtype=np.uint16)

    with MemoryFile() as memory_file:
        with memory_file.open(
            driver="GTiff",
            width=8,
            height=8,
            count=1,
            dtype="uint16",
        ) as dataset:
            dataset.write(data, 1)

        return memory_file.read()


def start(test_client, campaign_id, role="FLIGHT"):
    return test_client.post(
        "/api/reception/start",
        json={"campaign_id": campaign_id, "capture_role": role},
    )


def send_raw(test_client, name, body=None, headers=None, path=None):
    url = f"/api/esp32/upload?filename={name}"
    if path:
        url += f"&path={path}"

    return test_client.post(
        url,
        data=body if body is not None else tiff_bytes(1),
        content_type="application/octet-stream",
        headers=headers or {},
    )


def test_upload_is_rejected_when_reception_is_not_active():
    response = send_raw(client(), "IMG_0001_1.tif")

    assert response.status_code == 409
    assert "no está recibiendo" in response.get_json()["msg"]


def test_start_requires_multispectral_campaign():
    rgb = create_campaign("RGB")

    response = start(client(), rgb["id"])

    assert response.status_code == 400

    response = start(client(), 99999)

    assert response.status_code == 400


def test_only_one_active_session():
    campaign = create_campaign()
    test_client = client()

    assert start(test_client, campaign["id"]).status_code == 201
    assert start(test_client, campaign["id"]).status_code == 409


def test_five_bands_are_saved_as_one_capture():
    campaign = create_campaign()
    test_client = client()

    assert start(test_client, campaign["id"]).status_code == 201

    for band in range(1, 5):
        response = send_raw(
            test_client,
            f"IMG_0142_{band}.tif",
            tiff_bytes(band),
        )
        payload = response.get_json()

        assert response.status_code == 200
        assert payload["status"] == "stored"
        assert payload["bands_received"] == band
        assert "capture_status" not in payload

    status = test_client.get("/api/reception/status").get_json()
    assert status["pending_captures"][0]["missing_bands"] == ["RED_EDGE"]

    response = send_raw(test_client, "IMG_0142_5.tif", tiff_bytes(5))
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["capture_status"] == "saved"

    captures = CaptureService.list_captures(campaign["id"])
    assert len(captures) == 1
    assert captures[0]["status"] == "READY"
    assert captures[0]["role"] == "FLIGHT"
    assert len(captures[0]["assets"]) == 5

    status = test_client.get("/api/reception/status").get_json()
    assert status["files_received"] == 5
    assert status["captures_imported"] == 1
    assert status["pending_captures"] == []
    assert status["device"]["online"] is True
    assert status["last_capture_id"] == captures[0]["id"]


def test_resent_band_after_capture_saved_is_ignored():
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    for band in range(1, 6):
        send_raw(test_client, f"IMG_0001_{band}.tif", tiff_bytes(band))

    response = send_raw(test_client, "IMG_0001_3.tif", tiff_bytes(3))

    assert response.status_code == 200
    assert response.get_json()["status"] == "duplicate"
    assert len(CaptureService.list_captures(campaign["id"])) == 1


def test_same_image_number_in_different_sets_are_different_captures():
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"], role="CALIBRATION")

    for folder in ("0000SET/000", "0001SET/000"):
        for band in range(1, 6):
            response = send_raw(
                test_client,
                f"IMG_0000_{band}.tif",
                tiff_bytes(band),
                path=f"{folder}/IMG_0000_{band}.tif",
            )
            assert response.status_code == 200

    captures = CaptureService.list_captures(campaign["id"])
    assert len(captures) == 2
    assert {item["role"] for item in captures} == {"CALIBRATION"}


def test_multipart_upload_is_accepted():
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    response = test_client.post(
        "/api/esp32/upload",
        data={"file": (io.BytesIO(tiff_bytes(1)), "IMG_0007_1.tif")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["band"] == "BLUE"


@pytest.mark.parametrize(
    "name",
    ["foto.jpg", "IMG_0001_6.tif", "IMG_0001.tif", "../IMG_0001_1.tif"],
)
def test_invalid_names_are_rejected(name):
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    response = send_raw(test_client, name)

    assert response.status_code == 400


def test_non_tiff_content_is_rejected():
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    response = send_raw(test_client, "IMG_0001_1.tif", b"esto no es un tiff")

    assert response.status_code == 400
    assert "TIFF" in response.get_json()["msg"]

    response = send_raw(test_client, "IMG_0001_1.tif", b"")

    assert response.status_code == 400


def test_corrupt_tiff_is_reported_without_saving():
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    for band in range(1, 5):
        send_raw(test_client, f"IMG_0002_{band}.tif", tiff_bytes(band))

    # Cabecera TIFF válida pero contenido roto.
    response = send_raw(
        test_client,
        "IMG_0002_5.tif",
        b"II*\x00" + b"\x00" * 64,
    )

    assert response.status_code == 200
    assert response.get_json()["capture_status"] == "error"
    assert CaptureService.list_captures(campaign["id"]) == []

    status = test_client.get("/api/reception/status").get_json()
    assert status["captures_failed"] == 1
    assert status["pending_captures"] == []
    assert status["events"][0]["level"] == "error"


def test_oversized_file_is_rejected(monkeypatch):
    monkeypatch.setattr(Config, "ESP32_MAX_FILE_MB", 0.0001)

    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    response = send_raw(test_client, "IMG_0001_1.tif", tiff_bytes(1))

    assert response.status_code == 400
    assert "límite" in response.get_json()["msg"]


def test_device_key_is_enforced_when_configured(monkeypatch):
    monkeypatch.setattr(Config, "ESP32_DEVICE_KEY", "clave-cultiva")

    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    assert send_raw(test_client, "IMG_0001_1.tif").status_code == 401
    assert test_client.get("/api/esp32/ping").status_code == 401

    response = send_raw(
        test_client,
        "IMG_0001_1.tif",
        headers={"X-Device-Key": "clave-cultiva"},
    )

    assert response.status_code == 200


def test_ping_reports_reception_state_and_marks_device_online():
    test_client = client()

    response = test_client.get(
        "/api/esp32/ping",
        headers={"X-Device-Id": "cultiva-esp32c5"},
    )

    assert response.status_code == 200
    assert response.get_json()["receiving"] is False

    status = test_client.get("/api/reception/status").get_json()
    assert status["device"]["online"] is True
    assert status["device"]["device_id"] == "cultiva-esp32c5"

    campaign = create_campaign()
    start(test_client, campaign["id"])

    payload = test_client.get("/api/esp32/ping").get_json()
    assert payload["receiving"] is True
    assert payload["campaign_code"] == campaign["campaign_code"]


def test_stop_reports_incomplete_captures():
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    send_raw(test_client, "IMG_0003_1.tif", tiff_bytes(1))

    response = test_client.post("/api/reception/stop")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["active"] is False
    assert "incompletas" in payload["events"][0]["message"]

    assert test_client.post("/api/reception/stop").status_code == 409
    assert send_raw(test_client, "IMG_0003_2.tif").status_code == 409


def test_session_survives_server_restart():
    campaign = create_campaign()
    test_client = client()
    start(test_client, campaign["id"])

    for band in range(1, 4):
        send_raw(test_client, f"IMG_0004_{band}.tif", tiff_bytes(band))

    # Simula reinicio de Flask: se pierde el estado en memoria.
    Esp32ReceptionService.reset_for_tests()

    for band in range(4, 6):
        response = send_raw(
            test_client,
            f"IMG_0004_{band}.tif",
            tiff_bytes(band),
        )

    assert response.get_json()["capture_status"] == "saved"
    assert len(CaptureService.list_captures(campaign["id"])) == 1


def test_restarting_reception_on_same_campaign_does_not_duplicate():
    campaign = create_campaign()
    other = create_campaign()
    test_client = client()

    start(test_client, campaign["id"])
    for band in range(1, 6):
        send_raw(test_client, f"IMG_0005_{band}.tif", tiff_bytes(band))
    send_raw(test_client, "IMG_0006_1.tif", tiff_bytes(1))
    test_client.post("/api/reception/stop")

    reception_root = Esp32ReceptionService._root()
    old_sessions = [p for p in reception_root.iterdir() if p.is_dir()]
    assert len(old_sessions) == 1

    start(test_client, campaign["id"])

    # La sesión anterior se limpia al iniciar otra.
    assert not old_sessions[0].exists()

    response = send_raw(test_client, "IMG_0005_1.tif", tiff_bytes(1))
    assert response.get_json()["status"] == "duplicate"
    assert len(CaptureService.list_captures(campaign["id"])) == 1

    # En otra campaña la misma captura sí se guarda.
    test_client.post("/api/reception/stop")
    start(test_client, other["id"])

    for band in range(1, 6):
        response = send_raw(
            test_client,
            f"IMG_0005_{band}.tif",
            tiff_bytes(band),
        )

    assert response.get_json()["capture_status"] == "saved"
    assert len(CaptureService.list_captures(other["id"])) == 1


def test_status_includes_server_addresses():
    status = client().get("/api/reception/status").get_json()

    assert "addresses" in status["server"]
    assert status["server"]["port"] == Config.FLASK_PORT
    assert status["active"] is False
