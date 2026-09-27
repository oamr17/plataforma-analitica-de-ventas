# E6: idempotencia, auditoría y recuperación

Registro histórico del 24/09/2026. Para el estado actual y la preparación completa, incluidas las vistas de marts, consultar el [README](../README.md). Los resultados y límites siguientes corresponden al cierre de E6.

Durante E6 no se realizaron
revaloraciones reales, cargas de originales ni escrituras en las bases de aceptación.

## Cambios implementados

- Publicación con exclusión transaccional común y comprobación del vigente,
  conflictos y autorizaciones después de obtener el bloqueo.
- Inserción/actualización por claves naturales: Tipo 1 conserva claves sustitutas;
  la revaloración autorizada modifica todas las líneas afectadas y su identidad de
  publicación en el mismo commit. Las filas sin cambios conservan su run_id.
- Un intento nuevo idéntico al vigente termina `sin_cambios`, sin nueva identidad.
  Un snapshot antiguo distinto del vigente se somete a C6; no equivale a repetición.
  Consultar un run terminal devuelve su resultado histórico y la identidad vigente.
- Registro confirmado de equipo, PID e inicio del proceso antes de validar/publicar.
  Se conserva la identidad anterior en la auditoría; staging permanece textual.
- Rollback íntegro ante fallos y diagnóstico separado. Si PostgreSQL no puede
  guardarlo, se informa la ruta local; diagnósticos sucesivos no se sobrescriben.
  Un diagnóstico tardío no altera estados terminales ni fases posteriores.
- Si un competidor ocupa el bloqueo antes del relevo, el intento queda disponible;
  si ocurre después del relevo confirmado, se registra fallo y el reintento necesita
  otro run_id. No se deja un intento propio abandonado por una colisión conocida.
- Respuesta del commit perdida: releer el run bajo exclusión, devolver el resultado
  confirmado si existe y mantener incertidumbre explícita en caso contrario.
  La reconciliación manual verifica proceso terminado y relee bajo el mismo bloqueo;
  modifica solo auditoría. Nunca crea publication_id ni reanuda etapas.

## Operación manual

Usar el entorno Windows/Python 3.13.0 y la configuración de E1–E2. Las credenciales
se suministran mediante `SALES_WRITER_DATABASE_URL`; no se escriben en comandos ni
se incorporan al repositorio. Ejecutar desde la raíz:

```powershell
.\.venv\Scripts\python.exe -m sales_analytics.cli reconcile <run_id>
.\.venv\Scripts\python.exe -m sales_analytics.cli reconcile <run_id> --diagnostic .local\publication-errors\<archivo>.json
```

Consultar primero la auditoría y conservar/aportar el diagnóstico local cuando
exista; la incertidumbre o un error guardado solo en disco no se infieren de la edad
del intento. Un proceso activo, equipo desconocido o bloqueo ocupado deja el caso
pendiente (salida 1). Un resultado terminal o reconciliado devuelve salida 0: significa
que la consulta/reconciliación terminó, no que el intento necesariamente publicó.
Un intento interrumpido/fallido requiere un nuevo `run`, nunca reutilizar su UUID.

`run --authorizations <archivo.json>` acepta un objeto JSON que relaciona cada
`cambio_id` SHA-256 de C6 con una referencia no vacía a su aprobación documentada.
Debe corresponder exactamente al cambio propuesto; se verifica de nuevo contra el
vigente bajo bloqueo. El archivo se guarda en `.local`, sin datos personales.
La referencia documenta una aprobación externa; no incorpora autenticación ni una
firma digital. E6 solo autoriza probar cambios con fixtures sintéticas.

## Aislamiento y reproducción de pruebas

Se creó `sales_analytics_e6_test` porque ambas bases anteriores conservan publicaciones
de aceptación. Tiene roles exclusivos `sales_e6_etl` y `sales_e6_reader`, sin permiso
para conectarse a `sales_analytics` ni a `sales_analytics_test`. Reutiliza el esquema
inicial E2; no añade tablas. PostgreSQL local 17.11 y Psycopg 3.3.5.

En un clúster local preparado, `scripts/setup_databases.py prepare --isolated-tests`
y `schema --isolated-tests` crean solamente esos objetos nuevos, usando la variable
administrativa de E2. Rechazan nombres/esquemas existentes; no son órdenes de reset.
El archivo privado resultante es `.local/e6-connections.json`.

```powershell
$e6 = Get-Content .local/e6-connections.json -Raw | ConvertFrom-Json
$env:SALES_TEST_WRITER_DATABASE_URL = $e6.sales_analytics_e6_test.writer
$env:SALES_TEST_READER_DATABASE_URL = $e6.sales_analytics_e6_test.reader
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m pip check
```

Ejecutar una sola suite de integración a la vez. Las fixtures exigen DW vacío en la
base sintética y eliminan únicamente sus intentos y datos al terminar; no vacían las
bases de aceptación ni borran su evidencia. Los tests rechazan otras bases/roles.

## Resultados ejecutados (2026-09-24)

| Comprobación | Resultado |
|---|---|
| E1–E6 | 157 unitarias y 132 de integración aprobadas; 289 en total, ninguna omitida |
| Repetición vigente | Mismas filas, claves sustitutas e importes; intento nuevo sin cambios y sin identidad nueva |
| Replay antiguo | Se bloquea el snapshot distinto; consultar un intento antiguo no lo republica |
| Concurrencia | Dos publicadores sincronizados con Events; una publicación, sin mezcla; colisión tras relevo auditada como fallo confirmado |
| Conflictos | Cambios no autorizados bloqueados; autorización preparada contra una versión anterior rechazada bajo bloqueo |
| Tipo 1 / revaloración | Claves conservadas; dos líneas sintéticas conciliadas en USD 39.00/19.50; identidad y valores visibles conjuntamente |
| Rollback | Fallos tras dimensiones, tasas y hechos conservan la publicación previa; C5 fuera de la transacción revertida |
| Auditoría no disponible | Diagnóstico local declarado como tal, sin afirmar persistencia en ops; conservación de diagnósticos sucesivos |
| Commit incierto | Respuesta perdida después de confirmar resuelta por relectura; rollback previo sin identidad nueva y con incertidumbre documentada |
| Reconciliación | Proceso activo, inicio distinto/PID reutilizado, equipo desconocido y bloqueo ocupado; fallo documentado frente a interrupción; repetición inocua |
| Interrupciones | Trabajadores reales terminados durante extracción, validación y publicación; rollback y propietario correcto comprobados |
| CLI real | Dos invocaciones de reconciliación en procesos independientes; una sola incidencia y ninguna publicación |
| Ruff | Lint y format check aprobados; 46 archivos conformes |
| Dependencias | pip check aprobado; las 17 versiones instaladas coinciden con requirements.lock; módulos instalados idénticos a las fuentes |

La primera comprobación de esta sesión encontró PostgreSQL detenido tras un cierre
inesperado. Se inició el clúster existente, sin reinicializarlo. Una ejecución durante
la recuperación obtuvo 157 unitarias aprobadas y 132 errores de conexión al preparar
las fixtures; tras comprobar disponibilidad real se repitió toda la integración:
132 aprobadas en 223.45 s. No se ocultaron ni contabilizaron esos errores como éxitos.

La revisión independiente detectó dos intercalados de concurrencia; ambos se
reprodujeron como fallos y quedaron cubiertos por regresiones aprobadas. No se
instalaron dependencias nuevas ni se regeneró el lock. Se reutilizó la instalación
existente; no se declara una instalación limpia nueva.

## Estado final y conservación

| Base | run_id publicado | Estado / publication_id |
|---|---|---|
| sales_analytics | cc2ead44-5c5d-4d35-a6c2-39c8b44cc5a3 | publicado / 1 |
| sales_analytics_test | c72eb197-9913-4015-97be-761003d5064d | publicado / 1 |

El intento de aceptación `80a0553f-5555-40e6-9913-9e6961d4cfe4` conserva
`sin_cambios` y publication_id nulo en sales_analytics_test.

En cada base: **62884 líneas, 26326 pedidos, 197757 unidades, ingresos estimados
USD 55755479.59 y costos estimados USD 23092791.21**. La comparación de conteos y
SHA-256 de todas las filas DW, staging y ops confirmó igualdad exacta con el baseline
previo a E6. Ninguna publicación nueva ni revaloración en esas bases.

Los seis CSV y architecture.md, data_model.md, business_rules.md, data_profile.md y
data_dictionary.md conservan sus hashes. Evidencia local ignorada:
`.verification/e6-baseline.json`, `e6-preservation-result.json`,
`e6-final-tests.xml` (incluye el intento con errores de conexión),
`e6-final-integration.xml` (repetición aprobada).

La base sintética terminó sin filas en staging, DW ni ops y sin publicaciones;
las fixtures retiraron solo sus propios datos. No existen vistas de marts.
Comprobación: `.verification/e6-isolation-final.json`.

## Inventario de archivos

Nuevos: `src/sales_analytics/recovery.py`; `tests/integration/test_idempotency.py`,
`test_publication.py`, `test_recovery.py`; `tests/unit/test_recovery.py`,
`test_setup.py`; este documento.

Modificados: `src/sales_analytics/load.py`, `audit.py`, `db.py`, `cli.py`,
`validation_run.py`; `scripts/setup_databases.py`; `tests/integration/conftest.py`,
`test_connections.py`, `test_load.py`, `test_validation_run.py`;
`tests/unit/test_audit.py`, `test_cli.py`; README y plan incremental.

Sin nuevas dependencias, clases de servicio ni tablas. Se reutilizan los conversores,
las reglas E4 y los controles E5. `recovery.py` separa el protocolo manual de la
persistencia de auditoría; no ejecuta recuperación en segundo plano.

## Límites

Identidad de procesos comprobada en Windows local. Los equipos ajenos se mantienen
pendientes; no se implementó inspección remota. El caso PID reutilizado se prueba
con discrepancia de inicio, sin forzar al sistema operativo a reciclar un PID.
Las respuestas perdidas y caídas de auditoría se inyectan en fronteras transaccionales;
las interrupciones de trabajadores se prueban además terminando procesos reales.
No se ensaya avería física de disco ni caída deliberada del servidor compartido.
Si fallan base y disco, se informa que no se pudo persistir el diagnóstico.

Las autorizaciones no habilitan borrados implícitos de claves anteriores.
No hay limpieza automática de evidencia, servicio de recuperación, KPIs, vistas de
marts, analytics.py ni Streamlit. No se ha publicado contenido ni usado servicios remotos.
