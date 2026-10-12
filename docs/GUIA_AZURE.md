# Guía paso a paso — publicar la calculadora y abrir los JSON en Azure

**Objetivo:** primera publicación desde cero y, después, actualización totalmente automática cuando hagas un commit en GitHub `main`.

> Esta guía presupone que tienes permisos sobre la suscripción, el registro, App Service y una identidad de Microsoft Entra. Algunas cuentas Azure for Students restringen registros de identidades, regiones o planes. Si algo aparece bloqueado por políticas, pide habilitación al administrador; no se puede resolver modificando solo el YAML.

## 1. Subir el ZIP a GitHub

1. Descomprime `Calculadora-Docker-CosmosDB.zip`.
2. Crea un repositorio nuevo de GitHub (por ejemplo `Calculadora-Docker-CosmosDB`) con rama `main`, o utiliza uno existente que quieras sustituir. Si reutilizas `Pagina-Web-Simple`, elimina/desactiva los workflows antiguos de Node/Python: no conserves múltiples workflows activos desplegando a la misma App Service.
3. Sube **el contenido** de la carpeta, no el ZIP ni una carpeta contenedora. Es importante incluir la carpeta oculta **`.github/workflows/`** con `deploy-azure-docker.yml`.
4. Comprueba que `Dockerfile`, `server.py`, `index.html`, `requirements.txt` y `.github/workflows/deploy-azure-docker.yml` estén en sus rutas correctas. GitHub Desktop o `git add -A` es más fiable que arrastrar una carpeta oculta al sitio web.
5. Haz commit en `main`. **Al principio Actions fallará por falta de secretos/variables de Azure; eso es normal** hasta completar la configuración.

## 2. Crear la base de datos documental JSON: Azure Cosmos DB

1. Abre [portal.azure.com](https://portal.azure.com) y busca **Azure Cosmos DB** → **Crear**.
2. Selecciona **Azure Cosmos DB for NoSQL** (no MongoDB ni Table). Elige tu suscripción, un grupo de recursos y una región **admitida** por Azure for Students.
3. Si está disponible y eres elegible, activa la opción **Apply Free Tier Discount** y elige **Provisioned throughput**. No confundas **Serverless** con el nivel gratuito; son modalidades distintas. Confirma siempre el precio antes de crear.
4. Al terminar, abre la cuenta → **Data Explorer / Explorador de datos** → **New Container / Nuevo contenedor**.
5. Configura exactamente:

| Campo | Valor |
|---|---|
| Base de datos | **Crear nueva** `CalculadoraDB` |
| Contenedor (Container ID) | `sumas` |
| Partition key / Clave de partición | **`/tipo`** |
| Throughput | Si usas nivel gratuito con rendimiento aprovisionado, elige una configuración de RU/s dentro del nivel elegible (por ejemplo 400 RU/s donde se admita). |

6. Guarda. Ahora existe una base vacía. **Los documentos JSON aparecerán después de que alguien haga una suma desde la página desplegada**.
7. Para localizar las credenciales, entra a tu cuenta Cosmos → **Keys / Claves**. Copia el **URI** (o endpoint) y la **PRIMARY KEY**. **No los pegues en GitHub ni en el Dockerfile**.

## 3. Preparar el Azure Container Registry

1. En Azure busca **Registros de contenedores**. Puedes reutilizar el existente `registrocalculadora2026` si sigue activo y pertenece a la suscripción correcta.
2. Si necesitas crear otro, elige un nombre único global, región autorizada, grupo de recursos y nivel disponible (por ejemplo Basic, que **puede tener costo**).
3. Comprueba el nombre exacto del registro y la suscripción en **Información general**.
4. El repositorio de la imagen se llamará **`calculadora-registros`**. Aún no aparecerá si no has publicado ninguna imagen.

## 4. Primera imagen Docker en el registro (inicialización)

El workflow de GitHub puede construir la primera imagen, pero **necesitas primero conceder sus permisos** (pasos 6 y 7). Alternativamente, si tu repo GitHub ya es público, construye una imagen de arranque desde **Azure Cloud Shell (Bash)**:

```bash
az account show --query '{subscription:id,name:name}' -o json
az acr build \
  --registry registrocalculadora2026 \
  --image calculadora-registros:inicial \
  https://github.com/USUARIO/REPOSITORIO.git
```

Sustituye `USUARIO/REPOSITORIO` y el nombre del registro. **Para repos privados** esa URL sin autenticación no funcionará: usa GitHub Actions (pasos 6 y 7) para construir la primera imagen, o un entorno local con acceso autorizado. Si el flujo publica la imagen pero falla al desplegar porque falta la Web App, podrás crear la Web App después seleccionando el tag publicado y volver a ejecutar Actions.

Para comprobar imágenes en Cloud Shell:

```bash
az acr repository show-tags --name registrocalculadora2026 --repository calculadora-registros -o table
```

## 5. Crear una Web App **de contenedor** y conectar Cosmos DB

1. Azure Portal → **App Services** → **Crear → Aplicación web**.
2. Elige nombre nuevo, por ejemplo `paginacalculadora-registros` (si está disponible), suscripción, grupo de recursos y región autorizada.
3. En **Publicar**, selecciona **Contenedor** (NO **Código**), sistema operativo **Linux** y un plan que admita contenedores. Comprueba el precio: algunas combinaciones de región/plan no incluyen nivel gratuito.
4. En la pestaña **Contenedor**, elige **Azure Container Registry**, tu registro, repositorio `calculadora-registros` y la etiqueta `inicial` (o el SHA creado por GitHub Actions).
5. La Web App necesita permiso de extracción para descargar imágenes privadas de ACR. Activa su **Identidad → Asignada por el sistema → Activado** y guarda. En el **Control de acceso (IAM)** del registro, asigna a esa identidad:
   - **AcrPull** en registros de modo RBAC clásico.
   - **Container Registry Repository Reader** (y Catalog Lister si es necesario para exploración de catálogo) en registros habilitados para RBAC + ABAC.
6. Configura la Web App para usar esa identidad al descargar imágenes. En Cloud Shell puedes usar el comando oficial, reemplazando nombres:

```bash
az webapp config set \
  --resource-group MI_GRUPO \
  --name paginacalculadora-registros \
  --acr-use-identity true \
  --acr-identity "[system]"
```

7. En la Web App abre **Configuración → Variables de entorno → Configuración de aplicación** y crea estas variables:

| Nombre | Valor |
|---|---|
| `DB_BACKEND` | `cosmos` |
| `COSMOS_ENDPOINT` | URI de Cosmos, por ejemplo `https://mi-cosmos.documents.azure.com:443/` |
| `COSMOS_KEY` | Clave de la cuenta Cosmos DB (valor **privado**) |
| `COSMOS_DATABASE` | `CalculadoraDB` |
| `COSMOS_CONTAINER` | `sumas` |
| `TRUST_PROXY_HEADERS` | `1` |
| `WEBSITES_PORT` | `8080` |

Guarda y reinicia si Azure lo solicita. No es necesario poner `python server.py` en **Startup Command**: lo ejecuta `CMD` en el `Dockerfile`.

8. Comprueba la URL de la Web App. `/health` debe responder `{"estado":"ok"}`. Eso prueba el servicio web, pero para verificar la base de datos debes hacer una **suma real** y consultar Data Explorer.

**Nota:** las credenciales en variables de entorno son un punto de partida didáctico. Para producción, considera **Azure Key Vault** y una identidad administrada para Cosmos DB con permisos de datos de mínimo privilegio.

## 6. Crear identidad GitHub → Azure sin contraseña (OIDC)

Este proyecto trae un YAML propio en `.github/workflows/deploy-azure-docker.yml`. **No hace falta que Azure cree otro workflow desde Deployment Center**: hacerlo podría duplicar despliegues. La configuración siguiente permite usar el YAML suministrado.

1. Azure Portal → **Microsoft Entra ID** → **App registrations / Registros de aplicaciones** → **New registration**. Nombre sugerido `github-calculadora-registros`.
2. Abre la aplicación y copia **Application (client) ID** y **Directory (tenant) ID**.
3. Ve a **Certificates & secrets / Certificados y secretos → Federated credentials / Credenciales federadas → Add credential**.
4. Tipo: **GitHub Actions deploying Azure resources** (puede variar la traducción). Configura:
   - Organization/User: propietario del repositorio, por ejemplo `artemioadaysolvers`.
   - Repository: nombre **exacto** del repositorio que elegiste.
   - Entity type: **Branch**.
   - Branch: **`main`**.
   - Nombre de la credencial: uno descriptivo.
5. Confirma. No necesitas crear secretos de contraseña de esa aplicación.
6. En **ACR → Control de acceso (IAM)** asigna a la **aplicación GitHub**, no a tu usuario personal:
   - `AcrPush` si tu ACR usa RBAC clásico.
   - `Container Registry Repository Writer` si usa RBAC + ABAC.
7. En **Web App → Control de acceso (IAM)** concede a la misma aplicación los permisos de implementación de la Web App (por ejemplo **Website Contributor** si se ajusta a tu configuración, o **Contributor** limitado al recurso Web App). No concedas roles a nivel de suscripción salvo que sea necesario y esté autorizado.
8. La aplicación necesita también acceso de lectura al recurso ACR en el plano de control para ejecutar `az acr show`. Concede **Reader** en el recurso ACR si falla ese paso por falta de permisos; `AcrPush` por sí solo puede no bastar para descubrir metadatos del registro.

Si tu institución no permite crear registros de aplicaciones o asignar roles, necesitarás ayuda del administrador de la suscripción/tenant.

## 7. Configurar secretos y variables de GitHub

En el repositorio: **Settings → Secrets and variables → Actions**.

En la pestaña **Secrets**, crea:

| Secret | Valor |
|---|---|
| `AZURE_CLIENT_ID` | Application (client) ID de la identidad GitHub |
| `AZURE_TENANT_ID` | Directory (tenant) ID de Microsoft Entra |
| `AZURE_SUBSCRIPTION_ID` | ID de la suscripción Azure que contiene el ACR y la Web App |

En la pestaña **Variables**, crea:

| Variable | Valor de ejemplo |
|---|---|
| `ACR_NAME` | `registrocalculadora2026` |
| `AZURE_WEBAPP_NAME` | `paginacalculadora-registros` |

**No guardes `COSMOS_KEY` en el repositorio ni en el YAML**: el secreto Cosmos se configura directamente en Azure App Service (paso 5). Evita también pegarla en capturas o mensajes de error.

Si estás reutilizando un repositorio anterior, retira el workflow antiguo `main_paginacalculadora.yml` si despliega al mismo destino. **Conserva solo el workflow que deseas ejecutar**.

## 8. Desplegar automáticamente y comprobar

1. En GitHub abre **Actions** → **Docker - probar, construir y desplegar en Azure** → **Run workflow** (rama `main`). También puedes hacer un commit para iniciarlo.
2. El workflow realizará, en orden:
   - `docker build` con `python:3.14.8-slim`.
   - comprobación de versión y 13 pruebas de integración.
   - inicio de sesión OIDC con Azure.
   - comprobación del registro y `docker push` de la etiqueta del commit (`SHA`).
   - `azure/webapps-deploy` con la imagen del mismo `SHA`.
3. Espera a que aparezca la marca verde. Abre tu Web App Azure en la URL `https://NOMBRE.azurewebsites.net`.
4. Ingresa los números 2 y 3, acepta el registro de datos y calcula. Debes obtener resultado 5 con confirmación **Guardado en base de datos**.
5. Abre Cosmos DB → Data Explorer y comprueba que apareció el documento JSON.
6. Ahora cambia un texto de `index.html`, haz commit en `main` y observa GitHub Actions: **la aplicación se actualizará sola** cuando termine el deploy. La base Cosmos existente conserva el historial entre despliegues.

## 9. Abrir, consultar y exportar la base de datos JSON

1. Azure → **Azure Cosmos DB → tu cuenta → Data Explorer / Explorador de datos**.
2. Expande `CalculadoraDB` → `sumas` → **Items / Elementos**.
3. Haz clic en un elemento para visualizar el JSON de una suma.
4. Selecciona **New SQL Query / Nueva consulta SQL** y ejecuta:

```sql
SELECT TOP 100 c.fecha_hora_utc, c.ip_cliente,
       c.primera_celdilla, c.segunda_celdilla, c.resultado
FROM c
ORDER BY c.fecha_hora_utc DESC
```

5. Para obtener solamente los datos más recientes, ejecuta esta consulta con el editor de Cosmos. Si necesitas exportar a JSON en un archivo, usa las opciones de exportación disponibles en tu versión de Data Explorer o el SDK de Azure Cosmos DB; **no existe una URL pública para descargar el historial**, deliberadamente.
6. Restringe el acceso a Data Explorer y conserva la base con una política de borrado de registros. Si quieres retención automática de 30 días, configura un **TTL** de 2.592.000 segundos en el contenedor tras comprobar las consecuencias (borrará documentos antiguos).

## 10. Errores más comunes

| Problema | Causa probable / solución |
|---|---|
| `401` al ejecutar `az acr login` | Identidad de GitHub sin rol de publicación, registro en otra suscripción o ACR equivocado. Revisa suscripción, `ACR_NAME` y roles `AcrPush`/Repository Writer. |
| `az acr show` no encuentra el recurso | Registro o suscripción incorrectos, o la identidad no tiene permiso Reader para acceder a metadatos del recurso. |
| `No subscriptions found` | Credencial OIDC/rol/suscripción todavía sin acceso. Revisa federación, secretos y roles. |
| Error `Docker - ...` al probar | Consulta el trabajo en GitHub Actions, en particular la etapa de tests o construcción. |
| `503 Service Unavailable` en Azure | El contenedor puede no iniciar o no leer Cosmos. Revisa **App Service → Log stream / Secuencia de registro** y `WEBSITES_PORT=8080`. |
| `403` al conectar Cosmos DB | Clave equivocada, red/firewall o restricciones de la cuenta Cosmos. Comprueba endpoint, clave y red autorizada. |
| La suma dice que no se guardó | Cosmos no está disponible o la base/contenedor/clave de partición no coinciden. No se muestra éxito si no se guardó. |
| La página se ve pero no registra nada | Revisa la consola del navegador, la petición `/api/sumar` y luego los logs de Azure. Confirma el valor `DB_BACKEND=cosmos`. |
| Workflow no aparece en Actions | La carpeta debe ser exactamente `.github/workflows/` y el YAML debe estar allí, no en la raíz. |

## Costos, IP y normativa

- **Azure Container Registry Basic y App Service de contenedores pueden cobrar.** Cosmos DB puede incluir un nivel gratuito para cuentas elegibles, pero los consumos extra generan gastos. Crea alertas de presupuesto y verifica compatibilidad con Azure for Students.
- Guardar IP, fecha y hora es tratamiento de información potencialmente personal. Informa de la finalidad, acceso, conservación, políticas de privacidad y derechos correspondientes según legislación aplicable (en Chile, revisa las obligaciones vigentes). Evita capturar IP si no es necesaria.
- `X-Forwarded-For` se utiliza para intentar registrar IP de origen cuando App Service funciona detrás de proxies. Puede ser manipulada y no confirma identidad. Evita usarla en auditorías probatorias.
- En producción, añade protección centralizada contra bots, cuotas/rate-limiting, monitoreo, backup, política de TTL y controles de acceso de mínimo privilegio.

## Documentación oficial

- https://learn.microsoft.com/en-us/azure/cosmos-db/quickstart-portal
- https://learn.microsoft.com/en-us/azure/cosmos-db/free-tier
- https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container
- https://learn.microsoft.com/en-us/azure/app-service/deploy-container-github-action
- https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect
- https://learn.microsoft.com/en-us/azure/container-registry/container-registry-rbac-abac-repository-permissions
