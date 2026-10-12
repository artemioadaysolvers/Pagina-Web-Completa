# Calculadora con Docker, Python 3.14.8 y Azure Cosmos DB

**Proyecto completo desde cero.** Al sumar dos números se escribe un **documento JSON** con los valores, resultado, fecha, hora UTC e IP del visitante. En Azure, los documentos se guardan en **Azure Cosmos DB for NoSQL**, fuera del contenedor. Cada commit en `main` puede reconstruir la imagen Docker y actualizar Azure mediante GitHub Actions.

> **IMPORTANTE**: el despliegue automático requiere **configuración inicial** de Azure, roles de acceso, variables de GitHub y Cosmos DB. Subir el ZIP a GitHub no crea automáticamente los recursos ni concede permisos. Las claves nunca deben subirse a GitHub.

## Archivos

```text
Calculadora-Docker-CosmosDB/
├── index.html                 # interfaz con aviso de registro de IP
├── styles.css                 # diseño adaptable
├── app.js                     # envía las sumas al backend
├── favicon.svg
├── server.py                  # API, registro de IP, fecha/hora y almacenamiento
├── requirements.txt           # azure-cosmos==4.17.0
├── Dockerfile                 # imagen con Python 3.14.8
├── .dockerignore
├── .gitignore
├── .env.example
├── tests/test_app.py          # 13 pruebas automatizadas
├── .github/workflows/
│   └── deploy-azure-docker.yml # CI/CD en cada commit a main
└── docs/GUIA_AZURE.md         # instrucciones detalladas del portal
```

### Documento JSON en Cosmos DB

Un registro tendrá esta estructura (valores ilustrativos):

```json
{
  "id": "a8db5299-bf45-46ce-b1f2-58d5a891af85",
  "tipo": "suma",
  "fecha_hora_utc": "2026-10-12T20:15:30.120Z",
  "fecha_utc": "2026-10-12",
  "hora_utc": "20:15:30.120",
  "ip_cliente": "203.0.113.10",
  "primera_celdilla": 2,
  "segunda_celdilla": 3,
  "resultado": 5
}
```

`/tipo` es la clave de partición del contenedor en Cosmos DB. **Debes crear el contenedor con esta clave exacta**. La base de datos se llama `CalculadoraDB` y el contenedor `sumas` por defecto.

- `GET /` página web.
- `POST /api/sumar` envía JSON `{ "a": 2, "b": 3 }`, escribe en la base antes de devolver el resultado.
- `GET /health` verifica que el proceso HTTP responde; **no** es una prueba de conectividad a Cosmos DB.
- No existe una ruta pública para consultar las IP ni el historial.

## Probar localmente con Docker (sin Azure)

1. Instala y ejecuta Docker Desktop.
2. Descomprime el ZIP. En PowerShell, abre la carpeta del proyecto.
3. Construye e inicia:

```powershell
docker build -t calculadora-registros .
docker volume create calculadora-datos
docker run --name calculadora-local --rm -p 8080:8080 -v calculadora-datos:/app/data calculadora-registros
```

4. Abre **http://localhost:8080**; marca la casilla de aviso, introduce 2 y 3 y pulsa **Calcular y guardar**.
5. La base local es **SQLite**, con documentos JSON en la columna `documento_json`. Se guarda en el volumen Docker `calculadora-datos` y no se pierde al recrear el contenedor mientras conserves el volumen.
6. Para terminar pulsa `Ctrl+C`. No elimines el volumen si quieres conservar los registros.

Para comprobar la versión:

```powershell
docker run --rm --entrypoint python calculadora-registros --version
```

Resultado esperado: `Python 3.14.8`.

Sin Docker, puedes probar localmente con Python compatible e instalar las dependencias con `pip install -r requirements.txt` y ejecutar `python server.py`. La base SQLite se guarda en `data/sumas.sqlite3`. Esto **no** garantiza Python 3.14.8: solo Docker fija esa versión de la imagen.

## Desplegar en Azure

**Lee [docs/GUIA_AZURE.md](docs/GUIA_AZURE.md) y sigue los pasos en orden.**

Resumen de recursos necesarios:

1. **Azure Cosmos DB for NoSQL**: cuenta, base `CalculadoraDB`, contenedor `sumas` con clave `/tipo`.
2. **Azure Container Registry (ACR)**: por ejemplo el que ya creaste, `registrocalculadora2026`.
3. **Azure App Service (Linux, publicación Contenedor)**: por ejemplo `paginacalculadora-registros`. Revisa precios y regiones.
4. **Identidad de la Web App para descargar imágenes** del ACR (`AcrPull` o permiso equivalente).
5. **Variables en App Service**: `DB_BACKEND=cosmos`, `COSMOS_ENDPOINT`, `COSMOS_KEY`, `COSMOS_DATABASE=CalculadoraDB`, `COSMOS_CONTAINER=sumas`, `WEBSITES_PORT=8080`, `TRUST_PROXY_HEADERS=1`.
6. **Credencial federada para GitHub Actions** y roles: identidad de GitHub con permiso de **push** en ACR y despliegue en App Service.
7. **GitHub**: secretos `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` y variables `ACR_NAME`, `AZURE_WEBAPP_NAME`.

Luego, cada **commit en `main`** ejecutará `.github/workflows/deploy-azure-docker.yml`: pruebas, `docker build`, autenticación, `docker push` y `azure/webapps-deploy` apuntando a la imagen del nuevo commit.

### Ver los registros JSON en Azure

Azure Portal → **Azure Cosmos DB** → tu cuenta de Cosmos → **Data Explorer / Explorador de datos** → `CalculadoraDB` → `sumas` → **Items / Elementos**. Selecciona un documento para ver su JSON completo.

Consulta opcional en Data Explorer (New SQL Query):

```sql
SELECT TOP 100 c.fecha_hora_utc, c.ip_cliente,
       c.primera_celdilla, c.segunda_celdilla, c.resultado
FROM c
ORDER BY c.fecha_hora_utc DESC
```

### Privacidad, seguridad y costos

- Las **direcciones IP son datos personales** en muchos contextos. La página muestra un aviso y exige confirmación. Antes de publicar para terceros, establece finalidad, base legal, plazo de conservación y control de acceso acorde a la normativa aplicable. Considera anonimizar las IP si no son imprescindibles.
- Con `TRUST_PROXY_HEADERS=1` se toma la primera IP válida de `X-Forwarded-For` porque App Service se encuentra detrás de un proxy. **Esta cabecera puede ser manipulada**; no uses la IP registrada como identidad verificable ni como prueba de autoría.
- Cosmos DB está separado del contenedor: los datos sobreviven a nuevas imágenes y despliegues, pero puedes perderlos si borras la cuenta o el contenedor. Configura backups y, si procede, TTL para eliminación automática de datos antiguos.
- No expongas el contenido de Cosmos DB en el frontend; controla quién tiene acceso a Data Explorer y a las claves. Guarda `COSMOS_KEY` solo como configuración protegida de Azure App Service (idealmente Key Vault/identidad administrada en producción).
- Una web pública puede ser abusada y generar consumo en Cosmos DB. Para producción añade protección contra abuso, WAF/rate limiting centralizado, presupuestos/alertas y supervisión. La versión incluida es didáctica, no infraestructura endurecida de producción.
- **Cosmos DB**, **Container Registry** y el plan de **App Service** pueden producir cargos. Cosmos DB tiene una opción de nivel gratuito para cuentas elegibles, pero requiere seleccionarla al crear la cuenta. Consulta los precios antes de empezar.

## Notas de compatibilidad

- El Dockerfile fija la etiqueta oficial `python:3.14.8-slim` y la dependencia directa `azure-cosmos==4.17.0`. Dependencias transitivas y la imagen pueden variar al reconstruir si no se fijan hashes/digest; para reproducibilidad estricta adicional, fija un digest y un lockfile.
- El proyecto fue probado automáticamente en modo SQLite usando pruebas de integración; **para comprobar Cosmos DB real debes crear tu cuenta Azure y ejecutar una suma contra ella**.
- El `README.md` y los archivos `docs/` no entran en la imagen Docker.

## Documentación oficial

- [GitHub Actions para contenedores de App Service](https://learn.microsoft.com/en-us/azure/app-service/deploy-container-github-action)
- [Contenedor personalizado y puerto de App Service](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container)
- [Cosmos DB NoSQL en el portal](https://learn.microsoft.com/en-us/azure/cosmos-db/quickstart-portal)
- [Cosmos DB free tier](https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier)
- [Autenticación Azure/GitHub con OIDC](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
