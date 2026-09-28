# Estado Global

Panel local de seguimiento de fenómenos naturales y alertas geolocalizadas. Está construido con Grafana, InfluxDB y un recolector ligero en Docker, aprovechando la arquitectura del proyecto CO₂ original.

## Qué muestra

- **Terremotos** publicados por el USGS.
- **Tsunamis** y boletines de los centros PTWC y NTWC.
- **Tornados** a partir de avisos activos del Servicio Meteorológico Nacional de Estados Unidos (NWS).
- **Ciclones tropicales, inundaciones, volcanes, incendios forestales y sequías** cuando GDACS publica alertas para esos fenómenos.
- **Detecciones satelitales de focos de calor** de NASA FIRMS, si se configura una clave gratuita. Un foco de calor no confirma por sí solo un incendio ni delimita su perímetro.

## Colores del mapa

| Color | Fenómeno |
|---|---|
| Naranja | Terremoto |
| Cian | Tsunami |
| Violeta | Tornado |
| Azul | Ciclón tropical |
| Turquesa | Inundación |
| Rojo | Incendio forestal |
| Amarillo | Volcán |
| Marrón | Sequía |

La clave de color permanece completa para facilitar la lectura; los marcadores solo aparecen cuando hay observaciones en el intervalo elegido. Cada punto tiene un halo tenue para destacar actividad sin parpadeos que distraigan. Al pulsar un marcador, la ficha empieza por país, ciudad o municipio y pueblo o localidad; después muestra el nombre del suceso y las coordenadas. El recolector completa los lugares con geocodificación inversa de OpenStreetMap y conserva los resultados en una caché local. Cuando no hay un nombre de localidad verificable, la ficha lo indica y mantiene las coordenadas originales. Los terremotos USGS también muestran magnitud.

## Puesta en marcha

1. Inicia Docker Desktop.
2. Copia `.env.example` como `.env` y configura una contraseña propia para Grafana. Docker no inicia Grafana si falta este ajuste.
3. Opcional: solicita una clave de NASA FIRMS en <https://firms.modaps.eosdis.nasa.gov/api/map_key> y añádela como `FIRMS_MAP_KEY` en `.env`.
4. Desde esta carpeta, inicia los servicios con `docker compose up -d --build`.
5. Abre <http://localhost:3000>. El panel se carga automáticamente en la carpeta **Estado Global**.

Grafana consulta inicialmente los últimos siete días y actualiza el panel cada diez minutos. El recolector consulta las fuentes con una frecuencia aproximada de diez minutos. Para detener los servicios: `docker compose down`. Los datos se conservan en volúmenes locales.

## Fuentes públicas

- USGS, terremotos: <https://earthquake.usgs.gov/earthquakes/feed/>
- GDACS, alertas y referencia de datos: <https://www.gdacs.org/feed_reference.aspx>
- PTWC/NTWC, boletines de tsunami: <https://www.tsunami.gov/?page=productRetrieval>
- NWS, API meteorológica: <https://www.weather.gov/documentation/services-web-api>
- NASA FIRMS, detecciones satelitales: <https://firms.modaps.eosdis.nasa.gov/web-services/>
- OpenStreetMap Nominatim, geocodificación inversa con caché y límite de cuatro consultas por ciclo: <https://nominatim.org/release-docs/develop/api/Reverse/>

## Cobertura y uso responsable

La disponibilidad depende de cada organismo y de sus criterios de publicación. Los avisos de tornado del NWS cubren Estados Unidos; no existe aquí una fuente mundial homogénea de tornados. NASA FIRMS necesita una clave para activar sus detecciones. Una ausencia de marcadores no significa que no exista riesgo. Este panel es informativo y no sustituye las alertas oficiales ni debe usarse para tomar decisiones de emergencia.

## Arquitectura y privacidad

`USGS · GDACS · PTWC/NTWC · NWS · NASA FIRMS opcional → recolector → InfluxDB → Grafana Geomap`

Los servicios se ejecutan en Docker en este ordenador. Grafana solo escucha en `127.0.0.1:3000` y la base de datos no se publica fuera de la red interna de Docker. Antes de compartir el panel, cambia las credenciales locales y revisa las condiciones de atribución de cada proveedor.
