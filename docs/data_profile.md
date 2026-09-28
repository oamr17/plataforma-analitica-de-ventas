# Perfil del dataset — Plataforma Analítica de Ventas

Estado: análisis de los archivos locales; las políticas propuestas requieren aprobación.
Fecha de revisión: 2026-09-17.

## 1. Alcance y método

Se consultó primero [el diccionario original](../Data/Data_Dictionary.csv). Se recorrieron íntegramente los seis CSV de [Data](../Data), sin modificar sus bytes. Se utilizaron consultas de lectura y cálculos en memoria con PowerShell/.NET; no se generaron programas Python, SQL, ETL ni componentes de aplicación.

Las comprobaciones abarcan estructura CSV, vacíos, cardinalidades, tipos interpretables, claves, duplicados exactos, relaciones, consistencia por pedido, fechas, monedas y medidas. Los importes se calcularon con aritmética decimal; una segunda agregación por producto corroboró los totales calculados por línea. Los resultados no acreditan que el extracto contenga todas las operaciones del negocio.

Convenciones: **comprobado** = observado en los archivos o declarado por el diccionario; **hipótesis** = interpretación no demostrada; **propuesta** = decisión pendiente. Las tablas numéricas usan punto decimal, sin separador de miles.

## 2. Inventario y estructura

| Archivo | Registros | Columnas | Grano observado | Lectura usada |
|---|---:|---:|---|---|
| Customers.csv | 15266 | 10 | Cliente por CustomerKey | Windows-1252 |
| Products.csv | 2517 | 10 | Producto por ProductKey | UTF-8 |
| Stores.csv | 67 | 5 | Entrada de tienda/canal por StoreKey | UTF-8 |
| Sales.csv | 62884 | 9 | Línea por Order Number + Line Item | UTF-8 |
| Exchange_Rates.csv | 11215 | 3 | Fecha + moneda | UTF-8 |
| Data_Dictionary.csv | 37 | 3 | Campo por Table + Field | UTF-8 |

Comprobado: los CSV tienen delimitador coma y admiten campos entrecomillados; no se encontraron filas con número de campos distinto del encabezado. Los 37 campos de las cinco tablas de negocio coinciden con el diccionario, sin faltantes ni definiciones duplicadas. El nombre lógico «Exchange Rates» corresponde a Exchange_Rates.csv. Los tres campos del propio diccionario se documentan aparte en [data_dictionary.md](data_dictionary.md).

CSV no almacena tipos de base de datos: todos los tipos de [data_dictionary.md](data_dictionary.md) son interpretaciones verificadas/propuestas. Todas las fechas no vacías se interpretan con mes/día/año; no hay horas ni zonas horarias. Customers.csv no debe forzarse a UTF-8: la lectura Windows-1252 preserva sus caracteres. Esa compatibilidad no constituye una declaración de codificación del proveedor.

## 3. Completitud y significado de vacíos

| Campo | Vacíos | % de su tabla | Evidencia e interpretación |
|---|---:|---:|---|
| Sales.Delivery Date | 49719 | 79.0646% | Todas las líneas físicas están vacías; las 13165 online tienen fecha |
| Stores.Square Meters | 1 | 1.4925% | Corresponde únicamente a StoreKey=0, Country=State=Online |
| Los otros 38 campos de los seis CSV | 0 | 0% | Sin vacíos ni cadenas compuestas solo por espacios |

**Comprobado:** 10 clientes tienen State Code=NA, todos con Country=Italy y State=Napoli. No es un vacío; una conversión genérica de «NA» a nulo destruiría información. Propuesta: interpretar solo campos realmente vacíos como nulos en esta fuente.

**Hipótesis:** la fecha de entrega no aplica a compras físicas. El patrón es total, pero el diccionario no explica por qué falta. Propuesta: conservar el nulo, sin inventar fecha de entrega ni excluir la venta.

Comprobado: los 2517 precios y los 2517 costos contienen espacios exteriores; no se hallaron espacios exteriores en otros valores no vacíos. La normalización de lectura deberá retirar esos espacios y respetar símbolos monetarios, comas de miles y punto decimal. El original permanece intacto.

## 4. Claves, duplicados e integridad

| Control | Resultado comprobado |
|---|---|
| Filas exactas duplicadas | 0 en cada uno de los seis archivos |
| CustomerKey / ProductKey / StoreKey duplicados | 0 / 0 / 0 |
| Order Number + Line Item duplicados | 0 |
| Date + Currency duplicados | 0 |
| Table + Field duplicados en el diccionario | 0 |
| Ventas sin cliente / producto / tienda correspondiente | 0 / 0 / 0 |
| Ventas sin tasa para fecha del pedido + moneda | 0 |
| Pedidos con más de un cliente, fecha, tienda, moneda o fecha de entrega | 0 para cada atributo |
| CategoryKey con nombres incompatibles | 0 |
| SubcategoryKey con nombres o categoría padre incompatibles | 0 |

El número de pedido tiene 26326 valores distintos; no es clave de línea. Los nombres de personas no son claves: hay 15118 nombres distintos para 15266 clientes.

Hay 11887 clientes, 2492 productos y 58 entradas de tienda/canal referenciadas en ventas. Por diferencia, 3379 clientes, 25 productos y 9 tiendas no tienen ventas en este extracto. No son registros inválidos ni demuestran inactividad fuera de él. Las tiendas sin ventas tienen StoreKey 3, 7, 11, 25, 35, 46, 52, 58 y 60.

**Advertencias comprobadas:**

- 621 pedidos (2.3589%) presentan huecos internos en Line Item. Todos comienzan en 1. Ejemplo: pedido 1008000, líneas 1, 2 y 4. No se conoce si faltan registros o si la numeración original admite saltos. No renumerar ni completar filas.
- 75 pedidos repiten ProductKey en líneas diferentes. La clave continúa siendo pedido + línea; deduplicar por pedido + producto eliminaría compras registradas.
- La comparación de claves es por identificador, no por similitud de nombres o direcciones; no se ha hecho resolución de identidades.

## 5. Distribución temporal

Comprobado: pedidos entre 2016-01-01 y 2021-02-20, 62 meses representados, 1641 días con ventas de 1878 días calendario posibles. Hay 237 días sin registros; ninguna tabla de calendario operativo o manifiesto de extracción permite clasificarlos como cero ventas reales.

| Año | Líneas | Pedidos distintos | Unidades | Días con ventas |
|---|---:|---:|---:|---:|
| 2016 | 6905 | 2865 | 21761 | 309 |
| 2017 | 7942 | 3280 | 24798 | 321 |
| 2018 | 14188 | 5965 | 44498 | 326 |
| 2019 | 21611 | 9083 | 68440 | 330 |
| 2020 | 11026 | 4635 | 34463 | 309 |
| 2021, hasta 20 de febrero | 1212 | 498 | 3797 | 46 |
| Total | 62884 | 26326 | 197757 | 1641 |

Los cinco mayores intervalos sin ventas son:

| Inicio | Fin inclusive | Días |
|---|---|---:|
| 2016-03-18 | 2016-04-22 | 36 |
| 2017-03-19 | 2017-04-24 | 37 |
| 2018-03-18 | 2018-04-23 | 37 |
| 2019-03-20 | 2019-04-23 | 35 |
| 2020-03-19 | 2020-04-22 | 35 |

Se observa una recurrencia de estos huecos entre marzo y abril, y menos pedidos en 2020 que en 2019. Atribuirlos a cierres, pandemia, estacionalidad o errores de extracción sería una hipótesis sin evidencia suficiente. No entrenar ni calibrar alertas suponiendo que esos intervalos son ventas cero confirmadas.

Febrero de 2021 está truncado: máximo de pedidos el día 20, aunque existen entregas hasta el 27. Las fechas de entrega no amplían la cobertura de pedidos. Enero de 2016 carece de mes anterior; 2021 es un año parcial. Que un mes esté dentro del intervalo del extracto no demuestra completitud comercial.

Otras comprobaciones: las 13165 entregas informadas son entre 1 y 17 días después del pedido; ninguna precede al pedido. No hay compras anteriores al nacimiento del cliente ni a la apertura de su tienda. La edad calculada al comprar va de 14 a 85 años; 1335 líneas corresponden a menores de 18. No existe una regla documentada que permita rechazarlas por ese motivo.

## 6. Monedas y tasas

| Currency Code en ventas | Líneas | Pedidos | Tasas disponibles | Mínimo Exchange | Máximo Exchange |
|---|---:|---:|---:|---:|---:|
| AUD | 2941 | 1182 | 2243 | 1.2080 | 1.7253 |
| CAD | 5415 | 2281 | 2243 | 1.1583 | 1.4637 |
| EUR | 12621 | 5221 | 2243 | 0.8004 | 0.9649 |
| GBP | 8140 | 3421 | 2243 | 0.6285 | 0.8622 |
| USD | 33767 | 14221 | 2243 | 1.0000 | 1.0000 |

Comprobado: tasas diarias de 2015-01-01 a 2021-02-20, sin huecos en sus 2243 fechas, cinco monedas por fecha; tasas positivas y hasta cuatro decimales. USD siempre vale 1. El enlace por fecha de pedido y moneda encuentra exactamente una tasa por línea.

El diccionario define Exchange como «Exchange rate compared to USD», sin especificar explícitamente numerador, denominador, momento de cotización, proveedor ni método. **Hipótesis:** Exchange expresa unidades de moneda local por USD. Los archivos por sí solos no bastan para certificar esa dirección.

**Propuesta:** los KPIs principales usan directamente precios y costos USD del catálogo; no se aplica ninguna tasa a esas cantidades ya denominadas en USD. Conservar las tasas como referencia. La conversión local queda pendiente de metadatos o confirmación del proveedor. Si se confirma tasa moneda/USD, local=USD×tasa y USD=local/tasa; si la convención es inversa, las operaciones se invierten. Nunca sumar importes de monedas diferentes sin una base común.

## 7. Precios, costos y cantidades

| Medida | Rango observado | Problemas comprobados |
|---|---|---|
| Quantity | Enteros 1–10 | 0 no positivos o mal formados |
| Unit Price USD | 0.95–3199.99 | 0 no positivos o no interpretables |
| Unit Cost USD | 0.48–1060.22 | 0 no positivos o no interpretables |
| Costos superiores al precio del mismo producto | — | 0 productos |
| Ingreso estimado por línea | Máximo 31999.90 USD | Un valor alto no prueba error |

Todos los importes del catálogo tienen dos decimales. Cuartiles del ingreso estimado por línea: 115.00, 360.00 y 969.00 USD; percentil 95: 3329.70 USD. Cuantiles calculados sobre las 62884 líneas, interpolación lineal entre posiciones. La distribución tiene cola alta; estos valores describen la muestra y no definen límites de validez.

| Cantidad por línea | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Líneas | 18311 | 13611 | 10907 | 5441 | 3619 | 3699 | 3639 | 1849 | 895 | 913 |

**Limitaciones comprobadas:** no hay precio cobrado por transacción, historial de precios/costos, descuentos, impuestos, devoluciones, cancelaciones, gastos logísticos ni gastos operativos. El costo se describe como costo de producir el producto. Por tanto, no puede afirmarse ingreso contable, venta neta efectiva, costo histórico de venta ni utilidad neta.

### Totales de referencia bajo la valoración propuesta

Unión muchos-a-uno por ProductKey, sin excluir líneas y sin conversión de moneda:

| Medida calculada | Resultado |
|---|---:|
| Unidades | 197757 |
| Ingresos estimados, cantidad × precio de lista USD | 55755479.59 USD |
| Costos estimados, cantidad × costo del catálogo USD | 23092791.21 USD |
| Margen estimado, ingresos menos costos | 32662688.38 USD |
| Margen estimado / ingresos | 58.582024% |
| Ticket estimado, ingresos / 26326 pedidos | 2117.886484 USD |
| Clientes con compra | 11887 |
| Clientes con al menos dos pedidos en todo el extracto | 7272 |

Estos son cálculos de referencia comprobados sobre el snapshot; su uso como KPIs queda sujeto a aprobar las definiciones. Los 7272 clientes no equivalen a clientes que ya existían antes de 2016.

## 8. Decisiones pendientes y calidad

La matriz de acciones y bloqueos está en [business_rules.md](business_rules.md#calidad-de-datos-y-publicación). El modelo propuesto está en [data_model.md](data_model.md).

Pendiente de aprobación: valoración de catálogo en USD, definición de recurrencia, aceptación explícita de los huecos históricos, política de correcciones y publicación sin rechazos. Pendiente de evidencia externa: dirección de Exchange, significado de los saltos de línea y causa de los huecos. Una aprobación del diseño no convierte hipótesis en hechos.

No se puede evaluar frescura operativa, cambios de esquema entre versiones, retraso de ingestión, actualizaciones tardías o reconciliación contra el sistema original: solo se dispone de un snapshot y no hay marcas de extracción/actualización.

## 9. Identidad de los originales

SHA-256 obtenido antes de documentar; sirve para comprobar que las fuentes permanecen intactas.

| Archivo | SHA-256 |
|---|---|
| Customers.csv | B508818AC86FAE2059BD315C38A76396C3BB2FCBDFCE3217A1C436BF2A4638EB |
| Data_Dictionary.csv | 69611363F84302D1D4B294EE4A29B9C6DDEB68F6634CC630FCF9F266EC174769 |
| Exchange_Rates.csv | 9B46644A0035DAE4D6FED174523A2B2DEDCC733157C78AF6B57A839E0DEA39B8 |
| Products.csv | D6BA42DB0AA64C547A2F1F5A3E73CC19E6B1260C4702767A003A11CA2927A4FE |
| Sales.csv | 15CE9165098227714A2470341495855B55D31539773B85B86AC68A6DEE6F9A53 |
| Stores.csv | 21EBB499C905037CCF12A3602D2C47C4BF6C5EF706FBA3A4FBC6EBA34E5CBDF0 |

## Anexo: distribución mensual completa

«Días» cuenta fechas distintas con registros, no días de apertura. Unidades y pedidos no son ingresos. Febrero de 2021 solo cubre hasta el día 20; el resto conserva las limitaciones de cobertura descritas.

| Mes | Líneas | Pedidos | Unidades | Días |
|---|---:|---:|---:|---:|
| 2016-01 | 666 | 292 | 2116 | 28 |
| 2016-02 | 815 | 334 | 2676 | 28 |
| 2016-03 | 279 | 109 | 895 | 15 |
| 2016-04 | 106 | 41 | 326 | 7 |
| 2016-05 | 501 | 220 | 1646 | 26 |
| 2016-06 | 570 | 243 | 1864 | 29 |
| 2016-07 | 512 | 201 | 1572 | 29 |
| 2016-08 | 526 | 229 | 1611 | 29 |
| 2016-09 | 605 | 262 | 1885 | 29 |
| 2016-10 | 628 | 258 | 2042 | 31 |
| 2016-11 | 638 | 247 | 1919 | 28 |
| 2016-12 | 1059 | 429 | 3209 | 30 |
| 2017-01 | 754 | 292 | 2282 | 31 |
| 2017-02 | 811 | 356 | 2478 | 28 |
| 2017-03 | 322 | 130 | 1045 | 18 |
| 2017-04 | 54 | 26 | 161 | 5 |
| 2017-05 | 634 | 256 | 1959 | 30 |
| 2017-06 | 611 | 271 | 1945 | 29 |
| 2017-07 | 555 | 240 | 1705 | 29 |
| 2017-08 | 653 | 263 | 2008 | 30 |
| 2017-09 | 716 | 286 | 2184 | 30 |
| 2017-10 | 733 | 288 | 2286 | 31 |
| 2017-11 | 751 | 323 | 2431 | 29 |
| 2017-12 | 1348 | 549 | 4314 | 31 |
| 2018-01 | 1035 | 439 | 3216 | 31 |
| 2018-02 | 1268 | 513 | 4040 | 27 |
| 2018-03 | 463 | 193 | 1449 | 17 |
| 2018-04 | 95 | 43 | 258 | 7 |
| 2018-05 | 1181 | 464 | 3698 | 31 |
| 2018-06 | 1106 | 474 | 3494 | 30 |
| 2018-07 | 1052 | 446 | 3379 | 30 |
| 2018-08 | 1223 | 508 | 3684 | 31 |
| 2018-09 | 1334 | 561 | 4254 | 30 |
| 2018-10 | 1434 | 606 | 4476 | 31 |
| 2018-11 | 1477 | 629 | 4713 | 30 |
| 2018-12 | 2520 | 1089 | 7837 | 31 |
| 2019-01 | 2154 | 892 | 6850 | 31 |
| 2019-02 | 2323 | 967 | 7383 | 28 |
| 2019-03 | 921 | 386 | 3095 | 19 |
| 2019-04 | 174 | 75 | 562 | 7 |
| 2019-05 | 1918 | 808 | 5966 | 31 |
| 2019-06 | 1747 | 772 | 5497 | 30 |
| 2019-07 | 1713 | 716 | 5328 | 31 |
| 2019-08 | 1893 | 793 | 5922 | 31 |
| 2019-09 | 1909 | 781 | 6037 | 30 |
| 2019-10 | 2002 | 842 | 6412 | 31 |
| 2019-11 | 1890 | 801 | 6045 | 30 |
| 2019-12 | 2967 | 1250 | 9343 | 31 |
| 2020-01 | 2457 | 1027 | 7706 | 31 |
| 2020-02 | 2652 | 1124 | 8385 | 29 |
| 2020-03 | 775 | 332 | 2392 | 18 |
| 2020-04 | 211 | 80 | 694 | 7 |
| 2020-05 | 1107 | 455 | 3256 | 31 |
| 2020-06 | 863 | 357 | 2738 | 30 |
| 2020-07 | 609 | 253 | 1941 | 30 |
| 2020-08 | 488 | 201 | 1526 | 26 |
| 2020-09 | 409 | 180 | 1328 | 26 |
| 2020-10 | 338 | 159 | 1042 | 27 |
| 2020-11 | 356 | 149 | 1044 | 25 |
| 2020-12 | 761 | 318 | 2411 | 29 |
| 2021-01 | 617 | 256 | 1848 | 27 |
| 2021-02 | 595 | 242 | 1949 | 19 |

