# Diccionario de datos — Sales Analytics Platform

Estado: significado del origen verificado; tipos de destino y transformaciones propuestos.
Fuente principal: [Data_Dictionary.csv](../Data/Data_Dictionary.csv), leído antes de interpretar las columnas. Perfil de referencia: [data_profile.md](data_profile.md).

## Convenciones

- El CSV contiene texto; «tipo» indica el tipo semántico observado y el destino PostgreSQL propuesto, no un esquema declarado por el proveedor.
- Vacíos: campos ausentes o compuestos solo por espacios; «NA» se conserva como texto.
- Distintos: valores no vacíos, comparados exactamente. Las claves se validaron además por sus relaciones.
- Todas las fechas son M/d/yyyy en origen y se proponen como DATE; no se atribuye zona horaria.
- Los importes USD son NUMERIC(18,2), nunca coma flotante. La tasa se propone NUMERIC(18,8): los datos actuales usan hasta cuatro decimales.
- «PK candidata» refleja unicidad observada; las claves sustitutas del modelo se describen en [data_model.md](data_model.md).

## Sales.csv — 62884 filas

Grano: una línea de pedido. PK candidata compuesta: Order Number + Line Item. El diccionario describe el número de pedido como identificador del pedido, no de cada fila.

| Campo original | Significado del diccionario | Tipo semántico / destino propuesto | Vacíos | Distintos | Observaciones |
|---|---|---|---:|---:|---|
| Order Number | Identificador único del pedido | Entero / BIGINT | 0 | 26326 | Rango 366000–2243032; dimensión degenerada en el hecho |
| Line Item | Identifica productos individuales dentro del pedido | Entero / INTEGER | 0 | 7 | Valores 1–7; admite huecos observados; no es Quantity |
| Order Date | Fecha en que se realizó el pedido | Fecha / DATE | 0 | 1641 | 2016-01-01 a 2021-02-20; fecha canónica de ventas |
| Delivery Date | Fecha en que se entregó el pedido | Fecha opcional / DATE | 49719 | 1492 | Informada solo para StoreKey=0; 2016-01-06 a 2021-02-27 |
| CustomerKey | Cliente que realizó el pedido | Identificador entero / INTEGER | 0 | 11887 | Referencia Customers.CustomerKey |
| StoreKey | Tienda que procesó el pedido | Identificador entero / INTEGER | 0 | 58 | Referencia Stores.StoreKey; 0 existe y representa Online |
| ProductKey | Producto comprado | Identificador entero / INTEGER | 0 | 2492 | Referencia Products.ProductKey |
| Quantity | Número de artículos comprados | Entero / INTEGER | 0 | 10 | 1–10; se suma para unidades |
| Currency Code | Moneda utilizada al procesar el pedido | Código de texto / TEXT | 0 | 5 | AUD, CAD, EUR, GBP, USD; no redefine la unidad USD del catálogo |

Relaciones: muchos-a-uno con Customers, Products y Stores; con Exchange_Rates mediante Order Date=Date y Currency Code=Currency. Se verificó unicidad en los destinos y cobertura de todas las líneas. No unir tasas únicamente por moneda.

No hay columnas de importe, precio transaccional, descuento, estado de pedido o devolución.

## Customers.csv — 15266 filas

Grano: un cliente por CustomerKey; PK candidata CustomerKey. La identidad se conserva aunque coincidan nombres o direcciones.

| Campo original | Significado del diccionario | Tipo semántico / destino propuesto | Vacíos | Distintos | Observaciones |
|---|---|---|---:|---:|---|
| CustomerKey | Clave primaria del cliente | Identificador entero / INTEGER | 0 | 15266 | 301–2099937; no derivar identidad de Name |
| Gender | Género del cliente | Texto / TEXT | 0 | 2 | Female=7518, Male=7748; valores observados, no universo impuesto |
| Name | Nombre completo | Texto / TEXT | 0 | 15118 | Repetición permitida |
| City | Ciudad del cliente | Texto / TEXT | 0 | 8258 | No es una clave geográfica global |
| State Code | Abreviatura del estado | Texto / TEXT | 0 | 468 | NA aparece 10 veces en Italy/Napoli; conservar |
| State | Nombre completo del estado | Texto / TEXT | 0 | 512 | Interpretar junto con Country |
| Zip Code | Código postal | Texto / TEXT | 0 | 9505 | 4230 filas no son exclusivamente dígitos; no convertir a entero |
| Country | País del cliente | Texto / TEXT | 0 | 8 | Geografía del cliente, diferente de la tienda |
| Continent | Continente del cliente | Texto / TEXT | 0 | 3 | No reconstruir mediante reglas geográficas externas |
| Birthday | Fecha de nacimiento | Fecha / DATE | 0 | 11270 | 1935-02-03 a 2002-02-18; no es fecha de alta |

Países y clientes: Australia 1420; Canada 1553; France 670; Germany 1473; Italy 645; Netherlands 733; United Kingdom 1944; United States 6828.

No se dispone de fecha de registro, primera compra anterior al extracto, historial de domicilio ni identificador alternativo de cliente. «Nuevo» solo puede significar primera compra observada.

## Products.csv — 2517 filas

Grano: un producto por ProductKey; PK candidata ProductKey.

| Campo original | Significado del diccionario | Tipo semántico / destino propuesto | Vacíos | Distintos | Observaciones |
|---|---|---|---:|---:|---|
| ProductKey | Clave primaria del producto | Identificador entero / INTEGER | 0 | 2517 | Valores 1–2517 |
| Product Name | Nombre del producto | Texto / TEXT | 0 | 2517 | Unicidad observada; no usarlo como identidad |
| Brand | Marca | Texto / TEXT | 0 | 11 | Atributo de producto |
| Color | Color | Texto / TEXT | 0 | 16 | Atributo de producto |
| Unit Cost USD | Costo de producir el producto en USD | Decimal monetario / NUMERIC(18,2) | 0 | 480 | 0.48–1060.22; costo de catálogo, sin vigencia histórica |
| Unit Price USD | Precio de lista en USD | Decimal monetario / NUMERIC(18,2) | 0 | 426 | 0.95–3199.99; no equivale a importe efectivamente cobrado |
| SubcategoryKey | Clave de subcategoría | Código de texto / TEXT | 0 | 32 | Cuatro caracteres; conservar ceros iniciales, p. ej. 0101 |
| Subcategory | Nombre de subcategoría | Texto / TEXT | 0 | 32 | Una etiqueta y categoría padre por código en este snapshot |
| CategoryKey | Clave de categoría | Código de texto / TEXT | 0 | 8 | Dos caracteres; conservar ceros iniciales, p. ej. 01 |
| Category | Nombre de categoría | Texto / TEXT | 0 | 8 | Una etiqueta por código en este snapshot |

Los 5034 valores de precio/costo contienen espacios exteriores y símbolos de moneda; usar la convención estadounidense del archivo al interpretarlos. Todos son positivos; ningún costo supera el precio de su producto.

Categorías y productos: Audio 115; Cameras and camcorders 372; Cell phones 285; Computers 606; Games and Toys 166; Home Appliances 661; Music, Movies and Audio Books 90; TV and Video 222.

Propuesta: categoría, subcategoría, marca y color se mantienen como atributos de dim_producto; no necesitan tablas independientes para estos requisitos.

## Stores.csv — 67 filas

Grano: una entrada por StoreKey; PK candidata StoreKey. Hay 66 tiendas físicas y una entrada Online.

| Campo original | Significado del diccionario | Tipo semántico / destino propuesto | Vacíos | Distintos | Observaciones |
|---|---|---|---:|---:|---|
| StoreKey | Clave primaria de tienda | Identificador entero / INTEGER | 0 | 67 | 0–66; cero no es desconocido |
| Country | País de la tienda | Texto / TEXT | 0 | 9 | Ocho países y el literal Online |
| State | Estado de la tienda | Texto / TEXT | 0 | 67 | Para StoreKey=0 contiene Online |
| Square Meters | Superficie en metros cuadrados | Entero opcional / INTEGER | 1 | 36 | Físicas: 245–2105; vacío solo en Online |
| Open Date | Fecha de apertura | Fecha / DATE | 0 | 25 | 2005-03-04 a 2019-03-05; Online tiene 2010-01-01 |

Propuesta: conservar los literales originales, derivar canal de la entrada Online y presentar su ubicación física como «No aplica». No asignar a Online un país tomado del cliente ni incluirlo en promedios de superficie.

## Exchange_Rates.csv — 11215 filas

Grano: una tasa por fecha y moneda; PK candidata compuesta Date + Currency.

| Campo original | Significado del diccionario | Tipo semántico / destino propuesto | Vacíos | Distintos | Observaciones |
|---|---|---|---:|---:|---|
| Date | Fecha | Fecha / DATE | 0 | 2243 | 2015-01-01 a 2021-02-20; una fila por moneda cada día |
| Currency | Código de moneda | Texto / TEXT | 0 | 5 | AUD, CAD, EUR, GBP, USD |
| Exchange | Tasa comparada con USD | Decimal / NUMERIC(18,8) | 0 | 3473 | Positiva; USD=1; hasta cuatro decimales observados |

La dirección matemática no está explicitada en el diccionario. El modelo conserva la tasa original sin transformarla; no está autorizada su interpretación como moneda/USD o USD/moneda hasta confirmar la convención. No usar tasas actuales para valorar fechas históricas.

## Data_Dictionary.csv — 37 filas

Este archivo describe las cinco tablas anteriores; sus propios campos no tienen entradas dentro de él. Las siguientes descripciones se basan en su estructura observada.

| Campo original | Significado observado | Tipo | Vacíos | Distintos |
|---|---|---|---:|---:|
| Table | Nombre lógico de la tabla descrita | Texto | 0 | 5 |
| Field | Nombre de la columna descrita | Texto | 0 | 32 |
| Description | Descripción del campo | Texto | 0 | 37 |

PK candidata: Table + Field; Field solo no es único. No requiere una dimensión de negocio ni una tabla analítica. Se conserva como documentación de origen y su hash identifica la versión del contrato revisado.

## Separación entre origen y derivados

Los archivos no contienen canal, claves sustitutas, run_id, precio aplicado al hecho, importe de línea, primera compra, estado de cobertura ni etiquetas de calidad. Son **campos técnicos o derivados propuestos**, definidos en [data_model.md](data_model.md) y [business_rules.md](business_rules.md); nunca deben presentarse como columnas recibidas del proveedor.

La primera compra se calcula sobre el histórico publicado; la fecha de nacimiento no la sustituye. La fecha de entrega no sustituye la fecha del pedido. Los códigos postales y de categorías se conservan como texto.

