# Modelo de datos — Plataforma Analítica de Ventas

**Estado: diseño documental aprobado; implementación no autorizada.** D1–D6 y el contrato de lectura fueron aprobados por el usuario.
Base: [perfil comprobado](data_profile.md), [diccionario](data_dictionary.md) y [reglas aprobadas](business_rules.md). Los dos primeros se conservan como evidencia de la revisión inicial: sus menciones de aprobación pendiente quedan resueltas por las decisiones vigentes de business_rules.md.

Este documento define tablas e identidad; [architecture.md](architecture.md) define el ciclo de publicación y lectura; [implementation_plan.md](implementation_plan.md) organiza las entregas futuras.

## 1. Objetivo y límites

Un modelo estrella para analizar las líneas recibidas y valorarlas con el catálogo disponible, conservando trazabilidad. La estructura no supone que existan importes cobrados, descuentos, devoluciones, estados de pedido o historial de precios.

Los nombres españoles siguientes son **nombres de destino propuestos**. Las claves sustitutas, campos de auditoría y valores derivados se generarían en el futuro; no son campos inventados del origen.

El ETL será independiente de Streamlit. La aplicación consultará la capa analítica; no cargará archivos ni recalculará el histórico al abrirse o cambiar filtros.

## 2. Organización por esquemas

| Esquema | Objetos propuestos | Responsabilidad |
|---|---|---|
| staging | Una tabla textual por cada una de las cinco tablas de negocio | Conservar registros durante la extracción, antes de validar su semántica, vinculados a run_id y ordinal de origen |
| dw | Cuatro dimensiones, fact_ventas y tipos_cambio | Identidad, relaciones y valoración trazable |
| marts | Vistas de líneas valoradas, pedidos y primera compra observada | Centralizar las bases de los KPIs sin duplicar su definición en Streamlit |
| ops | ejecuciones e incidencias | Evidencia de origen, resultado del procesamiento y motivos de rechazo/advertencia |

El diccionario original se conserva como archivo y su hash se registra con el lote. No necesita tabla de hechos ni dimensión. No se proponen dimensiones independientes para canal, marca, color, categoría, país o moneda: actualmente no tienen atributos o ciclos de vida propios que justifiquen esa separación.

### Staging textual

Cada tabla conserva los campos del CSV como texto decodificado, incluidos vacíos, espacios, NA y ceros iniciales; sin normalización ni conversión de fechas/importes. Las comillas y escapes se interpretan al leer CSV: no es una copia byte a byte, que sigue siendo el archivo original identificado por hash.

La PK técnica es (run_id, numero_registro_origen), con FK de run_id a ops.ejecuciones. El ordinal cuenta registros lógicos sin encabezado, no líneas físicas. Las claves de negocio, incluso repetidas o inválidas, se conservan como texto; las restricciones semánticas del DW no se aplican a staging.

ops.incidencias identifica lógicamente el registro mediante run_id, archivo/tabla y ordinal; las incidencias de archivo o lote pueden no tener ordinal. No necesita una FK polimórfica a las cinco tablas. El estado de lectura por archivo y sus conteos/hashes pertenecen a los metadatos del intento, sin otra tabla.

La persistencia por archivo ocurre en E3 y precede a E4. El [ciclo de staging](architecture.md#staging-y-evidencia-de-origen) define transacciones independientes, archivos ilegibles y conservación de evidencia; el rollback analítico no elimina registros de staging ya confirmados.

## 3. Estrella y relaciones

- dim_cliente → fact_ventas: 1:N.
- dim_producto → fact_ventas: 1:N.
- dim_sucursal → fact_ventas: 1:N, incluida la entrada Online.
- dim_fecha → fact_ventas: 1:N por fecha de pedido, y otro rol opcional por fecha de entrega.
- tipos_cambio → fact_ventas: 1:N por fecha de pedido + código de moneda.
- dim_fecha → tipos_cambio: 1:N por fecha.

Cada línea enlaza con una sola fila de cada dimensión. En el snapshot actual la unión debe conservar exactamente 62884 filas; aumentarlas indicaría multiplicación por un enlace no único.

### dw.fact_ventas

**Grano:** una línea original identificada por Order Number + Line Item, dentro de la única fuente Maven de esta versión.

| Campo de destino | Origen o derivación | Papel |
|---|---|---|
| numero_pedido | Sales.Order Number | BIGINT; parte de PK, identificador degenerado de pedido |
| linea_pedido | Sales.Line Item | INTEGER; parte de PK; conservar huecos |
| fecha_pedido | Sales.Order Date | DATE; FK obligatoria a dim_fecha |
| fecha_entrega | Sales.Delivery Date | DATE nullable; FK de otro rol a dim_fecha |
| cliente_id | Correspondencia de CustomerKey | FK a clave sustituta de dim_cliente |
| producto_id | Correspondencia de ProductKey | FK a clave sustituta de dim_producto |
| sucursal_id | Correspondencia de StoreKey | FK a clave sustituta de dim_sucursal |
| cantidad | Sales.Quantity | INTEGER positivo |
| moneda_pedido | Sales.Currency Code | TEXT; parte del enlace con tipos_cambio |
| precio_referencia_usd | Products.Unit Price USD del catálogo aceptado | NUMERIC(18,2); valor usado para estimar esta línea |
| costo_referencia_usd | Products.Unit Cost USD del catálogo aceptado | NUMERIC(18,2); valor usado para estimar esta línea |
| run_id | Ejecución que insertó o modificó materialmente la línea | FK técnica a ops.ejecuciones |

PK natural compuesta (numero_pedido, linea_pedido); no se añade una clave sustituta de hecho porque ningún requisito la necesita. No se añade fuente_id mientras exista una única fuente; integrar otra requerirá revisar el contrato de identidad.

No se almacenan importes derivados, canal ni nombres en la tabla de hechos. Ingresos y costos de línea se calculan una sola vez en marts a partir de cantidad y valores de referencia.

La copia del precio/costo de catálogo en el hecho es una **duplicación deliberada para fijar la valoración**: una edición descriptiva del catálogo no altera importes históricos. No es evidencia de un precio real aplicado en la compra. run_id enlaza con el hash del catálogo utilizado; una revaloración autorizada cambia valores y trazabilidad de forma atómica.

### dw.dim_cliente

| Elemento | Propuesta |
|---|---|
| PK sustituta | cliente_id, BIGINT generado |
| Clave natural única | customer_key ← Customers.CustomerKey, INTEGER |
| Atributos | genero, nombre, ciudad, codigo_estado, estado, codigo_postal, pais, continente, fecha_nacimiento |
| Justificación | Identidad del comprador, filtros geográficos y demográficos |
| Actualización | Tipo 1 para correcciones descriptivas aprobadas; sin reconstruir domicilios históricos inexistentes |

No contiene fecha de alta ni «nuevo/recurrente» fijo. La primera compra se deriva del histórico publicado; la recurrencia depende del período y filtros.

### dw.dim_producto

| Elemento | Propuesta |
|---|---|
| PK sustituta | producto_id, BIGINT generado |
| Clave natural única | product_key ← Products.ProductKey, INTEGER |
| Atributos | nombre, marca, color, subcategoria_codigo, subcategoria, categoria_codigo, categoria |
| Valores de referencia | precio_catalogo_usd, costo_catalogo_usd, NUMERIC(18,2) |
| Justificación | Clasificación de productos y catálogo de referencia para nuevas valoraciones |
| Actualización | Descripciones Tipo 1; cambios monetarios requieren la política explícita de revaloración |

Los códigos de categoría/subcategoría son texto para conservar ceros iniciales. Se exige correspondencia estable código→nombre y subcategoría→categoría en cada catálogo aceptado. La jerarquía permanece desnormalizada en esta dimensión.

### dw.dim_sucursal

| Elemento | Propuesta |
|---|---|
| PK sustituta | sucursal_id, BIGINT generado |
| Clave natural única | store_key ← Stores.StoreKey, INTEGER; 0 es válido |
| Atributos | pais_origen, estado_origen, superficie_m2, fecha_apertura |
| Atributo derivado | canal: Online para la entrada StoreKey=0 documentada; Físico para 1–66 en el snapshot actual |
| Justificación | Comparación por tienda, canal y ubicación de la tienda |
| Actualización | Tipo 1; cambio de clasificación de canal requiere revisar el contrato |

Se preservan Country=Online y State=Online como valores originales. La presentación mostrará «No aplica» para su ubicación física y superficie, no cero metros cuadrados. No se fabrica una tienda física o un país para pedidos online.

No se generaliza que cualquier nueva clave distinta de cero sea física: futuras entradas deben verificarse contra el catálogo y el contrato de origen. «País del cliente» y «país de la sucursal» serán filtros diferentes.

### dw.dim_fecha

| Elemento | Propuesta |
|---|---|
| Clave natural y PK | fecha, DATE |
| Clave sustituta | No necesaria: la fecha es compacta, estable y no ambigua |
| Atributos derivados | año, mes_numero, trimestre, inicio_mes, fin_mes |
| Roles | Pedido, entrega y fecha de tasa |
| Justificación | Calendario continuo y períodos comparables |

Para este snapshot, abarcará 2015-01-01 a 2021-02-27: tasas, pedidos y entregas. Nacimientos y aperturas son atributos DATE de sus dimensiones, sin ampliar artificialmente el calendario analítico hasta 1935.

La dimensión calendario no garantiza que existan ventas o que una carga esté completa. Su mera existencia no autoriza rellenar huecos con ventas reales cero.

### dw.tipos_cambio

| Elemento | Propuesta |
|---|---|
| PK natural | fecha + moneda |
| Clave sustituta | No necesaria |
| Campos | fecha ← Date; moneda ← Currency; tasa_original ← Exchange |
| Tipo de tasa | NUMERIC(18,8), positiva |
| Justificación | Conservar las 11215 referencias y validar cobertura fecha/moneda |
| Relación | Fecha a dim_fecha; fact_ventas enlaza por fecha_pedido + moneda_pedido |

No es una dimensión adicional de moneda. La clave compuesta evita multiplicación de ventas por las múltiples fechas/monedas.

La tasa se conserva sin atribuirle una dirección no documentada. Su disponibilidad es parte del contrato de integridad propuesto; los KPIs USD no dependen de ella matemáticamente. La conversión local permanece deshabilitada hasta confirmar su convención.

## 4. Restricciones y contratos

Contratos del DW que deberán implementarse y probarse después de recibir autorización para programar; staging conserva texto y aplica solo sus restricciones técnicas:

- Unicidad y no nulidad de PK y claves naturales; FKs obligatorias sin registros huérfanos.
- numero_pedido, linea_pedido y cantidad: enteros positivos; no imponer los máximos observados como límites de negocio.
- Precio de referencia no negativo y costo no negativo; precio cero es advertencia, no invalidez automática. Se observan solo positivos en el snapshot.
- Tasas positivas; USD=1 bajo el contrato de referencia en USD.
- fecha_entrega, si existe, no anterior al pedido. La entrega física vacía se conserva.
- superficie_m2 positiva cuando esté informada; Online admite nulo.
- Un pedido mantiene cliente, tienda, moneda, fecha de pedido y fecha de entrega consistentes entre líneas. Este control cruza filas y debe ejecutarse antes de publicar, no se reduce a una restricción de una sola fila.
- Validar correspondencias de categorías y estructura del CSV.
- No exigir continuidad de Line Item, nombres únicos, mayoría de edad, margen positivo o ventas diarias.
- No descartar por cantidades >10 ni por importes superiores a los del perfil: son valores fuera del rango observado, no imposibles por definición.

Los índices iniciales se limitan a PK y unicidad. Índices adicionales sobre FKs o filtros se decidirán con consultas reales y planes de ejecución; no se prescribe particionado para este volumen.

## 5. Actualización e idempotencia

### Contrato inicial de carga

Primera versión: un snapshot completo identificado por los hashes de los seis archivos y la versión aprobada de reglas. No se deduce del contenido si una entrega futura es completa o incremental. El modo debe declararse; reproducir lotes incrementales sería una ampliación posterior.

Una ejecución mantiene run_id, hashes/rutas y estado de lectura por archivo, versión de reglas, modo, conteos, intervalo de pedidos observado, estado y fechas. Para detectar interrupciones conserva equipo, PID, inicio del proceso, fase y último avance; son metadatos de control en ops.ejecuciones, no nuevas tablas ni datos de negocio.

### Casos

| Situación | Política propuesta |
|---|---|
| Mismos hashes y versión de reglas que la publicación actualmente vigente | Registrar ejecución sin cambios, con publication_id nulo; no duplicar datos ni cambiar identidades o el run_id de los hechos |
| Reintento después de fallo o interrupción reconciliada | Nueva ejecución auditada; la publicación anterior permanece visible |
| Nuevo identificador natural válido | Crear dimensión o línea; resolver claves naturales antes de insertar hechos |
| Misma clave y mismo contenido en el destino | No modificar |
| Clave existente con contenido distinto | Conflicto: bloquear publicación hasta aprobar la corrección; sin «último gana» implícito |
| Duplicados dentro de un archivo, idénticos o contradictorios | Bloquear; conservar evidencia, sin deduplicación silenciosa |
| Fila anterior ausente de un nuevo snapshot completo | Advertir diferencia y bloquear sustitución hasta confirmar alcance; no borrar ni simular cancelación |
| Registro tardío autorizado | Insertar y recalcular agregaciones/primera compra de períodos afectados |
| Cambio descriptivo autorizado en dimensión | Tipo 1, sin cambiar su clave sustituta; advertir que los atributos actuales reclasifican el histórico |
| Cambio de precio/costo de catálogo | Bloquear hasta aprobar revaloración; no inferir fecha de vigencia |

Al no existir vigencia de precios, la política aprobada ante cambios monetarios es **revalorar todas las líneas históricas del producto afectado dentro de la nueva versión**, tras aprobación y en una sola transacción. Se actualizan catálogo, referencias de hechos y run_id de líneas afectadas; los hashes anteriores se conservan en auditoría. No mezclar inadvertidamente dos versiones de valoración bajo un mismo KPI.

No se propone SCD Tipo 2: el origen no suministra historia de atributos ni vigencias. Una nueva necesidad de análisis «tal como era» requeriría fuentes y un diseño específico.

### Identidad de ejecución y publicación

Se amplía únicamente ops.ejecuciones; no se añade una tabla de versiones.

| Metadato | Contrato |
|---|---|
| run_id | Identificador único de cada intento, incluso fallido, interrumpido o sin cambios; PK de ejecución |
| estado | En curso, fallido, interrumpido, sin cambios o publicado. Interrumpido exige reconciliación; incertidumbre no equivale a un estado terminal |
| publication_id | Entero positivo, único entre valores no nulos; nulo para intentos en curso, fallidos, interrumpidos o sin cambios |
| Versión de reglas y hashes | Identifican las reglas y fuentes usadas por la publicación |
| Fechas y conteos | Trazabilidad de ejecución; no sustituyen publication_id para ordenar publicaciones |

La invariante confirmada es: estado publicado si y solo si publication_id no es nulo. Solo una publicación efectiva confirmada recibe identidad visible. El escritor exclusivo calcula el siguiente ordinal a partir de publicaciones confirmadas y lo registra **dentro de la misma transacción que todos los cambios analíticos**. Antes del commit es solo un valor candidato no visible; al revertirse desaparece junto con esos cambios. No se reserva ni anuncia una versión desde otra transacción.

La exclusión entre publicadores se adquiere antes de leer el estado vigente; se vuelve a comprobar idempotencia bajo esa exclusión. La restricción de unicidad es una defensa adicional, no el mecanismo de coordinación. No se promete numeración sin huecos.

Las publicaciones confirmadas y sus identificadores se conservan en auditoría sin reasignarlos. Se localiza la vigente por el mayor publication_id confirmado, no por orden de run_id, fecha de inicio o último intento. Una reejecución sin cambios conserva esa identidad; publicar de nuevo un snapshot antiguo después de otro distinto no es automáticamente una operación sin cambios y debe resolver los conflictos correspondientes.

publication_id identifica un estado global confirmado del DW. No es un filtro sobre fact_ventas.run_id: las filas no modificadas pueden provenir de ejecuciones anteriores. No se conservan copias históricas consultables de todas las tablas; una identidad de auditoría no permite reabrir una instantánea ya cerrada.

### Publicación y auditoría

Staging y validación no son visibles como datos de ventas. Todas las modificaciones de dimensiones, tasas, hechos y metadatos de publicación se confirman conjuntamente. Ante un fallo confirmado se revierte el cambio analítico completo y permanece la publicación anterior.

ops.ejecuciones registra el inicio antes de publicar; ops.incidencias conserva run_id, archivo/registro o intervalo, regla, clasificación, motivo y evidencia mínima. Los diagnósticos de fallo se confirman fuera de la transacción revertida. La [publicación](architecture.md#publicación-atómica-y-diagnósticos) y la [reconciliación manual](architecture.md#ejecuciones-interrumpidas-y-reconciliación-manual) tienen sus protocolos en architecture.md. Una antigüedad elevada no permite reclasificar un intento; interrumpido se distingue del fallo observado y del commit todavía incierto.

Las claves sustitutas permanecen estables. No vaciar ni regenerar dimensiones/hechos para cada carga, ni utilizar TRUNCATE o sustitución de tablas publicadas durante la lectura concurrente.

La aplicación lee la publicación confirmada visible al fijar su instantánea, que puede ser anterior a una publicación concurrente posterior. Su actualización completa sigue el [contrato de lectura consistente](architecture.md#lectura-consistente-del-dashboard).

## 6. Capa analítica compartida

| Vista lógica propuesta | Grano | Responsabilidad |
|---|---|---|
| marts.ventas_base | Línea de pedido | Exponer dimensiones y calcular ingreso/costo/margen estimado de línea |
| marts.pedidos | Pedido | Proveer una única fila por pedido con sus atributos consistentes y totales |
| marts.clientes_primera_compra | Cliente con compra | Mínima fecha de pedido en todo el histórico publicado |

Estas son vistas lógicas propuestas, sin SQL en esta fase. Las fórmulas definitivas y filtros se rigen por [business_rules.md](business_rules.md). No crear una tabla agregada por cada gráfico antes de necesitarla.

Los indicadores filtrados se calculan sobre las líneas seleccionadas. El número de pedidos se cuenta distinto en esa selección: no se suman subtotales de pedidos por producto. El ticket se calcula a partir de ingresos y pedidos, no promediando tickets precalculados. La vista global de pedidos no debe reintroducir líneas de productos excluidos.

Streamlit reutiliza resultados de esa capa y solo da formato; no mantiene fórmulas de negocio paralelas.

## 7. Fechas, monedas y cobertura

- Fecha canónica de ventas: Order Date, sin hora ni conversión de zona.
- Intervalos de consulta inclusivos; las fechas de entrega no condicionan que una línea cuente como venta registrada.
- Moneda de reporte aprobada: USD. moneda_pedido se conserva para segmentación; no se aplica Exchange al precio USD.
- Corte observado de pedidos: 2021-02-20. No sustituirlo por la fecha actual ni por la última entrega.
- Se establecen etiquetas separadas: mes cerrado dentro del intervalo observado, mes truncado y cobertura comercial no verificada.
- MoM estándar solo compara meses enteros dentro del intervalo publicado; febrero de 2021 queda sin MoM estándar. Los demás meses conservan advertencias por posibles huecos.
- No imputar pedidos, clientes, importes o cantidades para días sin registros. Los conteos cero se etiquetan «sin registros», no «sin actividad comercial».
- El estado de cobertura pertenece a los metadatos de la publicación/incidencias, no a un atributo inmutable del calendario.

## 8. Criterios de aceptación del futuro modelo

1. Mantener grano, 62884 líneas y 26326 pedidos en la primera carga aceptada.
2. Resolver todas las FKs sin multiplicar ni perder líneas.
3. Reproducir 197757 unidades y las sumas de referencia del perfil con aritmética decimal.
4. Repetir la misma carga sin cambiar resultados ni identidades.
5. Demostrar que un fallo conserva íntegra la publicación anterior.
6. Conservar Online, vacíos legítimos, NA de Napoli y códigos con ceros iniciales.
7. Rechazar conflictos de claves y correcciones silenciosas; conservar su evidencia.
8. Obtener el mismo KPI para los mismos filtros desde SQL y Streamlit.
9. Etiquetar estimaciones y períodos parciales; no afirmar completitud comercial no demostrada.
10. Mantener publication_id nulo en fallos, interrupciones reconciliadas y ejecuciones sin cambios; confirmar cada nueva identidad con sus datos.
11. Obtener filtros, KPIs, gráficos y metadatos de cada actualización desde una sola conexión y transacción de solo lectura REPEATABLE READ; liberar recursos y descartar resultados parciales si falla.

Son criterios para pruebas futuras, **no pruebas de PostgreSQL ya ejecutadas**.

## 9. Autorización y límites

El diseño y D1–D6 están aprobados. Las entregas del plan requieren una autorización posterior y expresa para implementar. Permanecen abiertos los hechos que solo puede confirmar el proveedor: dirección de tasas y causas de los huecos. No se introducen SCD Tipo 2 ni nuevas tablas de versionado.
