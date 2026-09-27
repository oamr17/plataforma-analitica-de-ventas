-- Esquema inicial de E2. Ejecutar una sola vez como propietario, en transacción.
-- No carga filas, crea roles ni implementa validación/publicación del ETL.
CREATE SCHEMA staging;
CREATE SCHEMA dw;
CREATE SCHEMA marts;
CREATE SCHEMA ops;

CREATE TABLE ops.ejecuciones (
    run_id UUID PRIMARY KEY,
    estado TEXT NOT NULL DEFAULT 'en_curso'
        CHECK (estado IN ('en_curso', 'fallido', 'interrumpido', 'sin_cambios', 'publicado')),
    publication_id BIGINT UNIQUE CHECK (publication_id > 0),
    version_reglas TEXT NOT NULL,
    modo TEXT NOT NULL DEFAULT 'snapshot_completo' CHECK (modo = 'snapshot_completo'),
    archivos JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(archivos) = 'object'),
    conteos JSONB NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(conteos) = 'object'),
    fecha_pedido_min DATE,
    fecha_pedido_max DATE,
    inicio TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fin TIMESTAMPTZ,
    equipo TEXT NOT NULL,
    pid INTEGER NOT NULL CHECK (pid > 0),
    inicio_proceso TIMESTAMPTZ NOT NULL,
    fase TEXT NOT NULL DEFAULT 'inicio',
    ultimo_avance TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK ((estado = 'publicado') = (publication_id IS NOT NULL)),
    CHECK (fecha_pedido_max >= fecha_pedido_min),
    CHECK (fin >= inicio)
);

-- archivos conserva rutas, hashes, estado de lectura y conteos por archivo.
-- Sin secuencia/default: el publicador asigna publication_id bajo exclusión.
CREATE TABLE ops.incidencias (
    incidencia_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ops.ejecuciones (run_id),
    archivo TEXT,
    numero_registro_origen BIGINT CHECK (numero_registro_origen > 0),
    registro_fin BIGINT CHECK (registro_fin >= numero_registro_origen),
    regla TEXT NOT NULL,
    clasificacion TEXT NOT NULL
        CHECK (clasificacion IN ('error_critico', 'rechazo', 'advertencia')),
    motivo TEXT NOT NULL,
    evidencia TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Las columnas originales son texto sin conversión; '' conserva un vacío CSV.
-- Solo run_id y ordinal tienen restricciones técnicas. No hay claves de negocio.
CREATE TABLE staging.customers (
    run_id UUID NOT NULL REFERENCES ops.ejecuciones (run_id),
    numero_registro_origen BIGINT NOT NULL CHECK (numero_registro_origen > 0),
    "CustomerKey" TEXT NOT NULL,
    "Gender" TEXT NOT NULL,
    "Name" TEXT NOT NULL,
    "City" TEXT NOT NULL,
    "State Code" TEXT NOT NULL,
    "State" TEXT NOT NULL,
    "Zip Code" TEXT NOT NULL,
    "Country" TEXT NOT NULL,
    "Continent" TEXT NOT NULL,
    "Birthday" TEXT NOT NULL,
    PRIMARY KEY (run_id, numero_registro_origen)
);

CREATE TABLE staging.products (
    run_id UUID NOT NULL REFERENCES ops.ejecuciones (run_id),
    numero_registro_origen BIGINT NOT NULL CHECK (numero_registro_origen > 0),
    "ProductKey" TEXT NOT NULL,
    "Product Name" TEXT NOT NULL,
    "Brand" TEXT NOT NULL,
    "Color" TEXT NOT NULL,
    "Unit Cost USD" TEXT NOT NULL,
    "Unit Price USD" TEXT NOT NULL,
    "SubcategoryKey" TEXT NOT NULL,
    "Subcategory" TEXT NOT NULL,
    "CategoryKey" TEXT NOT NULL,
    "Category" TEXT NOT NULL,
    PRIMARY KEY (run_id, numero_registro_origen)
);

CREATE TABLE staging.stores (
    run_id UUID NOT NULL REFERENCES ops.ejecuciones (run_id),
    numero_registro_origen BIGINT NOT NULL CHECK (numero_registro_origen > 0),
    "StoreKey" TEXT NOT NULL,
    "Country" TEXT NOT NULL,
    "State" TEXT NOT NULL,
    "Square Meters" TEXT NOT NULL,
    "Open Date" TEXT NOT NULL,
    PRIMARY KEY (run_id, numero_registro_origen)
);

CREATE TABLE staging.sales (
    run_id UUID NOT NULL REFERENCES ops.ejecuciones (run_id),
    numero_registro_origen BIGINT NOT NULL CHECK (numero_registro_origen > 0),
    "Order Number" TEXT NOT NULL,
    "Line Item" TEXT NOT NULL,
    "Order Date" TEXT NOT NULL,
    "Delivery Date" TEXT NOT NULL,
    "CustomerKey" TEXT NOT NULL,
    "StoreKey" TEXT NOT NULL,
    "ProductKey" TEXT NOT NULL,
    "Quantity" TEXT NOT NULL,
    "Currency Code" TEXT NOT NULL,
    PRIMARY KEY (run_id, numero_registro_origen)
);

CREATE TABLE staging.exchange_rates (
    run_id UUID NOT NULL REFERENCES ops.ejecuciones (run_id),
    numero_registro_origen BIGINT NOT NULL CHECK (numero_registro_origen > 0),
    "Date" TEXT NOT NULL,
    "Currency" TEXT NOT NULL,
    "Exchange" TEXT NOT NULL,
    PRIMARY KEY (run_id, numero_registro_origen)
);

CREATE TABLE dw.dim_fecha (
    fecha DATE PRIMARY KEY,
    anio INTEGER GENERATED ALWAYS AS (EXTRACT(YEAR FROM fecha)::INTEGER) STORED,
    mes_numero INTEGER GENERATED ALWAYS AS (EXTRACT(MONTH FROM fecha)::INTEGER) STORED,
    trimestre INTEGER GENERATED ALWAYS AS (EXTRACT(QUARTER FROM fecha)::INTEGER) STORED,
    inicio_mes DATE GENERATED ALWAYS AS
        (fecha - (EXTRACT(DAY FROM fecha)::INTEGER - 1)) STORED,
    fin_mes DATE GENERATED ALWAYS AS
        ((date_trunc('month', fecha::TIMESTAMP) + INTERVAL '1 month - 1 day')::DATE) STORED
);

CREATE TABLE dw.dim_cliente (
    cliente_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_key INTEGER NOT NULL UNIQUE,
    genero TEXT,
    nombre TEXT,
    ciudad TEXT,
    codigo_estado TEXT,
    estado TEXT,
    codigo_postal TEXT,
    pais TEXT,
    continente TEXT,
    fecha_nacimiento DATE NOT NULL
);

CREATE TABLE dw.dim_producto (
    producto_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_key INTEGER NOT NULL UNIQUE,
    nombre TEXT,
    marca TEXT,
    color TEXT,
    subcategoria_codigo TEXT,
    subcategoria TEXT,
    categoria_codigo TEXT,
    categoria TEXT,
    precio_catalogo_usd NUMERIC(18,2) NOT NULL
        CHECK (precio_catalogo_usd >= 0 AND precio_catalogo_usd < 'Infinity'::NUMERIC),
    costo_catalogo_usd NUMERIC(18,2) NOT NULL
        CHECK (costo_catalogo_usd >= 0 AND costo_catalogo_usd < 'Infinity'::NUMERIC)
);

CREATE TABLE dw.dim_sucursal (
    sucursal_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_key INTEGER NOT NULL UNIQUE,
    pais_origen TEXT,
    estado_origen TEXT,
    superficie_m2 INTEGER CHECK (superficie_m2 > 0),
    fecha_apertura DATE NOT NULL,
    canal TEXT NOT NULL CHECK (canal IN ('Online', 'Físico')),
    CHECK (store_key <> 0 OR canal = 'Online')
);

CREATE TABLE dw.tipos_cambio (
    fecha DATE NOT NULL REFERENCES dw.dim_fecha (fecha),
    moneda TEXT NOT NULL CHECK (length(btrim(moneda)) > 0),
    tasa_original NUMERIC(18,8) NOT NULL
        CHECK (tasa_original > 0 AND tasa_original < 'Infinity'::NUMERIC),
    PRIMARY KEY (fecha, moneda),
    CHECK (moneda <> 'USD' OR tasa_original = 1)
);

CREATE TABLE dw.fact_ventas (
    numero_pedido BIGINT NOT NULL CHECK (numero_pedido > 0),
    linea_pedido INTEGER NOT NULL CHECK (linea_pedido > 0),
    fecha_pedido DATE NOT NULL REFERENCES dw.dim_fecha (fecha),
    fecha_entrega DATE REFERENCES dw.dim_fecha (fecha),
    cliente_id BIGINT NOT NULL REFERENCES dw.dim_cliente (cliente_id),
    producto_id BIGINT NOT NULL REFERENCES dw.dim_producto (producto_id),
    sucursal_id BIGINT NOT NULL REFERENCES dw.dim_sucursal (sucursal_id),
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    moneda_pedido TEXT NOT NULL,
    precio_referencia_usd NUMERIC(18,2) NOT NULL
        CHECK (precio_referencia_usd >= 0 AND precio_referencia_usd < 'Infinity'::NUMERIC),
    costo_referencia_usd NUMERIC(18,2) NOT NULL
        CHECK (costo_referencia_usd >= 0 AND costo_referencia_usd < 'Infinity'::NUMERIC),
    run_id UUID NOT NULL REFERENCES ops.ejecuciones (run_id),
    PRIMARY KEY (numero_pedido, linea_pedido),
    FOREIGN KEY (fecha_pedido, moneda_pedido) REFERENCES dw.tipos_cambio (fecha, moneda),
    CHECK (fecha_entrega >= fecha_pedido)
);

-- Las vistas de marts se despliegan con 002_marts.sql.
-- Solo índices implícitos de PK y UNIQUE; C3 y C7 se validan antes de publicar.
