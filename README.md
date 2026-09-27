# Sales Analytics Platform

Plataforma local para convertir archivos de ventas de un comercio de electrónica en indicadores reproducibles. Conserva el origen, distingue rechazos de advertencias y permite consultar una publicación consistente desde Streamlit, incluso mientras otro proceso publica datos.

El MVP utiliza CSV, Python 3.13.0, PostgreSQL 17.11, Psycopg 3.3.5 y Streamlit 1.64.0. Pytest y Ruff verifican el proyecto; pip-tools genera el bloqueo de dependencias. No requiere Docker.

![Dashboard: Resumen ejecutivo](docs/images/resumen-ejecutivo.jpg)

[Ver las cuatro capturas](docs/portfolio.md#capturas-finales) · [Ejecutar localmente](#instalación) · [Pruebas](#pruebas)

## Arquitectura

```mermaid
flowchart LR
    CSV[CSV originales] --> ETL[Python: extracción]
    ETL --> S[staging: texto original]
    S --> V[Validación]
    V --> DW[DW: transformación y publicación]
    DW --> M[marts: vistas SQL]
    M --> A[analytics: consultas parametrizadas]
    A --> R[read_dashboard: instantánea completa]
    R --> UI[Streamlit]
    O[ops: ejecuciones e incidencias] -. auditoría .-> ETL
    O -. auditoría .-> V
    O -. identidad confirmada .-> DW
    O -. metadatos .-> R
```

- **Origen:** seis archivos, incluido el diccionario. Cinco tablas de staging preservan valores textuales y ordinales; cada archivo se confirma por separado. Customers usa Windows-1252 y los demás UTF-8.
- **Calidad:** C1–C8 bloquean errores críticos, R1–R7 identifican registros rechazados y W1–W8 conservan advertencias. Solo un lote sin críticos ni rechazos puede publicar. No se eliminan atípicos válidos.
- **Modelo estrella:** una fila de hechos por línea de pedido, cuatro dimensiones —cliente, producto, sucursal y fecha— y tasas de referencia. StoreKey=0 representa Online. Precios y costos USD de catálogo quedan fijados en cada línea; no se aplica Exchange.
- **Publicación:** un bloqueo serializa escritores; datos, estado y publication_id se confirman en la misma transacción. Un fallo revierte los cambios analíticos y registra el diagnóstico fuera del rollback. Repetir el snapshot vigente registra `sin_cambios`, sin nueva publicación.
- **Lectura:** filtros, indicadores y metadatos comparten una conexión exclusiva y una transacción breve, de solo lectura, REPEATABLE READ. Se cierra antes de renderizar. No hay caché SQL ni conexiones compartidas; ante error se descarta el resultado parcial.

Contratos: [arquitectura](docs/architecture.md), [modelo](docs/data_model.md), [reglas y ejemplos](docs/business_rules.md), [perfil](docs/data_profile.md) y [diccionario](docs/data_dictionary.md). El [plan incremental](docs/implementation_plan.md) registra las entregas; el [portafolio](docs/portfolio.md) reúne resultados y capturas. Los documentos de diseño conservan el contexto de su aprobación inicial.

## Indicadores

K1–K10 residen en `sql/analytics.sql`: ingresos estimados, pedidos distintos, unidades, ticket estimado, crecimiento mensual, clientes nuevos observados, recurrentes en el período, ventas por producto, sucursal/canal y margen estimado.

Los nuevos usan la primera compra global; los recurrentes requieren dos pedidos distintos dentro de los filtros. Pueden solaparse. Los conteos distintos no se suman entre grupos. Bajo filtro de producto, el ticket representa solo las líneas seleccionadas. Los denominadores cero y los meses no comparables no se muestran como 0 %.

Snapshot de referencia, sin convertir sus cifras en restricciones de futuros lotes:

| Medida | Resultado |
|---|---:|
| Líneas | 62.884 |
| Pedidos | 26.326 |
| Unidades | 197.757 |
| Ingresos estimados | USD 55.755.479,59 |
| Costos estimados | USD 23.092.791,21 |
| Margen estimado | USD 32.662.688,38 |

## Estructura

```text
sales-analytics-platform/
├── AGENTS.md
├── README.md
├── pyproject.toml                 # única declaración editable de dependencias
├── requirements.lock             # generado, versiones y hashes
├── .env.example
├── .gitignore
├── docs/                         # contratos, instalación, evidencias y capturas
├── scripts/setup_databases.py    # preparación local explícita
├── sql/
│   ├── 001_schema.sql
│   ├── 002_marts.sql
│   └── analytics.sql
├── src/sales_analytics/           # configuración, ETL, auditoría, lectura y UI
└── tests/
    ├── unit/
    ├── integration/
    └── ui/
```

`Data/`, `.local/`, `.venv*/` y `.verification/` son exclusivamente locales y están excluidos. No se distribuyen los datos ni credenciales.

## Instalación

Entorno verificado: Windows x64 y Python **3.13.0**. Ejecutar desde la raíz en PowerShell; no hacen falta rutas personales absolutas.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip --isolated install --index-url https://pypi.org/simple --require-hashes -r requirements.lock
.\.venv\Scripts\python.exe -m pip --isolated install --no-index --no-deps --no-build-isolation .
.\.venv\Scripts\python.exe -m pip check
```

Comprobar el éxito de cada comando antes de continuar. El lock fija 51 paquetes para este entorno. Reinstalar el proyecto local después de modificar Python o SQL empaquetado. Para generar o actualizar el lock, seguir el [procedimiento único con pip-compile](docs/local_setup.md#actualizar-dependencias); no editarlo manualmente.

## Datos y configuración

Descargar el dataset [Global Electronics Retailer de Maven Analytics](https://mavenanalytics.io/data-playground/global-electronics-retailer) y disponer en `Data/`: `Customers.csv`, `Products.csv`, `Stores.csv`, `Sales.csv`, `Exchange_Rates.csv` y `Data_Dictionary.csv`. Consultar el perfil para los SHA-256 del snapshot utilizado. No se descarga ni modifica el dataset automáticamente.

Los CSV originales no forman parte del repositorio. Cada persona debe obtenerlos desde la fuente indicada; este proyecto no concede permisos para redistribuirlos.

`.env.example` documenta las variables, sin contraseñas. La aplicación lee el entorno del proceso: no carga un archivo `.env` automáticamente. `SALES_DATA_DIR` identifica el directorio de entrada; `SALES_WRITER_DATABASE_URL` pertenece al ETL y `SALES_READER_DATABASE_URL` al dashboard. Las variables `SALES_TEST_*` pertenecen únicamente a pruebas aisladas. `SALES_DATABASE_URL` conserva la validación inicial de configuración de E1.

### Crear las bases

Preparar primero PostgreSQL con la [guía local](docs/local_setup.md#postgresql-local). Los siguientes comandos son para una instalación nueva; nunca repetir la preparación sobre bases existentes. Las credenciales se generan en archivos privados bajo `.local/` y no se imprimen.

```powershell
$adminPassword = (Get-Content .local/admin.password -Raw).Trim()
$env:SALES_ADMIN_DATABASE_URL = "postgresql://postgres:${adminPassword}@127.0.0.1:55432/postgres"
.\.venv\Scripts\python.exe scripts/setup_databases.py prepare
.\.venv\Scripts\python.exe scripts/setup_databases.py schema
.\.venv\Scripts\python.exe scripts/setup_databases.py marts
.\.venv\Scripts\python.exe scripts/setup_databases.py prepare --isolated-tests
.\.venv\Scripts\python.exe scripts/setup_databases.py schema --isolated-tests
.\.venv\Scripts\python.exe scripts/setup_databases.py marts --isolated-tests
Remove-Item Env:SALES_ADMIN_DATABASE_URL
Remove-Variable adminPassword
```

Detenerse ante cualquier error. El administrador solo prepara los objetos; la operación usa roles separados. El lector no tiene permisos de escritura ni acceso directo a staging o DW.

| Base | Uso | Roles escritor / lector |
|---|---|---|
| sales_analytics | Publicación consultada por el dashboard | sales_etl / sales_reader |
| sales_analytics_test | Publicación de aceptación preservada | sales_test_etl / sales_test_reader |
| sales_analytics_e6_test | Fixtures mutables de integración, incluida E9 | sales_e6_etl / sales_e6_reader |

## Ejecutar el pipeline

```powershell
$connections = Get-Content .local/connections.json -Raw | ConvertFrom-Json
$env:SALES_DATA_DIR = (Resolve-Path Data).Path
$env:SALES_WRITER_DATABASE_URL = $connections.sales_analytics.writer
.\.venv\Scripts\python.exe -m sales_analytics.cli run
```

`run` crea el intento, extrae, valida y publica solo cuando corresponde. Streamlit nunca lo ejecuta. No repetir un intento de commit incierto sin reconciliar su estado. La [operación E6](docs/e6_execution.md) detalla recuperación manual, autorizaciones Tipo 1 y revaloración; estas últimas se probaron exclusivamente con fixtures sintéticas. La [primera carga](docs/e5_execution.md) conserva sus conciliaciones.

## Pruebas

Preparar las tres bases según la guía; las pruebas que escriben usan exclusivamente la tercera. No ejecutar suites de integración simultáneamente contra ella.

```powershell
$testConnections = Get-Content .local/e6-connections.json -Raw | ConvertFrom-Json
$env:SALES_TEST_WRITER_DATABASE_URL = $testConnections.sales_analytics_e6_test.writer
$env:SALES_TEST_READER_DATABASE_URL = $testConnections.sales_analytics_e6_test.reader
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pip check
```

La prueba integral requiere los seis originales del perfil y una base aislada inicialmente vacía; se detiene si encuentra evidencia previa y solo limpia sus fixtures. Para ejecutarla separadamente:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_end_to_end.py -q
```

La suite cubre codificación, conservación textual, calidad, integridad referencial, publicación, concurrencia determinista, recuperación manual, KPIs y lectura/UI. Los [resultados finales](docs/portfolio.md#verificación) distinguen pruebas ejecutadas de limitaciones.

Verificación final E9 (27/09/2026): **347 pruebas aprobadas** —157 unitarias, 180 de integración y 10 UI—, Ruff lint/formato y pip check sin errores. Instalación limpia y 51 versiones del lock comprobadas; cero conexiones residuales y publicaciones de aceptación intactas. Detalle, hashes y capturas en el [portafolio](docs/portfolio.md).

## Iniciar el dashboard

```powershell
$connections = Get-Content .local/connections.json -Raw | ConvertFrom-Json
$env:SALES_READER_DATABASE_URL = $connections.sales_analytics.reader
.\.venv\Scripts\python.exe -m streamlit run src/sales_analytics/dashboard.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false --theme.base light --theme.primaryColor '#0f766e'
```

Abrir `http://127.0.0.1:8501`. Las cuatro páginas son Resumen ejecutivo, Productos y sucursales, Clientes y Calidad y cargas. Los filtros se aplican conjuntamente. Las fechas se presentan como día/mes/año y la lectura muestra hora UTC; el timestamp conserva su tipo internamente. Si una actualización falla, se identifica el último resultado completo disponible.

## Rendimiento y límites

E8 midió ciclos de **12,65 s** y **17,02 s** sobre las publicaciones de aceptación. Límites: conexión 5 s, consulta 40 s, inactividad dentro de transacción 5 s y ciclo total 60 s. Son mediciones locales, no un compromiso de latencia. **No se realizó una prueba de carga multiusuario.**

Ingresos y margen son estimaciones basadas en catálogo; faltan precios efectivos por transacción, descuentos, impuestos, devoluciones y costos históricos. Los datos terminan el 20/02/2021; febrero no muestra MoM estándar. Huecos del histórico no prueban cierres comerciales. Las tasas se conservan como referencia sin asumir su orientación. Las dimensiones Tipo 1 no conservan versiones históricas consultables.

La recuperación es manual y el límite de transacción utiliza PostgreSQL 17. El entorno reproducido es Windows/Python 3.13.0; otras plataformas requieren verificación propia. El MVP no incluye cloud, CI/CD, APIs, ML ni despliegue productivo. Cualquier publicación en GitHub requiere autorización posterior; el dataset permanece local.

## Licencia

El código se distribuye bajo la [licencia MIT](LICENSE). Copyright (c) 2026 Omar Manrique Rodas. Esta licencia no se aplica al dataset externo.
