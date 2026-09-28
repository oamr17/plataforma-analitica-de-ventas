# Plan de implementación incremental — Plataforma Analítica de Ventas

**Estado: E1–E9 implementadas y verificadas dentro del alcance local autorizado.** El [README](../README.md) reúne la operación actual y el [portafolio](portfolio.md) registra la evidencia final. Las listas de aceptación que siguen conservan el plan aprobado; los resultados se identifican expresamente.

Objetivo: construir el MVP CSV → PostgreSQL → Streamlit con resultados conciliados, publicación atómica y lectura consistente.

Documentos rectores: [modelo](data_model.md), [reglas](business_rules.md) y [arquitectura](architecture.md). Este plan describe responsabilidades, dependencias y criterios de aceptación de las nueve entregas autorizadas.

## Condiciones generales

- Implementar una entrega cada vez, presentando alcance y criterios; continuar solo después de verificarla y resolver errores relacionados.
- Para cada comportamiento importante, definir primero la prueba, observar el fallo pertinente y desarrollar la solución mínima; distinguir pruebas previstas de resultados ejecutados.
- Usar funciones simples, módulos con responsabilidad concreta y composición; ninguna clase/interfaz por anticipación.
- Mantener intactos Data, data_profile.md y data_dictionary.md. Las fixtures de prueba serán pequeñas y separadas; nunca se alteran los CSV originales para simular defectos.
- Centralizar fórmulas en la capa SQL y usar Decimal para interpretar dinero; no duplicarlas en Streamlit.
- No agregar dependencias sin justificar propósito y compatibilidad. Propuesta mínima: biblioteca estándar para CSV, fechas, hashes, decimales y logs; Psycopg para PostgreSQL; Streamlit para presentación; Pytest y Ruff para desarrollo; pip-tools únicamente para generar el bloqueo reproducible descrito abajo. Evaluar versiones en el entorno antes de fijarlas.
- Mantener un único proyecto Python. No crear API, scheduler, detector estadístico, framework de repositorios ni servicios adicionales.
- Todas las pruebas de integración utilizan una base aislada de pruebas; no se borra una base con datos del usuario.
- No hay caché SQL ni conexiones compartidas en el MVP. Las comparaciones SQL/UI se harán sobre la misma publicación y filtros efectivos.

## Dependencias y orden

**E1 → E2 → E3 → E4 → E5 → E6 → E7 → E8 → E9.**

La estructura de auditoría y staging aparece en E2; E3 persiste el origen textual por archivo y E4 valida su semántica, conservando incidencias vinculadas al intento. La transacción y la identidad de publicación existen desde E5: E6 completa la prueba de idempotencia, conflictos y fallos; no agrega atomicidad a una carga previamente insegura.

Los límites y pruebas del lector se preparan al diseñar consultas en E7 y se verifican con Streamlit en E8. E9 comprueba el recorrido completo; no sustituye las pruebas de cada entrega.

Las rutas futuras son relativas a la raíz del repositorio. Los archivos existentes solo se amplían cuando les corresponde una responsabilidad. Cada entrega actualiza el lock de dependencias únicamente si incorpora o cambia alguna.

## Declaración y bloqueo de dependencias

**pyproject.toml es la única fuente editable de declaración de dependencias**: ejecución, grupo opcional de desarrollo y requisitos de construcción. requirements.lock es un artefacto generado, con versiones exactas directas/transitivas y hashes; no se edita manualmente ni se mantiene otra lista paralela.

Se elige pip-compile, de pip-tools, porque genera ese bloqueo desde pyproject.toml y permite actualizaciones controladas; evita mantener a mano el cierre transitivo o crear un resolvedor propio. Es una herramienta de desarrollo, no otro gestor del proyecto. [Documentación oficial de pip-tools](https://pip-tools.readthedocs.io/en/latest/).

Procedimiento previsto para E1 y actualizaciones posteriores:

1. Fijar Python y plataforma objetivo en README; declarar versiones exactas del generador pip-tools y de pip en el grupo de desarrollo de pyproject.toml. Preparar un entorno virtual limpio con esas versiones para generar el bloqueo.
2. Compilar desde pyproject.toml incluyendo desarrollo y todos los requisitos de construcción, también pip y las herramientas que el compilador pueda excluir por defecto, con hashes, extras eliminados del archivo de salida y destino requirements.lock. Registrar en README la invocación exacta verificada y el entorno; no copiar allí otra declaración de paquetes.
3. Para regenerar, conservar el lock vigente y sus fijaciones compatibles. Para actualizar, cambiar la declaración cuando proceda y solicitar la actualización deliberada de los paquetes afectados; revisar las diferencias y confirmar ambos archivos juntos. No regenerar borrando el lock ni editarlo a mano.
4. Verificar en otro entorno limpio la instalación desde el lock con hashes obligatorios, la coherencia de dependencias y la instalación del proyecto local sin resolver dependencias adicionales ni crear un entorno de construcción aislado: sus requisitos ya deben estar bloqueados e instalados.

La reproducción usa el lock confirmado, no una nueva resolución en cada instalación. El bloqueo se valida para la versión de Python y plataforma documentadas; cambiar de plataforma exige revisar y verificar su compatibilidad. Se incorporan bibliotecas únicamente en la entrega que las utiliza: Psycopg en E2 y Streamlit en E8. E1 ya generó y verificó el bloqueo en dos entornos virtuales; sus comandos y resultados están en README.md.

## E1. Estructura mínima y entorno reproducible

- **Objetivo:** preparar el entorno local reproducible y validar la configuración sin conexión a PostgreSQL ni lógica de ventas.
- **Archivos previstos:** pyproject.toml, requirements.lock, .gitignore, .env.example, README.md; src/sales_analytics/config.py; tests/unit/test_config.py.
- **Responsabilidades:** aplicar el contrato de dependencias; validar presencia, formato y rangos de configuración y existencia de rutas locales. La configuración de PostgreSQL solo se comprueba sintácticamente, sin abrir conexiones, resolver hosts ni autenticar. Las variables se leen del entorno; .env.example documenta nombres y ejemplos sin secretos.
- **Dependencias:** autorización expresa para E1 y revisión de Python/herramientas disponibles. Seleccionar versiones compatibles con evidencia y generar el lock según el contrato; no requiere PostgreSQL disponible ni credenciales válidas.
- **Aceptación:** entorno restaurable desde el lock; configuración ausente o mal formada da un error claro; secretos no aparecen en salidas; originales intactos. E1 puede aprobarse con PostgreSQL apagado: no acredita conectividad, credenciales ni permisos.
- **Pruebas previstas:** configuración mínima, parámetro ausente, ruta inexistente, formato de conexión inválido y redacción de secretos, sin acceso a PostgreSQL; restauración limpia desde lock, coherencia de dependencias, formato y lint.
- **Condición para continuar:** entorno reproducible demostrado y validaciones locales aprobadas; la disponibilidad y verificación real de PostgreSQL corresponden a E2.

La elección entre PostgreSQL local y Compose mínimo corresponde a E2. Contenedores ETL/Streamlit se añadirán solo cuando existan esas aplicaciones.

## E2. PostgreSQL, claves y restricciones

- **Objetivo:** comprobar conexiones reales, credenciales y permisos de PostgreSQL y disponer del esquema vacío correcto.
- **Archivos previstos:** sql/001_schema.sql, src/sales_analytics/db.py, tests/integration/test_schema.py; ampliar README.md y configuración. compose.yaml solo si E2 justifica esa opción.
- **Responsabilidades:** elegir PostgreSQL local o Compose mínimo y validar acceso real de escritor/lector; crear staging, dw, marts y ops con las restricciones propias de cada capa. Staging admite texto inválido y claves de negocio repetidas. Auditoría incorpora run_id/publication_id, estados, identidad del proceso y progreso definidos en el modelo.
- **Dependencias:** E1; disponer en E2 de PostgreSQL y una base aislada de pruebas. Incorporar Psycopg a pyproject.toml y regenerar el lock.
- **Aceptación:** existen solo los objetos justificados; StoreKey=0 es válido; claves sustitutas/naturales respetan contratos; publication_id único si no nulo y asociado a estado publicado; múltiples intentos no publicados admiten publication_id nulo.
- **Pruebas previstas:** conexión y autenticación reales; credenciales incorrectas, servidor inaccesible y permisos de escritor/lector; restricciones del DW y nulos permitidos; texto inválido/duplicados de negocio admitidos en staging; identidad de publicación y cierre de conexiones. Fixtures solo en la base de pruebas.
- **Condición para continuar:** pruebas de estructura y permisos aprobadas; no se carga aún el snapshot de ventas como publicación.

El archivo de esquema es una creación inicial versionada; no se introduce un framework de migraciones sin necesidad.

**Resultado de E2:** PostgreSQL local aislado 17.11 y Psycopg 3.3.5. Se añadió scripts/setup_databases.py para reproducir bases/roles/permisos, tests/unit/test_database_config.py y tests/integration/conftest.py/test_connections.py junto con test_schema.py. Al cerrar E2, las bases del proyecto y de pruebas estaban vacías. Se verificaron 40 pruebas unitarias, 59 de integración, Ruff, pip check y restauración desde lock; detalle en README. No se creó Compose porque no había Docker disponible.

## E3. Extracción y conservación del origen

- **Objetivo:** identificar, leer y conservar en staging textual el snapshot antes de validarlo semánticamente.
- **Archivos previstos:** src/sales_analytics/extract.py, src/sales_analytics/audit.py, tests/unit/test_extract.py, tests/integration/test_audit.py, tests/integration/test_staging.py; ampliar config.py.
- **Responsabilidades:** confirmar inicio de run_id e identidad del proceso; leer cinco tablas y verificar diccionario, codificación, encabezados y estructura CSV; Customers con Windows-1252 y los demás con UTF-8. Persistir texto por archivo con ordinal lógico, hash y estado de lectura en transacciones separadas del DW, según el [ciclo de staging](architecture.md#staging-y-evidencia-de-origen).
- **Dependencias:** E2 para auditoría persistente, contratos del diccionario. Biblioteca estándar para lectura y hashes.
- **Aceptación:** seis archivos identificados y conteos conciliados; cinco archivos de negocio completos en staging antes de E4; texto inválido, códigos y vacíos preservados sin normalizar. Error estructural conserva evidencia en origen/incidencias y bloquea el lote; no se altera Data ni el DW.
- **Pruebas previstas:** acentos, comillas y registros multilínea, NA, 01/0101, vacíos/espacios y cantidad no numérica conservados; archivo ausente o estructuralmente ilegible y bytes modificados; rollback del archivo incompleto sin perder otros archivos confirmados; incidencias localizables por run_id, archivo y ordinal cuando exista.
- **Condición para continuar:** lectura íntegra conciliada con el perfil y diagnósticos identificables por run_id.

**Resultado de E3:** extract.py y audit.py reutilizan config.py/db.py y los objetos existentes, sin dependencias adicionales. Se añadieron tests/unit/test_extract.py, tests/unit/test_audit.py, tests/integration/test_staging.py y fixtures sintéticas en tests/conftest.py. La auditoría transaccional se prueba junto al staging, sin módulo de pruebas redundante. E1–E3: 60 unitarias y 73 de integración aprobadas; Ruff y dependencias verificados. Se conservaron 91949 registros textuales en sales_analytics_test, comparados campo por campo con los originales; diccionario de 37 definiciones verificado. Hashes y conteos coinciden con el perfil. No hay publicación ni escrituras DW/marts. Operación, límites, run_id y evidencia en README. Al cerrar E3, el intento quedó en fase extraccion_completa, sin estado terminal del pipeline; su continuidad autorizada se documenta en el resultado E4.

## E4. Validaciones y clasificación de incidencias

- **Objetivo:** decidir si el candidato puede publicarse antes de modificar el DW.
- **Archivos previstos:** src/sales_analytics/validation.py, tests/unit/test_validation.py; ampliar audit.py y tests/integration/test_audit.py.
- **Responsabilidades:** aplicar C1–C8, R1–R7 y W1–W8 al lote completo de staging; validar tipos mediante conversores reutilizables, claves, relaciones y encabezado por pedido. Vincular rechazos/advertencias a su origen sin corregir ni borrar el texto; distinguir rechazo de línea de su impacto dependiente.
- **Dependencias:** E3 terminado con los cinco archivos completos y diccionario verificado; tablas de auditoría de E2. Los conversores numéricos/fechas se compartirán con transformación; no duplicar reglas.
- **Aceptación:** cero errores críticos/rechazos en el snapshot bajo el contrato aprobado; advertencias conocidas conservadas; originales intactos; un lote inválido registra diagnóstico sin alterar datos publicados.
- **Pruebas previstas:** duplicados exactos/conflictivos, referencias inexistentes, fechas contradictorias, cantidades no válidas, tasa ausente, catálogo incoherente y campos semánticamente inválidos; NA y nulos legítimos aceptados; saltos de línea y valores atípicos válidos no eliminados. Los defectos estructurales de CSV se prueban en E3.
- **Condición para continuar:** cada regla crítica/de rechazo tiene un caso verificable y la conciliación entradas=aceptadas+rechazadas evita doble conteo.

No se crea un detector comercial ni se adoptan máximos observados como límites de validez.

**Resultado de E4:** se añadieron converters.py, validation.py, validation_checks.py, validation_changes.py y validation_run.py; pruebas unitarias de conversores/reglas y tests/integration/test_validation_run.py. Se ampliaron audit.py y las fixtures comunes. La integración de auditoría reside junto a la validación, sin módulo redundante. Sin nuevas dependencias ni cambios de esquema. El mismo intento E3 quedó en en_curso/validacion_completa: 91949 aceptados, cero rechazos/críticos y 55512 advertencias trazables. Originales, staging y DW/marts permanecen íntegros; publication_id nulo. Se ejecutaron 228 pruebas (141 unitarias, 87 de integración), Ruff y verificaciones de dependencias. Operación, advertencias, concurrencia y límites en README. C4 valida conservación interna; la conciliación contra el DW y la comprobación de conflictos bajo exclusión de publicación pertenecen a E5. No se declara publicada ni lista sin esos controles una versión analítica.

## E5. Transformación y primera carga transaccional

- **Objetivo:** obtener la primera publicación válida usando el modelo aprobado.
- **Archivos previstos:** src/sales_analytics/transform.py, src/sales_analytics/load.py, src/sales_analytics/cli.py, tests/unit/test_transform.py, tests/integration/test_load.py; ampliar db.py y audit.py.
- **Responsabilidades:** transformar en memoria el lote validado, sin sobrescribir staging; resolver claves, fijar precio/costo de referencia, cargar calendario/dimensiones/tasas/hechos; comando manual del pipeline; tomar exclusión de publicación; confirmar juntos datos, estado e identidad.
- **Dependencias:** E4 y esquema E2. Reutilizar conversores verificados. No calcular reglas KPI duplicadas en el cargador: sus conciliaciones son controles de aceptación.
- **Aceptación:** 62884 líneas, 26326 pedidos y 197757 unidades; referencias completas; precio/costo decimal; ausencia de publicación parcial; solo el commit exitoso hace visible publication_id. La repetición idéntica ya debe ser inocua.
- **Pruebas previstas:** normalización monetaria, conservación de códigos, resolución Online, calendario con roles de pedido/entrega; primer commit exitoso, error inyectado antes de commit y repetición inmediata; comprobación desde una conexión externa de que la identidad candidata no es visible.
- **Condición para continuar:** primera publicación conciliada y frontera transaccional demostrada, sin exponer una carga que aún permita duplicados o identidades anticipadas.

Los montos de referencia se contrastan con fixtures y sumas independientes antes de desarrollar las vistas productivas de E7.

**Resultado de E5:** transform.py reutiliza los conversores; load.py y load_checks.py separan persistencia y conciliación; cli.py permite la ejecución manual. Auditoría ampliada sin nuevas tablas/dependencias. Primera publicación confirmada en cada base con publication_id=1; el intento propio de sales_analytics no reutiliza el historial de pruebas. Snapshot conciliado: 62884 líneas, 26326 pedidos, 197757 unidades, USD 55755479.59 y USD 23092791.21. Repetición real en pruebas sin cambios ni identidad nueva. Se ejecutaron 245 pruebas E1–E5, Ruff y dependencias; rollback y visibilidad antes del commit comprobados con fixtures. Run_id, archivos, evidencia y límites en README y [e5_execution.md](e5_execution.md). E5 bloquea snapshots distintos aunque exista autorización; aplicación de correcciones/revaloraciones y recuperación avanzadas permanecen en E6. La suite sintética exige DW vacío y se ejecutó antes de conservar las publicaciones de aceptación. No vaciar automáticamente esas publicaciones para repetir pruebas.

## E6. Idempotencia, auditoría y reconciliación manual

- **Objetivo:** demostrar las garantías de publicación bajo reintentos, conflictos y fallos.
- **Archivos previstos:** ampliar load.py, audit.py, db.py y cli.py; tests/integration/test_publication.py y tests/integration/test_idempotency.py.
- **Responsabilidades:** distinguir vigente idéntico de snapshot antiguo; serializar publicadores; bloquear correcciones no autorizadas; aplicar cambios Tipo 1 y revaloración solo con autorización documentada; aplicar el [protocolo manual de reconciliación](architecture.md#ejecuciones-interrumpidas-y-reconciliación-manual) a intentos en curso e incertidumbre de commit. Reutilizar cli.py, auditoría y bloqueo existentes; sin servicio automático.
- **Dependencias:** E5. Auditoría mínima ya operativa, ahora comprobada a través de los límites transaccionales.
- **Aceptación:** carga vigente repetida conserva PK/SK, valores y publication_id; fallo conserva publicación previa y no deja identidad nueva; diagnósticos persisten fuera del rollback; revaloración autorizada cambia íntegro el conjunto afectado. Una ejecución abandonada solo pasa a interrumpido tras confirmar que su proceso terminó y que no publicó; un commit incierto permanece pendiente hasta conciliarlo. No hay autorización real de revaloración implícita: se prueba con datos aislados.
- **Pruebas previstas:** errores después de modificar dimensiones, tasas y hechos; dos publicadores concurrentes; ejecuciones sin cambios; replay de una versión antigua distinta de la vigente; costo/precio cambiado sin autorización; interrupciones durante extracción, validación y publicación; proceso vivo lento, PID reutilizado y equipo inaccesible; pérdida de respuesta antes/después del commit; bloqueo ocupado y reconciliación repetida; registro de diagnóstico con DB disponible y señalización/fallback local cuando no lo está.
- **Condición para continuar:** fallos y reintentos sin corrupción, sin doble publicación y con estado de resultado veraz; ninguna incidencia relevante queda oculta.

No se promete persistencia ilimitada si fallan simultáneamente PostgreSQL y el almacenamiento local; el fallo debe exponerse.

**Resultado de E6:** publicaciones de aceptación protegidas mediante comparación de firmas completas; pruebas sintéticas trasladadas a `sales_analytics_e6_test` con roles exclusivos. Se implementaron cambios Tipo 1 y revaloración autorizada, exclusión de publicadores, auditoría fuera del rollback, identificación durable del propietario de cada fase y reconciliación manual. Sin dependencias, tablas ni reglas de negocio nuevas. Operación, inventario, resultados y límites en [e6_execution.md](e6_execution.md). E7 se autorizó e implementó posteriormente.

## E7. Capa analítica SQL compartida

- **Objetivo:** expresar K1–K10 una sola vez y verificar filtros/denominadores.
- **Archivos previstos:** sql/002_marts.sql, sql/analytics.sql, src/sales_analytics/analytics.py, tests/integration/test_kpis.py.
- **Responsabilidades:** vistas de líneas, pedidos y primera compra; consultas parametrizadas; validar alcance/filtros; devolver cifras y metadatos sin dar formato. analytics.py acepta una conexión existente para que el llamador controle la transacción.
- **Dependencias:** E6 y ejemplos numéricos de business_rules.md. Ninguna consulta crea conexiones propias ni confirma por separado.
- **Aceptación:** ingresos 55755479.59 USD, costos 23092791.21 USD y margen 32662688.38 USD en el snapshot; denominadores correctos; primera compra global; recurrencia posterior a filtros; febrero de 2021 sin MoM estándar.
- **Pruebas previstas:** ejemplo didáctico completo y su tabla de filtros; vacío/denominador cero; cliente nuevo y recurrente simultáneamente; múltiples líneas por pedido; conteos distintos no aditivos; productos excluidos no reintroducidos por la vista de pedidos; comparación decimal contra resultados esperados independientes.
- **Condición para continuar:** coincidencia de todas las definiciones y ejemplos; consultas ejecutables sobre una conexión suministrada y sin caché o acceso paralelo oculto.

No materializar una tabla por gráfico ni inventar KPIs adicionales.

**Resultado de E7:** creados los cuatro archivos previstos. K1–K10 residen en SQL; Python recibe una conexión y conserva la transacción del llamador. Se implementaron las tres vistas aprobadas y, con autorización expresa adicional, `marts.sucursales` para mostrar sucursales sin ventas sin conceder acceso directo al DW. Ejemplo y filtros conciliados; ambas publicaciones reales conservan sus identidades, filas e importes. Suite E1–E7: 321 aprobadas (157 unitarias y 164 de integración, incluidas 32 de KPIs). Ruff y dependencias verificados; operación, resultados y límites en [README](../README.md#indicadores). E8 se implementó posteriormente.

## E8. Streamlit y lectura consistente

- **Objetivo:** presentar las cuatro vistas del MVP con una instantánea coherente por actualización.
- **Archivos previstos:** src/sales_analytics/dashboard.py, src/sales_analytics/read_dashboard.py, tests/integration/test_dashboard_reads.py, tests/ui/test_dashboard.py; ampliar analytics.py, db.py y README.md. Streamlit se incorpora aquí.
- **Responsabilidades:** read_dashboard.py es dueño del ciclo corto de conexión/transacción y del conjunto completo; analytics.py ejecuta consultas en esa conexión; dashboard.py maneja navegación, selección y formato. Sin clases de servicio si funciones bastan.
- **Dependencias:** E7 y pruebas del escritor E6. Si se usa Compose, añadir aquí solo lo necesario para ejecutar las aplicaciones existentes.
- **Aceptación:** filtros, KPIs, gráficos y metadatos del ciclo comparten publicación y conexión; conexión cerrada antes de renderizar; sesiones aisladas; cero caché SQL; error sin resultados parciales; ningún evento de UI dispara el ETL. Valores UI idénticos a SQL para los mismos filtros/publicación.
- **Pruebas previstas:** casos de lectura definidos en architecture.md, incluida publicación B intercalada entre consultas de A, control READ COMMITTED, lectura posterior de B, sesiones simultáneas, cancelación, tiempo agotado, filtro inválido y ausencia de publicación; inspección de que no quedan transacciones del ciclo abiertas.
- **Condición para continuar:** prueba concurrente determinista aprobada; navegación y etiquetas verificadas; tiempos máximos de conexión, consulta, inactividad y ciclo fijados y probados con el snapshot, sin transacciones abiertas durante interacción.

Cambiar una pestaña/vista o aplicar filtros puede iniciar una lectura nueva; no se promete mantener una publicación durante toda la sesión del usuario.

**Resultado de E8:** cuatro páginas Streamlit, lectura completa REPEATABLE READ, permisos de solo lectura y descarte de resultados parciales. Suite E1–E8: 345 pruebas aprobadas (157 unitarias, 179 de integración y 9 UI). Ciclos medidos: 12,65 s y 17,02 s; sin prueba de carga multiusuario.

## E9. Pruebas integrales y documentación del portafolio

- **Objetivo:** demostrar un recorrido reproducible desde los CSV hasta la interfaz.
- **Archivos previstos:** tests/integration/test_end_to_end.py, README.md, docs/portfolio.md; actualizar solo la documentación afectada por decisiones verificadas.
- **Responsabilidades:** instalación documentada, carga manual, consultas, dashboard, reejecución y fallo controlado; explicar arquitectura, límites y decisiones; capturas/demo de la aplicación ya existente.
- **Dependencias:** E1–E8 aceptadas. No añade funcionalidades de negocio, autenticación ni despliegue cloud.
- **Aceptación:** recorrido completo en entorno limpio; indicadores de referencia conciliados; reejecución inocua; publicación anterior conservada ante fallo; lectura concurrente consistente; documentación permite repetirlo sin conocimiento de la conversación.
- **Pruebas previstas:** recorrido end-to-end con originales de solo lectura, fallo con fixture separada, integridad de hashes, igualdad SQL/UI, calidad visible y revisión de instrucciones/capturas. Ejecutar la suite pertinente final; no afirmar pruebas que no puedan ejecutarse.
- **Condición de cierre:** evidencia registrada de los criterios y limitaciones; proyecto listo para revisión de portafolio dentro del alcance local aprobado.

**Resultado de E9 (27/09/2026):** 347 pruebas aprobadas (157 unitarias, 180 de integración y 10 UI) en 612,88 s, incluido el end-to-end completo en 318,82 s. Instalación limpia desde el lock, 51 versiones coincidentes, Ruff lint/formato y pip check aprobados. Cero conexiones residuales; las dos publicaciones de aceptación, staging, auditoría y hashes originales permanecen intactos. README, guía local, portafolio y cuatro capturas documentan el MVP. El timeout inicial observado y la ausencia de prueba de carga multiusuario quedan explícitos en [portfolio.md](portfolio.md). Sin nuevas dependencias ni reglas de negocio. No se publicó en GitHub.

## Matriz de aceptación y dependencias

| Criterio | Entrega responsable | Dependencia / evidencia prevista |
|---|---|---|
| Entorno y configuración sin conexión PostgreSQL | E1 | Restauración desde lock y validación local; conexiones reales en E2 |
| Staging textual e incidencias trazables | E3–E4 | Persistencia previa a validación; evidencia conservada ante rechazo/rollback |
| Conservación de seis CSV, codificación y códigos | E3–E4 | Hashes y fixtures de lectura, después de E2 |
| Grano, PK/FK, Online y nulos legítimos | E2, E5 | Restricciones y primera carga |
| 62884 líneas, 26326 pedidos, 197757 unidades | E5, E9 | Snapshot y conciliación; no límites permanentes |
| Importes de catálogo de referencia | E5, E7 | Agregación independiente y consultas KPI |
| Idempotencia e identidades estables | E5–E6 | Repetición vigente y concurrencia de escritores |
| Rollback analítico y diagnósticos persistentes | E6 | Fallos inyectados y lectura externa |
| Interrupción frente a fallo y commit incierto | E6 | Proceso terminado, exclusión y relectura del intento; sin nueva publicación al reconciliar |
| publication_id solo confirmado, único | E2, E5–E6 | Restricción, no visibilidad antes de commit, fallo/sin cambios |
| Nuevos globales, recurrentes bajo filtros | E7 | Tabla didáctica de business_rules.md |
| SQL y UI equivalentes; etiquetas y mes parcial | E8–E9 | Mismos filtros/publicación |
| Una instantánea por actualización | E8 | Dos conexiones sincronizadas, A/B y nueva lectura |
| Liberación de recursos ante fallos | E8 | Error, cancelación, timeout y sesiones simultáneas |

## Riesgos y decisiones pendientes

D1–D6, el MVP y el contrato de lectura están cerrados. No se requiere volver a aprobarlos para documentarlos.

E1–E2 resolvieron las versiones, el entorno reproducible, PostgreSQL local y la base aislada de pruebas. E8 fijó límites de conexión de 5 s, consulta de 40 s, inactividad de 5 s y ciclo de 60 s. El sentido de Exchange y las causas de los huecos requieren evidencia externa; la conversión y las inferencias causales permanecen deshabilitadas.

Cambiar estas decisiones de diseño, ampliar alcance o revalorar datos reales requerirá autorización específica. **Tras E9, detenerse. Cualquier publicación en GitHub requiere autorización expresa posterior.**
