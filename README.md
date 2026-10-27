# Hortalizas Drone — PE2

El entorno validado para este repositorio usa **Python 3.12.7**, declarado también en `.python-version`. No se afirma compatibilidad con otras versiones de Python.

## Configuración

Copie `.env.example` a `.env` y complete los valores locales. Las variables consumidas por el proyecto son:

| Grupo | Variables |
| --- | --- |
| Flask | `FLASK_SECRET_KEY`, `FLASK_HOST`, `FLASK_PORT`, `FLASK_DEBUG` |
| Recepción Wi-Fi ESP32 | `ESP32_RECEPTION_ROOT`, `ESP32_DEVICE_KEY`, `ESP32_MAX_FILE_MB`, `ESP32_ONLINE_SECONDS` |
| Dron | `DRONE_TELEMETRY_STALE_SECONDS` |
| Persistencia | `DATABASE_URL`, `CAMPAIGN_STORAGE_ROOT` |
| Fotogrametría | `PHOTOGRAMMETRY_BACKEND`, `PROCESSING_ENABLED`, `PE2_DEMO_MODE`, `MOCK_PHOTOGRAMMETRY_DELAY_SECONDS`, `METASHAPE_EXECUTABLE` |
| Importación RedEdge | `RED_EDGE_STAGING_ROOT`, `RED_EDGE_UPLOAD_BATCH_MAX_FILES`, `RED_EDGE_STAGING_MAX_AGE_HOURS` |

`METASHAPE_EXECUTABLE` solo es obligatorio cuando `PHOTOGRAMMETRY_BACKEND=METASHAPE`. `PROCESSING_ENABLED` es un guard operativo de demostración; no es un sistema de autorización. `PE2_DEMO_MODE` orienta la navegación normal a campañas y oculta enlaces legacy, sin eliminar sus rutas directas.

## Recepción Wi-Fi desde el módulo ESP32-C5

La laptop ya no se conecta a una Raspberry Pi. El módulo de comunicación (ESP32-C5 + W5500) obtiene las imágenes de la cámara MicaSense RedEdge y las envía por Wi-Fi a la laptop, una banda por petición HTTP.

### Preparar la laptop

1. En `.env` use `FLASK_HOST=0.0.0.0`. Con `127.0.0.1` el ESP32 no puede llegar a la laptop.
2. Conecte la laptop y el ESP32 a la misma red Wi-Fi.
3. En Windows, la primera vez que inicie Flask acepte el aviso del Firewall para **redes privadas**. Si no apareció, permita el puerto `5050` (TCP) en *Firewall de Windows → Configuración avanzada → Reglas de entrada*.
4. Inicie Flask y abra **Adquisición de datos**. La pantalla muestra la dirección que debe usar el ESP32, por ejemplo `http://192.168.1.50:5050`.
5. Elija la campaña destino (MULTISPECTRAL) y el tipo de captura, y pulse **Iniciar recepción**.

Cada vez que llegan las 5 bandas de una captura (`IMG_0142_1.tif` … `IMG_0142_5.tif`), se registra en la campaña con sus datos GPS y fecha leídos de los metadatos MicaSense. Las capturas incompletas se muestran como pendientes y no se guardan.

### Protocolo para el ESP32

| Acción | Petición |
| --- | --- |
| Comprobar conexión | `GET /api/esp32/ping` → `{"ok": true, "receiving": true/false}` |
| Enviar una banda | `POST /api/esp32/upload?filename=IMG_0142_1.tif` con el TIFF como cuerpo (`Content-Type: application/octet-stream`) |

- El nombre también puede ir en la cabecera `X-Filename`.
- Si la cámara usa varias carpetas SET, envíe además la ruta en `?path=0001SET/000/IMG_0142_1.tif` (o cabecera `X-Path`) para no mezclar capturas con el mismo número.
- Cabeceras opcionales: `X-Device-Id` (nombre del módulo) y `X-Device-Key` (obligatoria solo si se definió `ESP32_DEVICE_KEY`).
- También se acepta `multipart/form-data` con el campo `file`.

Respuestas: `200` archivo recibido (el JSON indica si la captura se completó y guardó), `400` archivo inválido (no reintentar), `401` clave incorrecta, `409` la laptop no está recibiendo (reintentar más tarde).

Hay un ejemplo de firmware en `esp32/ejemplo_envio_laptop/ejemplo_envio_laptop.ino`.

Para probar sin el ESP32, desde otra terminal:

```powershell
curl.exe -X POST "http://127.0.0.1:5050/api/esp32/upload?filename=IMG_0142_1.tif" -H "Content-Type: application/octet-stream" --data-binary "@IMG_0142_1.tif"
```

## Demo PE2

1. Active el entorno: `.\.venv\Scripts\Activate.ps1`.
2. Prepare `.env` para la demo con `FLASK_DEBUG=false`, `PROCESSING_ENABLED=false` y `PE2_DEMO_MODE=true`.
3. Ejecute `python scripts/preflight.py`.
4. Confirme `READY_FOR_VIEWING YES`.
5. Inicie una única instancia: `python InterfazInit.py`.
6. Abra `http://127.0.0.1:5050/`.
7. Recorra el flujo principal:
   - Inicio PE2.
   - Adquisición de datos.
   - Recepción visual desde cámara/bridge.
   - Ver campaña completada.
   - Visor GIS y productos espectrales.
   - Operación UAV.
   - Rutas predefinidas.
8. Mantenga `PROCESSING_ENABLED=false` durante la exposición si solo se visualizan productos persistidos.

La demo no necesita Metashape instalado si solo visualiza productos ya persistidos.

## Checklist: 5 minutos antes de exponer

1. Active `.venv`.
2. Confirme `FLASK_DEBUG=false`.
3. Confirme `PROCESSING_ENABLED=false`.
4. Confirme `PE2_DEMO_MODE=true`.
5. Ejecute `python scripts/preflight.py` y confirme `READY_FOR_VIEWING YES`.
6. Inicie una única instancia Flask.
7. Abra el inicio PE2 en `http://127.0.0.1:5050/`.
8. Revise la pantalla **Adquisición de datos**:
   - Campaña destino `CP-20260907-0006`.
   - Flujo visual de recepción desde cámara/bridge.
   - Barra de progreso.
   - Resumen final de transferencia.
   - Acceso a campaña completada.
9. Abra la campaña 6 y compruebe:
   - 335 capturas totales.
   - 328 capturas de vuelo.
   - 7 capturas de calibración.
   - 0 capturas sin clasificar.
10. Compruebe el visor GIS:
    - Ortomosaico multiespectral.
    - NDVI.
    - NDRE.
    - GNDVI.
    - Estadísticas espectrales.
11. Revise **Operación UAV** como pantalla de ruta, telemetría y estado operacional.
12. Revise **Rutas predefinidas** y el detalle de ruta.
13. No pulse ni pruebe procesamiento durante la exposición.
14. No interprete NDVI, NDRE o GNDVI como diagnóstico de enfermedad; son productos espectrales derivados.

## Procesamiento Metashape

Para ejecutar reconstrucción real se requiere licencia válida de Metashape, `PHOTOGRAMMETRY_BACKEND=METASHAPE`, `PROCESSING_ENABLED=true`, `METASHAPE_EXECUTABLE` apuntando al ejecutable y un directorio `TEMP/TMP` escribible. Ejecute `python scripts/preflight.py` y continúe solo con `READY_FOR_PROCESSING YES`.

Use una sola instancia Flask y `FLASK_DEBUG=false` para procesamientos largos. El executor de PE2 es local y no ofrece recuperación de jobs tras reinicio.

## Backup y restore

El respaldo geoespacial es la pareja formada por:

```text
data/ground_control.db
data/campaigns/
```

No respalde solo la base de datos.

Para respaldar, detenga Flask, cree una copia consistente con la API `sqlite3.Connection.backup`, copie `data/campaigns/` a la misma carpeta/versionado y conserve ambos artefactos con la misma fecha. Ejemplo de la API:

```python
import sqlite3

source = sqlite3.connect("data/ground_control.db")
target = sqlite3.connect(r"D:\backups\pe2-YYYYMMDD\ground_control.db")
source.backup(target)
target.close()
source.close()
```

Para restaurar, detenga Flask, restaure la base de datos y el árbol `data/campaigns/` del mismo respaldo, compruebe que ambos existen con `python scripts/preflight.py` e inicie la aplicación. No mezcle una base de datos con assets de otro respaldo.
