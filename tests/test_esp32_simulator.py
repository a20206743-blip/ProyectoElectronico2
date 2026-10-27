import time

import pytest

from config import Config
from InterfazInit import app
from services.campaign_service import CampaignService
from services.capture_service import CaptureService
from services.esp32_reception_service import Esp32ReceptionService
from services.esp32_simulator_service import (
    Esp32SimulatorService,
    capture_position,
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):

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

    Esp32SimulatorService.stop()
    Esp32SimulatorService.wait(30)
    Esp32ReceptionService.reset_for_tests()


def client():
    app.config["TESTING"] = True
    return app.test_client()


def start_reception(test_client):
    campaign = CampaignService.create_campaign(
        name="Simulación",
        sensor_type="MULTISPECTRAL",
        sensor_model="MicaSense RedEdge",
    )

    response = test_client.post(
        "/api/reception/start",
        json={"campaign_id": campaign["id"], "capture_role": "FLIGHT"},
    )
    assert response.status_code == 201

    return campaign


def test_simulator_requires_active_reception():
    response = client().post(
        "/api/esp32/simulator/start",
        json={"captures": 2},
    )

    assert response.status_code == 400
    assert "recepción" in response.get_json()["msg"]


def test_simulator_saves_georeferenced_captures():
    test_client = client()
    campaign = start_reception(test_client)

    response = test_client.post(
        "/api/esp32/simulator/start",
        json={"captures": 3, "band_delay_s": 0},
    )
    assert response.status_code == 201

    Esp32SimulatorService.wait(60)

    captures = CaptureService.list_captures(campaign["id"])
    assert len(captures) == 3
    assert all(item["status"] == "READY" for item in captures)
    assert all(len(item["assets"]) == 5 for item in captures)
    assert all(item["latitude"] is not None for item in captures)
    assert len({(c["latitude"], c["longitude"]) for c in captures}) == 3

    status = test_client.get("/api/reception/status").get_json()
    assert status["captures_imported"] == 3
    assert status["simulator"]["running"] is False
    assert status["simulator"]["sent_captures"] == 3
    assert status["device"]["device_id"] == "simulador-esp32"


def test_simulator_can_leave_last_capture_incomplete():
    test_client = client()
    campaign = start_reception(test_client)

    test_client.post(
        "/api/esp32/simulator/start",
        json={"captures": 2, "band_delay_s": 0, "drop_last_band": True},
    )
    Esp32SimulatorService.wait(60)

    assert len(CaptureService.list_captures(campaign["id"])) == 1

    status = test_client.get("/api/reception/status").get_json()
    assert status["pending_captures"][0]["missing_bands"] == ["RED_EDGE"]


def test_two_simulations_do_not_collide_as_duplicates():
    test_client = client()
    campaign = start_reception(test_client)

    for _ in range(2):
        test_client.post(
            "/api/esp32/simulator/start",
            json={"captures": 1, "band_delay_s": 0},
        )
        Esp32SimulatorService.wait(60)
        # Garantiza un nombre de carpeta SET distinto.
        time.sleep(1.1)

    assert len(CaptureService.list_captures(campaign["id"])) == 2


def test_stopping_reception_stops_simulator():
    test_client = client()
    start_reception(test_client)

    test_client.post(
        "/api/esp32/simulator/start",
        json={"captures": 50, "band_delay_s": 0.5},
    )

    test_client.post("/api/reception/stop")
    Esp32SimulatorService.wait(30)

    simulator = Esp32SimulatorService.status()
    assert simulator["running"] is False
    assert simulator["sent_captures"] < 50


def test_invalid_capture_count_is_rejected():
    test_client = client()
    start_reception(test_client)

    for value in (0, 1000, "abc"):
        response = test_client.post(
            "/api/esp32/simulator/start",
            json={"captures": value},
        )
        assert response.status_code == 400


def test_capture_positions_follow_a_zigzag_path():
    first_line = [capture_position(i) for i in range(6)]
    second_line = [capture_position(i) for i in range(6, 12)]

    # Primera línea de oeste a este; la segunda vuelve.
    assert first_line[0][1] < first_line[-1][1]
    assert second_line[0][1] > second_line[-1][1]
    assert second_line[0][0] < first_line[0][0]
