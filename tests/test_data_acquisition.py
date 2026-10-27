from InterfazInit import app


def test_data_acquisition_view_is_available():
    app.config["TESTING"] = True

    client = app.test_client()
    response = client.get("/adquisicion_datos")

    assert response.status_code == 200

    html = response.get_data(as_text=True)

    for marker in (
        "Adquisición de datos",
        "Campaña destino",
        "Conexión con el módulo ESP32",
        "Recepción desde cámara",
        "Iniciar recepción",
        "Detener recepción",
        "Capturas guardadas",
    ):
        assert marker in html


def test_data_acquisition_contains_pe2_navigation():
    app.config["TESTING"] = True

    client = app.test_client()
    response = client.get("/adquisicion_datos")

    assert response.status_code == 200

    html = response.get_data(as_text=True)

    for href in (
        'href="/"',
        'href="/campaigns"',
        'href="/adquisicion_datos"',
        'href="/drone_operation"',
        'href="/mission_routes"',
    ):
        assert href in html

    assert 'class="active">Adquisición de datos' in html


def test_data_acquisition_uses_real_reception_api():
    app.config["TESTING"] = True

    client = app.test_client()
    response = client.get("/adquisicion_datos")

    html = response.get_data(as_text=True)

    for marker in (
        "/api/reception/status",
        "/api/reception/start",
        "/api/reception/stop",
        "/api/esp32/upload",
    ):
        assert marker in html

    # La simulación anterior fue reemplazada.
    assert "Simulación visual" not in html
    assert "335 capturas" not in html
