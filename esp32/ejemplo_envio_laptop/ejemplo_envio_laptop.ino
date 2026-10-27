/*
 * CULTIVA - Ejemplo de envío de bandas RedEdge a la laptop por Wi-Fi
 * Placa: ESP32-C5 (Arduino-ESP32 3.x)
 *
 * Este ejemplo solo cubre el tramo ESP32 -> laptop.
 * Cómo obtiene el ESP32 cada TIFF de la cámara (por el W5500)
 * depende de su firmware: basta con tener un Stream con los
 * bytes del archivo y su tamaño, y llamar a enviarBanda().
 *
 * Protocolo (ver README del repositorio):
 *   GET  http://<laptop>:5050/api/esp32/ping
 *   POST http://<laptop>:5050/api/esp32/upload?filename=IMG_0142_1.tif
 *        Content-Type: application/octet-stream
 *        cuerpo = bytes del TIFF
 */

#include <WiFi.h>
#include <HTTPClient.h>

// ---------- Configuración ----------
const char* WIFI_SSID     = "TU_RED";
const char* WIFI_PASSWORD = "TU_CLAVE";

// Dirección que muestra la pantalla "Adquisición de datos".
const char* LAPTOP_URL    = "http://192.168.1.50:5050";

// Déjalo vacío si no definiste ESP32_DEVICE_KEY en el .env.
const char* DEVICE_KEY    = "";
const char* DEVICE_ID     = "cultiva-esp32c5";

const int   MAX_INTENTOS  = 3;

// ---------- Utilidades ----------
void conectarWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Conectando a Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.print("\nIP del ESP32: ");
  Serial.println(WiFi.localIP());
}

void agregarCabeceras(HTTPClient& http) {
  http.addHeader("X-Device-Id", DEVICE_ID);
  if (strlen(DEVICE_KEY) > 0) {
    http.addHeader("X-Device-Key", DEVICE_KEY);
  }
}

// Devuelve true si la laptop está lista para recibir.
bool laptopRecibiendo() {
  HTTPClient http;
  http.begin(String(LAPTOP_URL) + "/api/esp32/ping");
  agregarCabeceras(http);

  int codigo = http.GET();
  String respuesta = http.getString();
  http.end();

  if (codigo != 200) {
    Serial.printf("Ping falló (%d)\n", codigo);
    return false;
  }
  respuesta.replace(" ", "");
  return respuesta.indexOf("\"receiving\":true") >= 0;
}

/*
 * Envía una banda a la laptop.
 *   datos  : Stream con los bytes del TIFF (posicionado al inicio)
 *   tamano : tamaño exacto en bytes
 *   ruta   : nombre del archivo, p. ej. "IMG_0142_1.tif", o la ruta
 *            con su carpeta SET, p. ej. "0001SET/000/IMG_0142_1.tif"
 *
 * Códigos de respuesta:
 *   200 recibido | 400 archivo inválido (no reintentar)
 *   401 clave incorrecta | 409 la laptop no está recibiendo
 */
int enviarBanda(Stream* datos, size_t tamano, const String& ruta) {
  String nombre = ruta.substring(ruta.lastIndexOf('/') + 1);

  HTTPClient http;
  http.setTimeout(30000);
  http.begin(String(LAPTOP_URL) + "/api/esp32/upload?filename=" + nombre);
  http.addHeader("Content-Type", "application/octet-stream");
  if (ruta != nombre) {
    http.addHeader("X-Path", ruta);
  }
  agregarCabeceras(http);

  int codigo = http.sendRequest("POST", datos, tamano);
  String respuesta = http.getString();
  http.end();

  Serial.printf("%s -> %d %s\n", ruta.c_str(), codigo, respuesta.c_str());
  return codigo;
}

/*
 * Ejemplo de uso con reintentos. "abrirBanda" representa la parte
 * de su firmware que entrega el archivo desde la cámara. Si el
 * Stream no se puede rebobinar, hay que volver a pedirlo a la
 * cámara en cada intento.
 */
// bool enviarConReintentos(const String& ruta) {
//   for (int intento = 1; intento <= MAX_INTENTOS; intento++) {
//     size_t tamano = 0;
//     Stream* datos = abrirBanda(ruta, &tamano);   // <- su firmware
//     if (datos == nullptr) return false;
//
//     int codigo = enviarBanda(datos, tamano, ruta);
//     if (codigo == 200) return true;
//     if (codigo == 400 || codigo == 401) return false;  // no reintentar
//     delay(1000 * intento);
//   }
//   return false;
// }

void setup() {
  Serial.begin(115200);
  conectarWiFi();

  while (!laptopRecibiendo()) {
    Serial.println("Esperando que la laptop inicie la recepción...");
    delay(3000);
  }
  Serial.println("Laptop lista para recibir.");

  // Por cada captura, enviar las 5 bandas:
  // for (int banda = 1; banda <= 5; banda++) {
  //   enviarConReintentos("0001SET/000/IMG_0142_" + String(banda) + ".tif");
  // }
}

void loop() {
  conectarWiFi();

  // Señal de vida cada 5 s para que la interfaz muestre
  // el módulo como conectado.
  static unsigned long ultimoPing = 0;
  if (millis() - ultimoPing > 5000) {
    ultimoPing = millis();
    laptopRecibiendo();
  }
}
