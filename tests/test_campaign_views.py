from InterfazInit import app


def test_campaign_detail_contains_capture_roles_and_import_control():
    html = app.test_client().get("/campaigns/123").get_data(as_text=True)
    for marker in (
        'id="captureRoleSummary"', 'id="captureFlightCount"',
        'id="captureCalibrationCount"', 'id="captureUnknownCount"',
        'id="redEdgeCaptureRole"', 'value="UNKNOWN"', 'value="FLIGHT"',
        'value="CALIBRATION"', "capture_role:", "getCaptureRoleCounts",
        "getCaptureRoleLabel(capture)", "Capturas de vuelo",
        "Capturas de calibración", "Capturas sin clasificar",
    ):
        assert marker in html


def test_capture_map_layers_and_counts_are_independent():
    import re
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute frontend helpers")
    html = app.test_client().get("/campaigns/123").get_data(as_text=True)
    functions = re.findall(r"^function [\s\S]*?^}", html, re.MULTILINE)
    script = "\n".join(functions) + r'''
const assert = require("node:assert/strict");
const campaignMap = {
    layers: new Set(),
    hasLayer(layer) {return this.layers.has(layer);},
    removeLayer(layer) {this.layers.delete(layer); layer.visible = false;},
};
let gisCaptureLayers = {};
const elements = {};
const document = {getElementById(id) {return elements[id] ??= {};}};
const L = {
    layerGroup() {return {
        visible: false, markers: [],
        addTo(map) {this.visible = true; map.layers?.add(this); return this;},
        clearLayers() {this.markers = [];},
    };},
    circleMarker(coordinate, style) {return {
        coordinate, style,
        bindPopup(html) {this.popup = html;},
        addTo(layer) {layer.markers.push(this);},
    };},
};
let currentCaptures = [
    {role: "FLIGHT", latitude: 1, longitude: 2},
    {role: "CALIBRATION", latitude: 3, longitude: 4},
    {role: "UNKNOWN", latitude: 5, longitude: 6},
    {latitude: null, longitude: null},
];
initializeCaptureLayers();
assert.equal(gisCaptureLayers.FLIGHT.visible, true);
assert.equal(gisCaptureLayers.CALIBRATION.visible, false);
assert.equal(gisCaptureLayers.UNKNOWN.visible, false);
assert.notEqual(gisCaptureLayers.FLIGHT, gisCaptureLayers.CALIBRATION);
assert.deepEqual(getCaptureRoleCounts(currentCaptures), {FLIGHT:1, CALIBRATION:1, UNKNOWN:2});
assert.equal(renderCaptureGeometries([]), 3);
assert.equal(elements.gisCaptureCount.textContent, 3);
assert.equal(gisCaptureLayers.UNKNOWN.visible, true);
for (const layer of Object.values(gisCaptureLayers)) assert.equal(layer.markers.length, 1);
assert.deepEqual(gisCaptureLayers.CALIBRATION.markers[0].coordinate, [3, 4]);
assert.notDeepEqual(getCaptureMarkerStyle(currentCaptures[0]), getCaptureMarkerStyle(currentCaptures[1]));
gisCaptureLayers.FLIGHT.visible = false;
gisCaptureLayers.CALIBRATION.visible = true;
assert.equal(renderCaptureGeometries([]), 3);
assert.equal(gisCaptureLayers.FLIGHT.visible, false);
assert.equal(gisCaptureLayers.CALIBRATION.visible, true);
assert.deepEqual(getCaptureRoleCounts(currentCaptures), {FLIGHT:1, CALIBRATION:1, UNKNOWN:2});
renderCaptures(currentCaptures);
assert.equal(elements.summaryCaptures.textContent, "4 total");
assert.equal(elements.captureUnknownCount.textContent, 2);
renderCaptures([]);
assert.equal(elements.captureFlightCount.textContent, 0);
assert.equal(elements.captureUnknownCount.textContent, 0);
currentCaptures = currentCaptures.filter((capture) => getCaptureRole(capture) !== "UNKNOWN");
renderCaptureGeometries([]);
assert.equal(gisCaptureLayers.UNKNOWN.visible, false);
'''
    result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr


def test_campaigns_view_is_available():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns"
    )

    assert response.status_code == 200

    html = response.get_data(
        as_text=True
    )

    assert (
        "Campañas de adquisición"
        in html
    )

    assert (
        "/api/campaigns"
        in html
    )



def test_pe2_demo_mode_hides_legacy_navigation_but_keeps_direct_routes(
    monkeypatch,
):
    from InterfazInit import Config

    monkeypatch.setattr(Config, "PE2_DEMO_MODE", True)
    client = app.test_client()

    # El nuevo inicio PE2 se muestra directamente.
    index_response = client.get("/")
    assert index_response.status_code == 200

    home_html = index_response.get_data(as_text=True)
    assert "Plataforma UAV multiespectral" in home_html
    assert 'href="/campaigns"' in home_html
    assert 'href="/drone_operation"' in home_html

    # Ocultar accesos legacy durante la demostración.
    assert 'href="/flight_history"' not in home_html
    assert 'href="/ground_login"' not in home_html

    # Verificar navegación de campañas.
    campaigns_html = client.get("/campaigns").get_data(
        as_text=True
    )
    assert 'href="/flight_history"' not in campaigns_html
    assert "Campañas de adquisición" in campaigns_html

    # Verificar el detalle de campaña en modo demo.
    detail_html = client.get(
        "/campaigns/123"
    ).get_data(as_text=True)

    assert "Modo demostración" in detail_html
    assert "const PE2_DEMO_MODE =\n    true;" in detail_html
    assert "/api/campaigns/${CAMPAIGN_ID}/products" in detail_html

    # El historial de vuelos sigue disponible directamente.
    # El flujo legacy de la Raspberry Pi fue retirado.
    assert client.get("/ground_login").status_code == 404
    assert client.get("/flight_history").status_code == 200


def test_campaign_detail_view_is_available():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/123"
    )

    assert response.status_code == 200

    html = response.get_data(
        as_text=True
    )

    assert (
        "Detalle de campaña"
        in html
    )

    assert (
        "123"
        in html
    )

def test_campaign_detail_contains_domain_sections():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/123"
    )

    assert response.status_code == 200

    html = response.get_data(
        as_text=True
    )

    assert (
        "Información general"
        in html
    )

    assert "Capturas" in html

    assert "Procesamiento" in html

    assert (
        "Productos geoespaciales"
        in html
    )

    assert (
        "Procesar campaña"
        in html
    )


def test_campaign_detail_uses_geospatial_apis():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/7"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "/api/campaigns/${CAMPAIGN_ID}/captures"
        in html
    )

    assert (
        "/api/campaigns/${CAMPAIGN_ID}/processing-jobs"
        in html
    )

    assert (
        "/api/campaigns/${CAMPAIGN_ID}/products"
        in html
    )

    assert (
        "/api/campaigns/${CAMPAIGN_ID}/process"
        in html
    )


def test_campaign_detail_disables_processing_without_hiding_visualization(
    monkeypatch,
):

    from InterfazInit import Config

    monkeypatch.setattr(Config, "PROCESSING_ENABLED", False)

    html = app.test_client().get("/campaigns/123").get_data(as_text=True)

    assert "const PROCESSING_ENABLED =\n    false;" in html
    assert "Procesamiento deshabilitado" in html
    assert "/api/campaigns/${CAMPAIGN_ID}/products" in html


def test_campaign_detail_uses_presentation_labels_for_pe2():

    html = app.test_client().get("/campaigns/123").get_data(as_text=True)

    for marker in (
        "DSM · Modelo digital de superficie",
        "Ortomosaico multiespectral",
        "NIR y Rojo",
        "NIR y Red Edge",
        "NIR y Verde",
        "getProcessingBackendLabel",
        "summaryCurrentProcessing",
    ):
        assert marker in html

def test_campaign_detail_contains_rededge_import_controls():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/123"
    )

    assert response.status_code == 200

    html = response.get_data(
        as_text=True
    )

    assert (
        "Importar carpeta RedEdge"
        in html
    )

    assert (
        'id="redEdgeFolderInput"'
        in html
    )

    assert "webkitdirectory" in html

    assert (
        'id="redEdgeModal"'
        in html
    )


def test_campaign_detail_uses_rededge_import_api():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/9"
    )

    assert response.status_code == 200

    html = response.get_data(
        as_text=True
    )

    assert (
        "/rededge/import"
        in html
    )

    assert (
        "/rededge/staging/"
        in html
    )

def test_campaign_detail_contains_rededge_progress():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/11"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        'id="redEdgeUploadProgress"'
        in html
    )

    assert (
        'id="redEdgeProgressBar"'
        in html
    )

    assert (
        "buildRedEdgeBatches"
        in html
    )


def test_campaign_detail_uses_batch_staging_api():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/11"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "/rededge/staging"
        in html
    )

    assert (
        "/files"
        in html
    )

    assert (
        "/inspect"
        in html
    )

def test_campaign_detail_contains_gis_viewer():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/20"
    )

    assert response.status_code == 200

    html = response.get_data(
        as_text=True
    )

    assert (
        'id="campaignMap"'
        in html
    )

    assert "Visor GIS" in html

    assert (
        'id="gisCaptureCount"'
        in html
    )

    assert (
        'id="gisProductCount"'
        in html
    )


def test_campaign_detail_loads_leaflet():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/20"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "leaflet@1.9.4"
        in html
    )

    assert (
        "initializeCampaignMap"
        in html
    )

    assert (
        "renderCampaignMap"
        in html
    )


def test_campaign_detail_renders_geospatial_geometries():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/20"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "getCaptureCoordinate"
        in html
    )

    assert (
        "getProductWgs84Bounds"
        in html
    )

    assert (
        "renderCaptureGeometries"
        in html
    )

    assert (
        "renderProductGeometries"
        in html
    )

def test_campaign_detail_contains_raster_preview_state():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/25"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        'id="gisRasterCount"'
        in html
    )

    assert (
        "gisRasterLayer"
        in html
    )

    assert (
        "rasterPreviewPane"
        in html
    )


def test_campaign_detail_uses_geospatial_preview_endpoint():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/25"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "/preview.png"
        in html
    )

    assert (
        "L.imageOverlay"
        in html
    )


def test_campaign_detail_selects_primary_raster_product():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/25"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "selectRasterPreviewProduct"
        in html
    )

    assert (
        "ORTHOMOSAIC_RGB"
        in html
    )

    assert (
        "ORTHOMOSAIC_MULTISPECTRAL"
        in html
    )

def test_campaign_detail_does_not_convert_missing_coordinates_to_zero():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/30"
    )

    assert response.status_code == 200

    html = response.get_data(
        as_text=True
    )

    assert (
        "rawLatitude === null"
        in html
    )

    assert (
        "rawLongitude === null"
        in html
    )

    assert (
        "rawLatitude === undefined"
        in html
    )

    assert (
        "rawLongitude === undefined"
        in html
    )

def test_campaign_detail_contains_multispectral_layer_selector():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/40"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        'id="gisRasterMode"'
        in html
    )

    assert (
        'value="natural"'
        in html
    )

    assert (
        'value="blue"'
        in html
    )

    assert (
        'value="nir"'
        in html
    )

    assert (
        'value="red_edge"'
        in html
    )

def test_campaign_detail_preview_uses_raster_mode():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/40"
    )

    html = response.get_data(
        as_text=True
    )

    assert "new URLSearchParams" in html

    assert "mode: currentRasterMode" in html

    assert "band_palette: currentBandPalette" in html

    assert (
        "currentRasterMode"
        in html
    )

    assert "previewUrl +=" in html

    assert "params.toString()" in html

def test_campaign_detail_contains_spectral_analysis_controls():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/50"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "Análisis espectral"
        in html
    )

    assert (
        'data-index="NDVI"'
        in html
    )

    assert (
        'data-index="NDRE"'
        in html
    )

    assert (
        'data-index="GNDVI"'
        in html
    )

def test_campaign_detail_uses_spectral_index_api():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/50"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "/spectral-indices/"
        in html
    )

    assert (
        "generateSpectralIndex"
        in html
    )

    assert (
        "getSpectralSourceProduct"
        in html
    )

    assert (
        "hasSpectralIndex"
        in html
    )

def test_campaign_detail_contains_raster_product_selector():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/60"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        'id="gisRasterProduct"'
        in html
    )

    assert (
        "renderRasterProductSelector"
        in html
    )

    assert (
        "currentRasterProductId"
        in html
    )

def test_campaign_detail_contains_spectral_statistics_panel():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/60"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        'id="spectralMapPanel"'
        in html
    )

    assert (
        'id="spectralStatMin"'
        in html
    )

    assert (
        'id="spectralStatMean"'
        in html
    )

    assert (
        'id="spectralStatMax"'
        in html
    )

    assert (
        'id="spectralLegendGradient"'
        in html
    )

def test_campaign_detail_loads_selected_spectral_statistics():

    app.config["TESTING"] = True

    client = app.test_client()

    response = client.get(
        "/campaigns/60"
    )

    html = response.get_data(
        as_text=True
    )

    assert (
        "/statistics"
        in html
    )

    assert (
        "loadSelectedSpectralStatistics"
        in html
    )

    assert (
        "renderSpectralMapPanel"
        in html
    )

def test_campaign_detail_contains_processing_traceability():
    html = app.test_client().get("/campaigns/123").get_data(as_text=True)
    for marker in (
        "getLatestCompletedRealJob", "getProductProcessingState",
        "getProductTraceability", "getRasterProductOptionLabel",
        "getPreferredRasterProduct", "ACTUAL", "HISTORICO", "MOCK",
        'class="secondary-value product-traceability"',
        "product.processing_job_id === sourceProduct.processing_job_id",
    ):
        assert marker in html

def test_campaign_detail_contains_adaptive_resolution():
    html = app.test_client().get("/campaigns/123").get_data(as_text=True)
    for marker in (
        "formatProductResolution(product)", "formatResolutionValue",
        "toExponential(4)", "epsg === 4326", "32601", "32760", "m/px",
    ):
        assert marker in html

def test_campaign_detail_contains_escaped_expandable_errors():
    html = app.test_client().get("/campaigns/123").get_data(as_text=True)
    for marker in (
        "renderJobError(job.error_message)", 'details class="job-error-details"',
        "<summary>Ver detalles</summary>", "escapeHtml(summary)",
        "escapeHtml(fullError)", "compact.slice(0, 157)",
        "white-space: pre-wrap", "max-height: 320px", "overflow: auto",
    ):
        assert marker in html


def test_campaign_detail_has_satellite_basemap():
    app.config["TESTING"] = True

    response = app.test_client().get("/campaigns/40")

    assert response.status_code == 200

    html = response.get_data(as_text=True)

    for marker in (
        "server.arcgisonline.com",
        "tile.openstreetmap.org",
        "satelliteBaseLayer",
        "streetBaseLayer",
        "L.control.layers",
        "rasterPreviewPane",
    ):
        assert marker in html

def test_campaign_detail_helpers_behavior():
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute frontend helpers")
    html = app.test_client().get("/campaigns/123").get_data(as_text=True)
    # Extract declarations without executing page initialization or network requests.
    import re
    functions = re.findall(r"^function [\s\S]*?^}", html, re.MULTILINE)
    script = "\n".join(functions) + r'''

const assert = require("node:assert/strict");
let currentJobs = [
    {id: 91, backend: "MOCK", status: "COMPLETED", finished_at: "2030-02-01"},
    {id: 82, backend: "METASHAPE", status: "FAILED", finished_at: "2030-02-01"},
    {id: 71, backend: "METASHAPE", status: "COMPLETED", finished_at: "2030-01-02"},
    {id: 73, backend: "METASHAPE", status: "COMPLETED", finished_at: "2030-01-01"},
];
let currentProducts = [
    {id: 101, processing_job_id: 91, product_type: "ORTHOMOSAIC_MULTISPECTRAL"},
    {id: 102, processing_job_id: 73, product_type: "NDVI"},
    {id: 103, processing_job_id: 71, product_type: "ORTHOMOSAIC_MULTISPECTRAL", product_code: "GP-test"},
    {id: 104, processing_job_id: 71, product_type: "NDRE"},
];
assert.equal(getLatestCompletedRealJob().id, 71);
assert.equal(getPreferredRasterProduct(currentProducts).id, 103);
assert.equal(getSpectralSourceProduct().id, 103);
assert.equal(hasSpectralIndex("NDVI"), false);
assert.equal(hasSpectralIndex("NDRE"), true);
assert.equal(getProductProcessingState(currentProducts[0]), "MOCK");
assert.equal(getProductProcessingState(currentProducts[1]), "HISTORICO");
assert.equal(getProductProcessingState(currentProducts[2]), "ACTUAL");
assert.match(getRasterProductOptionLabel(currentProducts[2]), /^GP-test.*Metashape.*ACTUAL$/);
assert.equal(getPreferredRasterProduct([currentProducts[0], currentProducts[3]]).id, 104);
assert.equal(getPreferredRasterProduct(currentProducts.slice(0, 2)).id, 102);
assert.equal(formatProductResolution({epsg:4326, resolution:{x:9.0471e-7,y:7.2969e-7}}), "9.0471e-7° × 7.2969e-7°/px");
assert.equal(formatProductResolution({epsg:32718, resolution:{x:20,y:20}}), "20.0000 × 20.0000 m/px");
assert.match(formatProductResolution({epsg:9999, resolution:{x:1e-8,y:2e-8}}), /1.0000e-8/);
assert.equal(formatProductResolution({}), "--");
assert.equal(renderJobError(null), "--");
const error = "<script>alert(1)</script>\n" + "x".repeat(500);
const rendered = renderJobError(error);
assert.ok(!rendered.includes("<script>"));
assert.ok(rendered.includes(escapeHtml(error)));
assert.ok(rendered.includes("<summary>Ver detalles</summary>"));
currentJobs = currentJobs.filter(job => job.status === "COMPLETED" && job.backend !== "MOCK");
currentJobs.forEach(job => job.finished_at = null);
assert.equal(getLatestCompletedRealJob().id, 73);
currentJobs = [];
assert.equal(getSpectralSourceProduct(), null);
assert.equal(hasSpectralIndex("NDVI"), false);
'''
    result = subprocess.run(
        [node, "-"], input=script, text=True, encoding="utf-8",
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
