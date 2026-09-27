# Arquitectura — Sales Analytics Platform

**Estado: diseño documental aprobado. No autoriza implementación.**

Este documento define responsabilidades y operación. [data_model.md](data_model.md) es la autoridad sobre tablas, claves e identidad de publicación; [business_rules.md](business_rules.md) define KPIs y calidad; [implementation_plan.md](implementation_plan.md) establece las entregas futuras. El perfil y el diccionario permanecen como evidencia del snapshot inicial.

## Alcance definitivo del MVP

Flujo: **CSV originales → extracción y staging textual → validación semántica → transformación → publicación PostgreSQL → vistas analíticas → Streamlit**.

| Componente | Incluido |
|---|---|
| Extracción | Los cinco CSV de negocio y verificación del diccionario; Customers en Windows-1252; hashes de los seis archivos y run_id |
| Validación | Estructura, tipos, fechas, claves, relaciones, consistencia por pedido e incidencias |
| Transformación | Normalización, importes de catálogo a decimales USD y resolución de claves; sin conversión cambiaria |
| PostgreSQL | staging, dw, marts y ops; cuatro dimensiones, hecho por línea, tasas diarias, vistas y auditoría del modelo aprobado |
| Indicadores | K1–K10 de business_rules.md, con sus filtros, límites y ejemplos |
| Streamlit | Resumen ejecutivo; productos y sucursales; clientes; calidad y estado de cargas |
| Verificación futura | Pruebas unitarias, integración, idempotencia, atomicidad, KPIs y lectura concurrente consistente |

Fuera del MVP: API simulada, Excel artificial, Power BI, detección estadística de anomalías, ML, ejecución programada, Airflow, Kafka, Spark, Kubernetes, autenticación/gestión de usuarios y despliegue cloud. El ETL se ejecutará manualmente y separado de la aplicación.

Docker Compose se contempla únicamente como opción local mínima: PostgreSQL y, cuando sean necesarios, ETL y Streamlit. No es requisito del modelo ni motivo para agregar servicios.

## Responsabilidades e interfaces conceptuales

No se prescriben clases, herencia ni repositorios genéricos; funciones y módulos cohesionados son suficientes.

| Responsabilidad | Recibe | Produce / límite |
|---|---|---|
| Extracción | Rutas y contrato de archivos | Hashes y registros textuales identificables persistidos en staging por run_id; nunca sobrescribe Data |
| Validación | Lote completo en staging, diccionario y reglas | Aceptados, rechazados y advertencias con evidencia; sin publicación parcial |
| Transformación | Registros interpretables y válidos | Valores tipados y relaciones preparadas; usa Decimal para catálogo |
| Persistencia/publicación | Candidato validado, run_id y autorizaciones concretas | Cambios atómicos o ninguno; una identidad solo si se confirma publicación efectiva |
| Auditoría | Intento, conteos, incidencias y resultado confirmado | Trazabilidad que sobrevive al rollback analítico |
| Lectura analítica | Vista y filtros solicitados | Un conjunto completo de resultados bajo una sola instantánea |
| Presentación | Conjunto completo ya leído | Formato y navegación; no ETL, escrituras ni fórmulas de negocio duplicadas |

Validar y transformar pueden compartir conversores puros de tipos; no mantener dos interpretaciones independientes de fechas o dinero. Los archivos permanecen intactos; cualquier copia de archivo conservará bytes y vínculo al hash. El hash debe identificar los mismos bytes que se procesan: detectar cambios del archivo durante la lectura y fallar ante inconsistencia.

Se separan las credenciales de escritura del ETL y de lectura del dashboard. El lector accede a las vistas y metadatos autorizados; nunca necesita privilegios de carga.

## Staging y evidencia de origen

Staging se escribe **durante la extracción (E3), antes de la validación semántica (E4)**. Primero se confirma el inicio del run_id. Tras comprobar codificación, encabezado y estructura de cada registro CSV, se conservan sus campos como texto, sin normalizar, convertir tipos ni descartar duplicados. El [modelo](data_model.md#staging-textual) fija representación y claves.

Cada archivo de negocio se persiste en una transacción propia, separada de la publicación analítica; su confirmación registra también en ops.ejecuciones que ese archivo está completo y su conteo/hash. E4 comienza únicamente con los cinco archivos completos y el diccionario verificado. Si falla la lectura o persistencia de un archivo, se revierte la transacción de ese archivo; los archivos ya confirmados permanecen como evidencia y el lote no puede publicarse.

Un valor semánticamente inválido, por ejemplo una cantidad no numérica, permanece en staging y su incidencia enlaza run_id, archivo y ordinal del registro. Si la estructura impide representar el registro (encabezado incorrecto, campos incompatibles o CSV ilegible), se registra la incidencia de extracción con ruta/hash y posición disponible; el original es la evidencia, sin inventar campos ni saltar el defecto. Una posición desconocida se deja sin informar.

Las conversiones y el candidato validado se preparan fuera de staging: no se reemplaza el texto de origen por valores corregidos. Staging e incidencias confirmados sobreviven al rollback analítico y no alimentan KPIs. Cada reintento usa otro run_id; no sobrescribe la evidencia anterior. El MVP no realiza limpieza automática de esta evidencia.

## Publicación atómica y diagnósticos

1. Crear el intento run_id y confirmar su inicio e identidad del proceso. Extraer a staging según el contrato anterior; validar el lote completo y preparar el candidato fuera de las tablas publicadas.
2. Si hay errores críticos o rechazos, registrar el fallo sin modificar el DW ni crear identidad de publicación.
3. Adquirir exclusión entre publicadores mediante un bloqueo transaccional común. Bajo ella, leer el estado vigente y volver a comprobar idempotencia, conflictos y autorizaciones; no basar la decisión en un estado consultado antes del bloqueo.
4. Si el lote es idéntico al vigente y no cambia datos ni definiciones analíticas, registrar «sin cambios», sin publication_id nuevo.
5. Si hay cambios autorizados, aplicar dimensiones, tasas y hechos en una transacción. Conciliar los resultados y registrar la identidad candidata de publicación en la misma transacción.
6. Confirmar juntos datos, identidad y estado publicado. Antes del commit no se expone la identidad candidata. No anunciar éxito anticipadamente.
7. Ante fallo confirmado, revertir toda la transacción analítica y registrar el diagnóstico en otra transacción; el DW y su publicación vigente permanecen como antes.

La semántica y unicidad de publication_id se definen en [data_model.md](data_model.md#identidad-de-ejecución-y-publicación). Solo se utiliza ops.ejecuciones; no hay tabla nueva de versiones, copias históricas de hechos ni puntero separado que se confirme después de los datos.

Las escrituras operan sobre tablas estables mediante cambios transaccionales de filas. No utilizar TRUNCATE, recreación de tablas o cambios de esquema durante una publicación concurrente: ciertas operaciones DDL no respetan la visibilidad esperada de instantáneas antiguas. Las migraciones futuras se ejecutarán fuera del ciclo de publicación, con la aplicación detenida. [Limitaciones MVCC de PostgreSQL](https://www.postgresql.org/docs/current/mvcc-caveats.html).

**Fallo de infraestructura:** si PostgreSQL no permite guardar un diagnóstico, conservar run_id y evidencia mínima sin secretos en el registro local persistente del proceso. No declarar que ops lo recibió. La reconciliación será explícita cuando se restablezca acceso; no se introduce un servicio de reintentos.

### Ejecuciones interrumpidas y reconciliación manual

| Situación | Interpretación |
|---|---|
| Fallo confirmado | Hay error observado y certeza de que no se publicó; registrar fallido con su diagnóstico |
| En curso sin avance | Señal de revisión, no prueba de fallo: puede haber trabajo legítimo o un proceso interrumpido |
| Commit incierto | Se perdió la confirmación; puede haberse publicado. Informar «resultado no confirmado», sin inferir rollback ni repetir escrituras |

La revisión se solicita manualmente y consulta los intentos en curso, su fase y último avance. ops.ejecuciones conserva equipo, PID e inicio del proceso para distinguir reutilización de PID; fase/último avance se actualizan al cambiar de etapa, no mediante un heartbeat. La antigüedad o ausencia de una conexión PostgreSQL no demuestran que el proceso terminó.

Protocolo único de reconciliación:

1. Comprobar en el equipo de origen que el proceso identificado terminó y no puede reanudar ese run_id. Si sigue vivo, el equipo no es accesible o falta evidencia, mantener el resultado pendiente; no terminar procesos automáticamente.
2. Con acceso restablecido a PostgreSQL, adquirir el mismo bloqueo transaccional del publicador, con espera limitada. Si no se obtiene, no reclasificar. El bloqueo permite esperar la resolución de una publicación todavía activa.
3. **Después de obtener el bloqueo**, releer el run_id desde una instantánea nueva. Si figura publicado con publication_id, conservar el éxito y esa identidad, aunque exista una publicación posterior. Si ya tiene otro estado terminal, respetarlo.
4. Si sigue en curso, no tiene publication_id y se confirmó que su proceso terminó, registrar **interrumpido** cuando no existe un fallo observado; registrar **fallido** si el error está documentado y ahora se confirmó la ausencia de publicación. Guardar motivo, evidencia y fecha de reconciliación en ops.incidencias. Ante evidencia contradictoria, mantener pendiente e investigar.
5. Confirmar solo la auditoría. No modificar datos analíticos, generar publication_id, reanudar automáticamente etapas ni reutilizar run_id. Una nueva ejecución requiere una acción manual y conserva la evidencia previa.

Hasta resolver un commit incierto, el intento puede permanecer en curso; la incertidumbre se registra en incidencias o, si la base no está disponible, en el registro local persistente. No es un fallo confirmado. El protocolo reutiliza ops.ejecuciones, ops.incidencias y el bloqueo existente; no añade tablas ni servicios de recuperación.

## Lectura consistente del dashboard

### Unidad de consistencia

Una actualización lógica es un ciclo de lectura para una vista y sus filtros efectivos. Incluye opciones y límites de filtros, KPIs, gráficos, advertencias y metadatos de publicación/estado de cargas que se muestran juntos. Un cambio de página, aplicación de filtros o actualización posterior puede iniciar un ciclo nuevo.

La atomicidad del escritor evita datos parcialmente publicados. La transacción del lector evita mezclar dos publicaciones confirmadas en consultas sucesivas. PostgreSQL REPEATABLE READ mantiene la instantánea fijada por la primera consulta de datos para las consultas posteriores de esa transacción. [Aislamiento de transacciones](https://www.postgresql.org/docs/current/transaction-iso.html).

### Ciclo obligatorio

1. Capturar la vista y selección solicitada desde el estado de esa sesión, sin abrir aún una transacción para esperar al usuario.
2. Abrir una conexión dedicada a este ciclo, iniciar una transacción de solo lectura REPEATABLE READ y establecer sus límites antes de consultar datos.
3. Como primera consulta de datos, leer la última publicación efectiva confirmada de ops.ejecuciones. Esta consulta fija la instantánea. Si no hay publicación, terminar y mostrar «Sin publicación disponible».
4. En esa misma conexión y transacción, consultar opciones de filtros y validar la selección. Si una opción dejó de existir, terminar sin calcular KPIs incompatibles y pedir una selección válida; no ampliar silenciosamente el alcance. En la primera visita, los valores iniciales se determinan con ese catálogo visible.
5. Ejecutar secuencialmente todas las consultas de KPIs, gráficos y metadatos necesarios. Toda función lectora recibe la conexión del ciclo; no abre otra ni confirma transacciones individuales.
6. Materializar resultados completos en memoria; no dejar cursores, generadores o consultas diferidas para después del cierre.
7. Finalizar satisfactoriamente la transacción, cerrar cursores y conexión y, solo después, reemplazar el conjunto mostrado por el conjunto completo.
8. Ante error, cancelación o tiempo agotado, revertir si la conexión lo permite, cerrar recursos en una salida garantizada y descartar todo resultado parcial. Un reintento, si se solicita, empieza desde el paso 1.

No se renderizan KPIs a medida que llegan consultas. No se mantiene la transacción abierta para dibujar gráficos, interactuar con widgets o navegar. No se utilizan actualizaciones independientes de fragmentos que combinen datos anteriores con nuevos.

Si se conserva el último resultado completo tras un fallo, debe mostrarse con su publicación, vista, filtros anteriores y aviso de error. No etiquetarlo con los filtros de la solicitud fallida. Si no existe conjunto anterior, mostrar error sin métricas parciales.

### Identidad del conjunto de resultados

El resultado lleva publication_id, versión de reglas, vista, filtros efectivos y momento de lectura, obtenidos/asociados dentro del mismo ciclo. publication_id se muestra en el estado de actualización y acompaña al conjunto completo; no se obtiene con una consulta externa previa o posterior.

La publicación efectiva y el último intento ETL son conceptos distintos: puede mostrarse una publicación A válida junto a un intento posterior fallido, ambos observados en la misma instantánea. Los diagnósticos de otros intentos no convierten esa lectura en la publicación B.

No filtrar los hechos por su run_id para reconstruir la publicación. Las filas vigentes pueden haberse insertado en ejecuciones diferentes.

### Concurrencia, sesiones y recursos

Si B se confirma después de que el lector fijó A, todos los resultados de ese ciclo continúan reflejando A. Una nueva lectura después de B puede mostrar B. No se promete que A sea la versión más reciente al terminar de renderizar, sino que el conjunto es consistente.

Cada actualización abre y cierra su propia conexión. No compartir conexiones, transacciones ni cursores entre usuarios, sesiones o ciclos. No guardarlos en Session State ni en recursos globales; el MVP no necesita un pool compartido.

Aplicar límites de conexión, consulta e inactividad y un plazo total al ciclo. Los valores se medirán y fijarán en E8 antes de considerar aceptada la lectura; deben provocar cancelación/limpieza, no resultados parciales. El cierre ocurre también en interrupciones del ciclo de Streamlit. No acumular transacciones «idle in transaction».

### Caché

**No almacenar resultados SQL en caché durante el MVP**, ni por consulta ni como conjunto. Tampoco usar conectores con caché implícita que eludan la conexión y transacción del ciclo.

Un último resultado completo conservado en el estado privado de sesión sirve solo para mostrar el fallo de actualización; no se usa como caché para resolver una solicitud nueva. Los parámetros del usuario pueden conservarse en la sesión; los recursos de base de datos, no.

Streamlit permite compartir recursos cacheados entre sesiones; una conexión transaccional compartida no satisface este contrato. No se requiere caché de conexiones ni de resultados para esta primera versión. [Caché de Streamlit](https://docs.streamlit.io/develop/concepts/architecture/caching).

## Alternativas consideradas

| Alternativa | Ventaja | Costo / decisión |
|---|---|---|
| Transacción breve de solo lectura REPEATABLE READ | Instantánea común para múltiples consultas, sin copiar versiones del DW | Elegida; requiere cerrar pronto y no cachear consultas independientemente |
| Versionado explícito de hechos, dimensiones y referencias | Consultar una publicación histórica después de cerrar la lectura | Fuera del MVP: agrega almacenamiento, propagación de versiones y retención que no se requieren |
| Identificar la publicación antes/después con consultas independientes | Parece simple | Descartada: identificar A no fija la visibilidad de los datos consultados después |

El ordinal de auditoría no es un mecanismo de versionado explícito de todas las filas.

## Verificación prevista

Estas pruebas están **planificadas, no implementadas ni ejecutadas**. Se utilizarán conexiones reales independientes y sincronización determinista entre fases, no esperas temporales arbitrarias.

| Caso | Evidencia exigida |
|---|---|
| Publicación concurrente | Lector fija A y consulta; escritor confirma B con importes, conteos y catálogo distintos; el mismo lector termina con todos los valores y opciones de A; un lector nuevo obtiene B |
| Aislamiento insuficiente como control | El mismo intercalado bajo READ COMMITTED exhibe A/B con la fixture, para comprobar que la prueba detecta el defecto |
| Sesiones simultáneas | Sesión 1 permanece en A mientras sesión 2 abre B; cerrar/cancelar una no afecta a la otra |
| Consulta intermedia fallida | Ningún resultado parcial se renderiza o conserva como conjunto válido; no quedan conexiones/transacciones activas del ciclo |
| Idempotencia | Repetir el lote vigente no crea publication_id ni cambia identidades o resultados |
| Error de publicación | Fallo después de cambios internos: no aparece identidad nueva, datos anteriores intactos y diagnóstico persistente fuera del rollback |
| Filtro inválido / sin publicación | Estado explícito, sin ensanchar selección ni mostrar métricas incompatibles |
| Revaloración autorizada | Cambian juntos precios/costos de referencia afectados, métricas e identidad; lectores A conservan A |
| Staging inválido | Texto semánticamente inválido conservado y vinculado a incidencias; archivo ilegible diagnosticado sin publicación ni pérdida de evidencia ya confirmada |
| Ejecución interrumpida | Un proceso terminado se reconcilia manualmente; un proceso vivo, PID reutilizado o equipo inaccesible no se reclasifica solo por antigüedad |
| Confirmación incierta | Pérdida de respuesta antes/después del commit: releer run_id bajo exclusión, conservar publicación confirmada y evitar doble publicación |

## Riesgos y asuntos pendientes

- Sin dirección documentada de Exchange, la conversión local sigue deshabilitada; no bloquea los KPIs USD.
- Huecos y saltos de línea limitan interpretación comercial; no se corrigen inventando registros.
- Transacciones lectoras largas retienen versiones antiguas y consumen conexiones. Medir duración y fijar límites en E8; no introducir caché o versionado para ocultar el problema.
- Disponibilidad de PostgreSQL y del disco condiciona persistencia de diagnósticos; informar el medio realmente utilizado.
- E1 prepara Python, dependencias y configuración sin conectarse a PostgreSQL; E2 verifica conexiones reales, credenciales y permisos y decide instalación local/Compose. El [contrato de dependencias](implementation_plan.md#declaración-y-bloqueo-de-dependencias) tiene una única fuente.
- La reconciliación necesita evidencia del proceso y acceso a PostgreSQL; sin ellos el resultado permanece pendiente, no se fuerza un estado terminal.
- No hay autorización para ejecutar el plan. Todas las pruebas y garantías descritas son contratos por demostrar, no resultados de una aplicación existente.
