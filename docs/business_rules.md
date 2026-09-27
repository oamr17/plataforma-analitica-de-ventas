# Reglas de negocio y calidad — Sales Analytics Platform

**Estado: diseño documental aprobado; implementación no autorizada.** D1–D6, alcance del MVP y contrato de lectura aprobados por el usuario.
Evidencia: [data_profile.md](data_profile.md). Campos de origen: [data_dictionary.md](data_dictionary.md). Estructura: [data_model.md](data_model.md). Operación y lectura: [architecture.md](architecture.md).

El perfil y el diccionario se conservan como evidencia histórica; sus menciones de decisiones pendientes quedan resueltas por este documento. Aprobar el diseño no confirma hipótesis sobre el origen.

## Contrato común de los indicadores

### Alcance y lenguaje

Se analizan **pedidos registrados en el extracto**. No existe estado de pago, cancelación o devolución; no se puede afirmar que todos sean ventas cobradas o completadas. No se fabricarán filtros para campos inexistentes.

La unidad monetaria canónica aprobada es USD. Los nombres visibles serán «Ingresos estimados», «Ticket promedio estimado» y «Margen estimado de catálogo». El ejemplo genérico de ticket sobre ventas netas de AGENTS.md se concreta aquí con ingresos estimados porque la fuente no contiene ventas netas efectivas.

### Notación y filtros

- P=[inicio, fin]: período inclusivo, aplicado sobre Sales.Order Date.
- F: filtros no temporales explícitos, por producto/categoría, cliente/país del cliente, sucursal/país de sucursal, canal o moneda del pedido.
- L(P,F): líneas válidas de la versión publicada que satisfacen P y F.
- qᵢ: Quantity de la línea i.
- pᵢ y cᵢ: precio y costo USD de referencia fijados desde el catálogo aceptado.
- rᵢ=qᵢ×pᵢ; kᵢ=qᵢ×cᵢ; mᵢ=rᵢ−kᵢ.
- O(P,F): conjunto de Order Number distintos representados por L.
- C(P,F): conjunto de CustomerKey distintos representados por L.
- primera(c): mínima Order Date del cliente en **todo el histórico publicado**, antes de aplicar P o F.

Todos los importes y ratios deben usar el mismo conjunto de líneas. Filtrar productos reduce el importe de cada pedido al de sus líneas seleccionadas: el ticket resultante es el ticket de la selección, no necesariamente el valor de la cesta completa.

No sumar conteos distintos de pedidos o clientes entre productos, sucursales o períodos para construir el total. Se recalculan sobre la unión de líneas. Los ingresos, costos y unidades son aditivos entre grupos de líneas disjuntos; tickets, porcentajes y conteos distintos no siempre lo son.

### Vacíos, precisión y versiones

- Sumas y conteos sobre un conjunto vacío: 0; cocientes con denominador 0 o sin período comparable: no calculable, mostrado como «—», no como 0%.
- Importes con aritmética decimal; precio/costo base con dos decimales. Sumar valores sin redondeos intermedios de ratios.
- Presentar dinero, ticket y porcentajes con dos decimales; empates de redondeo alejándose de cero. Conservar precisión antes de presentar.
- Redondear margen porcentual una vez después de dividir sumas; no promediar porcentajes de productos.
- Cada actualización lógica consume una sola publicación, identificada dentro de su transacción de lectura; incluye filtros, KPIs, gráficos y metadatos. No se mezclan resultados de actualizaciones distintas ni se cachean resultados SQL en el MVP. El ciclo completo está en architecture.md.
- Las fórmulas permanecen en la capa analítica; Streamlit no las duplica.
- Las entregas vacías no excluyen ventas. Tampoco se excluyen líneas por importes altos, edad menor a 18, categorías sin ventas o numeración no consecutiva.

## Ejemplo didáctico común

**Datos hipotéticos, exclusivamente para explicar fórmulas; no son filas de Maven.** Se supone que este es todo el historial del ejemplo, enero/febrero están cerrados y no hay rechazos. Todos los valores monetarios son USD.

| Pedido | Línea | Fecha | Cliente | Sucursal/canal | Producto | q | p | c | Ingreso | Costo |
|---|---:|---|---|---|---|---:|---:|---:|---:|---:|
| P1 | 1 | 2020-01-10 | A | T1/Físico | X | 2 | 100 | 60 | 200 | 120 |
| P2 | 1 | 2020-01-20 | A | Online | X | 1 | 100 | 60 | 100 | 60 |
| P2 | 2 | 2020-01-20 | A | Online | Y | 1 | 50 | 20 | 50 | 20 |
| P3 | 1 | 2020-01-25 | B | T1/Físico | Y | 3 | 50 | 20 | 150 | 60 |
| P4 | 1 | 2020-02-10 | A | T1/Físico | X | 2 | 100 | 60 | 200 | 120 |

## K1. Ingresos estimados

1. **Definición:** valoración de las unidades registradas con los precios de lista USD del catálogo aceptado.
2. **Fórmula:** R(P,F)=Σ rᵢ para i∈L(P,F).
3. **Campos de origen:** Sales.Quantity, ProductKey y Order Date; Products.ProductKey y Unit Price USD; dimensiones para F.
4. **Condiciones y exclusiones:** correspondencia única de producto y precio interpretable; incluir todas las líneas publicadas seleccionadas. No aplicar Exchange ni inventar descuentos/impuestos.
5. **Limitaciones:** precio de lista sin vigencia ni importe cobrado; sin devoluciones/cancelaciones conocidas. No representa ingreso contable ni venta neta.
6. **Ejemplo:** enero=200+100+50+150=500.00 USD.

## K2. Pedidos

1. **Definición:** pedidos distintos con al menos una línea en la selección.
2. **Fórmula:** N(P,F)=|O(P,F)|.
3. **Campos de origen:** Sales.Order Number, Order Date y campos de filtro. Line Item preserva el grano pero no es el contador.
4. **Condiciones y exclusiones:** contar cada identificador una vez. Consistencia del encabezado validada antes de publicar; no se requiere fecha de entrega.
5. **Limitaciones:** los 621 pedidos con saltos de línea siguen contando; no se demuestra integridad de su cesta ni estado final. Bajo filtro de producto cuenta pedidos que contienen ese producto.
6. **Ejemplo:** enero contiene cuatro líneas y tres pedidos: P1, P2 y P3. Resultado=3.

## K3. Unidades registradas

1. **Definición:** unidades registradas en las líneas seleccionadas; nombre visible acompañado de la limitación de estado de venta.
2. **Fórmula:** U(P,F)=Σ qᵢ.
3. **Campos de origen:** Sales.Quantity, Order Date y campos de filtro.
4. **Condiciones y exclusiones:** Quantity entero positivo; ningún límite máximo de 10 se impone como regla de negocio. Rechazos impiden publicar el lote conforme a la política de calidad.
5. **Limitaciones:** no equivale a unidades efectivamente entregadas o netas de devoluciones; no incluye operaciones ausentes del extracto.
6. **Ejemplo:** enero=2+1+1+3=7 unidades.

## K4. Ticket promedio estimado

1. **Definición:** ingreso estimado por pedido distinto de la selección.
2. **Fórmula:** T(P,F)=R(P,F)/N(P,F), si N>0.
3. **Campos de origen:** los de K1 y Sales.Order Number.
4. **Condiciones y exclusiones:** numerador y denominador con idénticos filtros. N=0 produce no calculable. No usar número de líneas ni promediar tickets segmentados.
5. **Limitaciones:** hereda la valoración de catálogo; con filtro de producto describe el importe seleccionado por pedido, no la cesta completa.
6. **Ejemplo:** enero=500/3=166.67 USD. Para X=300/2=150.00 USD.

## K5. Crecimiento mensual de ingresos estimados

1. **Definición:** variación porcentual entre un mes calendario y su mes calendario inmediatamente anterior, con el mismo F.
2. **Fórmula:** G(m,F)=100×[R(m,F)−R(m−1,F)]/R(m−1,F), si el mes anterior tiene ingreso positivo y ambos meses cumplen las condiciones.
3. **Campos de origen:** Sales.Order Date, Quantity y ProductKey; Products.Unit Price USD; calendario y metadatos de cobertura propuestos.
4. **Condiciones y exclusiones:** ambos meses enteros dentro del intervalo publicado; no comparar el último mes truncado con uno completo. Sin anterior, con anterior cero, selección parcial o fallo conocido de cobertura: no calculable. No buscar el «último mes con ventas» saltando meses.
5. **Limitaciones:** meses dentro del intervalo pueden tener huecos del extracto; se muestra como crecimiento de registros observados y con advertencia. No es evidencia de crecimiento comercial completo ni explica causas. El MoM estándar de febrero de 2021 y de enero de 2016 no se publica.
6. **Ejemplo:** febrero frente a enero en el ejemplo cerrado=(200−500)/500×100=−60.00%.

Una comparación MTD 1–20 contra 1–20 podría definirse posteriormente como otro indicador; no forma parte del MoM estándar de esta fase. Los meses históricos con huecos conocidos pueden mostrar cálculo descriptivo después de aceptar expresamente la advertencia; un nuevo fallo de carga impide actualizar la publicación.

## K6. Clientes nuevos observados

1. **Definición:** compradores de la selección cuya primera compra en todo el histórico disponible ocurre dentro de P.
2. **Fórmula:** Nuevos(P,F)=|{c∈C(P,F): inicio≤primera(c)≤fin}|.
3. **Campos de origen:** Sales.CustomerKey, Order Date y campos de filtro; Customers.CustomerKey identifica al cliente. Birthday no interviene.
4. **Condiciones y exclusiones:** calcular primera(c) antes de filtros de fecha, producto o canal; luego exigir compra en la selección. Excluir clientes del catálogo sin compras. No recalcular la primera compra a partir del subconjunto visible.
5. **Limitaciones:** significa nuevo en el histórico observado, no alta real ni adquisición demostrada. El corte inicial y los huecos pueden clasificar como nuevos a clientes previamente existentes. Una carga retrospectiva puede cambiar primera(c).
6. **Ejemplo:** enero: A y B, resultado=2. Febrero: A ya apareció en enero, resultado=0.

## K7. Clientes recurrentes en el período

1. **Definición aprobada:** compradores con al menos dos pedidos distintos dentro de P y F, después de aplicar esos filtros.
2. **Fórmula:** Recurrentes(P,F)=|{c∈C(P,F): |{o∈O(P,F) del cliente c}|≥2}|.
3. **Campos de origen:** Sales.CustomerKey, Order Number, Order Date y campos de filtro.
4. **Condiciones y exclusiones:** varias líneas del mismo pedido cuentan como una compra; se cuentan solo pedidos representados en la selección. Tasa complementaria, si se muestra: 100×Recurrentes/|C|; sin clientes, no calculable.
5. **Limitaciones:** no mide retención ni significa «ya había comprado antes del período». Puede solaparse con nuevos; no sumar ambos grupos como partición del total. El período y filtros cambian la clasificación.
6. **Ejemplo:** enero: A tiene P1/P2, B solo P3; recurrentes=1, tasa=50%. A también es nuevo en enero. Febrero: A tiene solo P4; recurrentes=0 aunque compró en enero.

D3 confirma esta definición. «Clientes que compraron antes del período» no es este indicador ni se añade como otro KPI del MVP.

### Ejemplos verificables de filtros y recurrencia

Aplicados al ejemplo didáctico anterior, sin agregar datos:

| Período y selección | Pedidos distintos | Clientes | Nuevos observados | Recurrentes |
|---|---:|---:|---:|---:|
| Enero, todos | 3 | 2 | 2 | 1 |
| Enero, producto X | 2 | 1 | 1 | 1 |
| Enero, producto Y | 2 | 2 | 2 | 0 |
| Enero, Online | 1 | 1 | 1 | 0 |
| 20–31 de enero, todos | 2 | 2 | 1 | 0 |
| Febrero, todos | 1 | 1 | 0 | 0 |

En Online, P2 tiene dos líneas pero es una compra. Del 20 al 31 de enero, A no es nuevo: su primera compra global fue el día 10, aunque quede fuera del filtro temporal. En febrero, haber comprado antes no vuelve recurrente a A: solo tiene P4 en el período.

Los clientes por producto suman 1+2=3, pero hay dos clientes distintos en enero. Los pedidos por producto suman 2+2=4, pero hay tres pedidos distintos. En enero, A es simultáneamente nuevo y recurrente; nuevos+recurrentes no es una partición de los compradores.

## K8. Ventas por producto

1. **Definición:** ingresos estimados agrupados por identidad de producto, acompañados por unidades y pedidos distintos del producto.
2. **Fórmula:** para producto j, Rⱼ=Σ rᵢ, Uⱼ=Σ qᵢ y Nⱼ=pedidos distintos, restringidos a i∈L y producto=j.
3. **Campos de origen:** Sales.ProductKey, Quantity, Order Number y Order Date; Products.ProductKey, Product Name, Unit Price USD y atributos de clasificación.
4. **Condiciones y exclusiones:** unir por clave; el ranking principal ordena ingreso descendente, con ProductKey ascendente para empates. Los productos del catálogo sin líneas tienen cero registros observados y no entran en «top vendidos».
5. **Limitaciones:** popularidad por ingresos difiere de popularidad por unidades; el precio es estimado. Un pedido con varios productos pertenece a varios grupos y no se suman Nⱼ para obtener N total.
6. **Ejemplo:** enero: X=300 USD, 3 unidades, 2 pedidos; Y=200 USD, 4 unidades, 2 pedidos. Total global=500 USD, 7 unidades, 3 pedidos, no 4.

## K9. Ventas por sucursal y canal

1. **Definición:** ingresos estimados y unidades por StoreKey, agregables a canal según la clasificación aprobada del catálogo.
2. **Fórmula:** Rₛ=Σ rᵢ para sucursal s; R_canal=Σ rᵢ de las líneas del canal. Pedidos se cuentan distintos sobre cada selección.
3. **Campos de origen:** Sales.StoreKey, Quantity, ProductKey, Order Number, Order Date; Stores.StoreKey, Country y State; Products.Unit Price USD.
4. **Condiciones y exclusiones:** StoreKey=0 documentado como Online; conservarlo en totales y excluirlo solo de comparaciones exclusivamente físicas. No tratar Online como país ni mezclar país del cliente con país de tienda. Tiendas sin líneas se rotulan «sin registros de ventas».
5. **Limitaciones:** no hay detalle de ubicación física para Online ni información de operación de tiendas sin ventas. No deducir productividad neta o cierres.
6. **Ejemplo:** enero: T1/Físico=350 USD y 2 pedidos; Online=150 USD y 1 pedido; total=500 USD y 3 pedidos.

## K10. Margen estimado

1. **Definición:** diferencia entre ingresos valorados a precio de lista y costos de producir valorados con el catálogo; importe y porcentaje sobre ingresos.
2. **Fórmula:** K=Σ kᵢ; M=R−K; M%=100×M/R si R>0.
3. **Campos de origen:** Sales.Quantity y ProductKey; Products.Unit Price USD y Unit Cost USD; Order Date y dimensiones para la selección.
4. **Condiciones y exclusiones:** misma selección y versión de catálogo para R y K; no promediar márgenes porcentuales de líneas. Ingreso cero permite mostrar M pero no M%. Costo superior al precio genera advertencia; no se borra la línea por margen negativo.
5. **Limitaciones:** no es utilidad neta ni margen contable histórico. No hay costo de adquisición real, gastos operativos, logística, impuestos ni descuentos. Los precios/costos carecen de vigencia temporal.
6. **Ejemplo:** enero: K=120+60+20+60=260 USD; M=500−260=240 USD; M%=48.00%.

## Calidad de datos y publicación

**La clasificación siguiente es el contrato aprobado para el MVP**, no una afirmación de que el proveedor documente todas estas reglas. Las observaciones del snapshot se separan en data_profile.md.

### A. Errores críticos

Impiden interpretar el lote, resolver identidades o garantizar una publicación coherente. Bloquean la carga completa.

| Regla | Condición | Acción |
|---|---|---|
| C1 | Archivo esperado ausente/ilegible, CSV mal formado, columnas faltantes o adicionales sin contrato actualizado | Registrar fallo; no publicar |
| C2 | Clave natural duplicada en dimensión/tasa o pedido+línea duplicado en el lote, incluso idéntico | Bloquear; no deduplicar silenciosamente |
| C3 | Un pedido presenta cliente, fecha, moneda, tienda o entrega incompatibles entre sus líneas | Bloquear y conservar evidencia del pedido |
| C4 | Una unión multiplica líneas, o los conteos/unidades/importes no concilian | Bloquear; investigar cardinalidad y normalización |
| C5 | Fallo de conexión, transacción, restricción del destino o publicación concurrente no controlada | Revertir publicación; conservar versión anterior |
| C6 | Corrección de una clave existente, cambio monetario o eliminación implícita sin aprobación | Bloquear hasta resolver política de actualización |
| C7 | Subcategoría con múltiples categorías/nombres o categoría con nombres incompatibles en el mismo catálogo | Bloquear por ambigüedad de clasificación |
| C8 | Faltan archivos/lotes esperados según un manifiesto declarado, versión de reglas desconocida o tasas USD distintas de 1 bajo este contrato | Bloquear; no inferir éxito por tener algunas ventas |

No se considera crítico que el extracto termine en 2021: es histórico. No existe aún una obligación de frescura diaria.

### B. Registros rechazados

Defectos localizables; conservar referencia al original, clave cuando exista, regla y motivo. **Tolerancia inicial aprobada: cero rechazos para publicar.** Se identifican registros rechazados en staging, pero no se publica silenciosamente el resto.

| Regla | Condición | Acción |
|---|---|---|
| R1 | Identificador obligatorio vacío, mal formado o fuera del dominio aprobado; pedido/línea no positivos | Rechazar registro; StoreKey=0 sí es válido |
| R2 | Fecha obligatoria no interpretable, entrega informada anterior al pedido, pedido anterior al nacimiento/apertura | Rechazar y pedir revisión de la contradicción |
| R3 | Quantity vacío, no entero o ≤0 | Rechazar; esta fuente no documenta cantidades como devoluciones |
| R4 | Precio/costo obligatorio vacío, no interpretable o negativo | Rechazar producto/valoración; registrar impacto sobre ventas dependientes |
| R5 | Cliente, producto o tienda de la línea ausente del catálogo | Rechazar línea; no crear identidad ficticia de desconocido |
| R6 | Moneda de pedido vacía o par fecha/moneda sin tasa, tasa no numérica o ≤0 | Rechazar referencia/línea afectada según el contrato aprobado |
| R7 | Superficie informada no numérica o ≤0 | Rechazar atributo/registro; superficie online vacía no es error |

Un defecto de dimensión puede afectar múltiples líneas. Contabilizar por separado registros de origen rechazados y líneas dependientes, para no duplicar el conteo de entrada. Si una nueva moneda aparece con referencias válidas, requiere revisar el contrato; no fijar para siempre las cinco monedas actuales como universo del negocio.

### C. Advertencias

Permiten publicar si están registradas y visibles; las estructurales conocidas requieren aceptación inicial explícita del alcance.

| Regla | Condición | Acción |
|---|---|---|
| W1 | Entrega vacía en tienda física o superficie vacía en Online | Conservar nulo; documentar patrón esperado |
| W2 | Entrega online vacía o entrega física informada en una versión futura | Advertir cambio respecto al patrón; no fabricar valores |
| W3 | Line Item no consecutivo, producto repetido en distintas líneas del pedido | Conservar identidades y compras; no renumerar |
| W4 | Cliente/producto/tienda sin ventas, nombres personales repetidos | Conservar catálogo; no inferir abandono/cierre ni fusionar personas |
| W5 | Día sin registros, huecos históricos, período truncado | Señalar cobertura; no imputar actividad; restringir comparaciones |
| W6 | Precio cero, costo cero, costo superior al precio o edades poco habituales | Conservar si los tipos y relaciones son válidos; mostrar impacto |
| W7 | Categoría/género/color/atributo descriptivo nuevo o faltante no esencial | Advertir; no rechazar solo por estar fuera de la muestra conocida |
| W8 | Cambios descriptivos autorizados Tipo 1 o revaloración autorizada | Avisar que pueden cambiar análisis históricos; registrar versión |

«NA» de Napoli y espacios de formato monetario son casos de interpretación del origen, no defectos que deban producir rechazos. El precio cero deja ticket de un pedido en cero; si todos los ingresos son cero, el margen porcentual no es calculable.

### D. Anomalías comerciales: fuera del MVP

Se conserva la distinción conceptual: un valor atípico válido no se elimina, no modifica importes y no bloquea por sí solo. El MVP **no ejecuta detección estadística**, no calibra umbrales ni genera alertas comerciales. La pantalla de calidad muestra incidencias del pipeline y advertencias de cobertura, no un detector de anomalías.

### Puerta de publicación

Contrato para la primera versión:

1. Todos los archivos esperados y el contrato de columnas están disponibles.
2. Cero errores críticos y cero registros rechazados.
3. Unicidad, relaciones y consistencia por pedido satisfechas.
4. Conteos conciliados: entradas=aceptadas+rechazadas, sin descartar filas en silencio; con esta política, rechazadas=0 al publicar.
5. Hechos candidatos conservan las líneas aceptadas; uniones sin expansión; sumas monetarias exactas en centavos y unidades enteras conciliadas.
6. Primera carga de este snapshot: 62884 líneas, 26326 pedidos y 197757 unidades; valoración aprobada de 55755479.59 USD de ingresos y 23092791.21 USD de costos.
7. Hashes/versiones registrados y diferencias con publicaciones anteriores resueltas.
8. Advertencias conocidas aceptadas y accesibles; cobertura temporal etiquetada.
9. Confirmación atómica de datos e identidad de publicación; antes del commit no existe nueva versión visible. Los intentos fallidos o sin cambios no generan publication_id.
10. La lectura ya iniciada conserva su instantánea aunque se confirme otra publicación. Cada actualización posterior abre una nueva lectura; un fallo descarta el conjunto parcial completo.

Los valores del punto 6 son una referencia de **este snapshot**, no umbrales permanentes que impidan incorporar datos nuevos autorizados. Las diferencias deberán explicarse por el alcance declarado.

Los huecos ya presentes y los saltos de línea son advertencias, porque no existe evidencia de pérdida en el procesamiento local. Si un manifiesto futuro demuestra que falta un archivo esperado, esa ausencia sí es crítica. Un archivo vacío de ventas inesperado se bloquea hasta declarar y justificar su alcance.

## Verificación futura de reglas

Casos de aceptación funcional, todavía sin pruebas de aplicación implementadas:

| Caso | Resultado esperado |
|---|---|
| Ejemplo didáctico, enero | R=500; pedidos=3; unidades=7; ticket=166.67; nuevos=2; recurrentes=1; margen=240, 48% |
| Ejemplo didáctico, filtro X | R=300; pedidos=2; unidades=3; ticket=150 |
| Ejemplo didáctico, febrero cerrado | Crecimiento=−60%; nuevos=0; recurrentes=0 |
| Período sin líneas | Sumas/conteos=0; ticket y margen %=no calculable |
| Mes previo con ingreso cero | Crecimiento=no calculable |
| Varias líneas del mismo pedido | Un solo pedido y una sola compra por cliente |
| Cliente con primera compra fuera del filtro de producto | No se convierte artificialmente en nuevo |
| Pedido online con StoreKey=0 | Canal Online válido, sin superficie física inventada |
| Campo State Code=NA | Se conserva NA |
| Precio/costo de catálogo cambiado | Bloqueo hasta aprobar revaloración |
| Fallo durante publicación | Dashboard conserva la versión anterior |
| Reejecución idéntica a la publicación vigente | Mismos resultados e identidades, publication_id vigente sin cambios |
| Falla una consulta intermedia del dashboard | Ningún resultado parcial reemplaza la actualización completa anterior |
| Publicación B entre consultas de una lectura A | Filtros, KPIs, gráficos y metadatos siguen siendo de A; una nueva lectura puede ver B |

Los controles del perfil sí se ejecutaron sobre archivos. Las pruebas de PostgreSQL, ETL y Streamlit de esta tabla no se han ejecutado porque esas funcionalidades no existen todavía.

## Decisiones aprobadas

| ID | Decisión aprobada | Consecuencia |
|---|---|---|
| D1 | Modelo estrella de cuatro dimensiones, hecho por línea y referencia diaria de tasas | Sin tabla adicional de pedidos, canal, moneda o categorías |
| D2 | KPIs monetarios estimados en USD usando catálogo; conversión local pendiente de evidencia | No se presentan como cobros, ventas netas o utilidad contable |
| D3 | Nuevos=primera compra observada; recurrentes=dos o más pedidos en período/filtros | Pueden solaparse; no son segmentos excluyentes |
| D4 | Corte de pedidos 2021-02-20; huecos sin imputación, comparación mensual descriptiva con advertencias | Febrero de 2021 sin MoM estándar; completitud comercial no afirmada |
| D5 | Publicación atómica con cero rechazos; aceptar expresamente advertencias de la base histórica | Ninguna carga parcialmente válida modifica el dashboard |
| D6 | Dimensiones Tipo 1 y conflictos bloqueados; revaloración histórica explícitamente aprobada si cambia catálogo monetario | Auditoría de versiones; sin SCD2 ni vigencias ficticias |

D1–D6 están aprobadas. El MVP y su contrato técnico se delimitan en [architecture.md](architecture.md); sus entregas están en [implementation_plan.md](implementation_plan.md). La ejecución del plan requiere autorización posterior y expresa. No quedan pendientes estas seis decisiones; sí la evidencia de origen sobre Exchange, huecos y numeración de líneas.
