-- Vistas lógicas E7. Ejecutar como propietario, fuera de una publicación ETL.
CREATE OR REPLACE VIEW marts.ventas_base AS
WITH valoradas AS (
    SELECT f.numero_pedido, f.linea_pedido, f.fecha_pedido, f.fecha_entrega,
           f.cliente_id, c.customer_key, c.pais AS pais_cliente,
           f.producto_id, p.product_key, p.nombre AS producto,
           p.categoria_codigo, p.categoria, p.subcategoria_codigo, p.subcategoria,
           f.sucursal_id, s.store_key, s.canal,
           CASE WHEN s.canal = 'Online' THEN NULL ELSE s.pais_origen END AS pais_sucursal,
           f.moneda_pedido, f.cantidad,
           f.precio_referencia_usd, f.costo_referencia_usd,
           f.cantidad * f.precio_referencia_usd AS ingresos_usd,
           f.cantidad * f.costo_referencia_usd AS costos_usd
    FROM dw.fact_ventas f
    JOIN dw.dim_cliente c USING (cliente_id)
    JOIN dw.dim_producto p USING (producto_id)
    JOIN dw.dim_sucursal s USING (sucursal_id)
)
SELECT *, ingresos_usd - costos_usd AS margen_usd FROM valoradas;

CREATE OR REPLACE VIEW marts.pedidos AS
SELECT numero_pedido, min(fecha_pedido) AS fecha_pedido,
       min(fecha_entrega) AS fecha_entrega, min(customer_key) AS customer_key,
       min(store_key) AS store_key, min(canal) AS canal,
       min(moneda_pedido) AS moneda_pedido,
       count(*) AS lineas, sum(cantidad) AS unidades,
       sum(ingresos_usd) AS ingresos_usd, sum(costos_usd) AS costos_usd,
       sum(margen_usd) AS margen_usd
FROM marts.ventas_base
GROUP BY numero_pedido;

CREATE OR REPLACE VIEW marts.clientes_primera_compra AS
SELECT customer_key, min(fecha_pedido) AS primera_compra
FROM marts.pedidos
GROUP BY customer_key;

-- Ampliación autorizada para identificar sucursales sin ninguna venta.
CREATE OR REPLACE VIEW marts.sucursales AS
SELECT store_key, canal,
       CASE WHEN canal = 'Online' THEN NULL ELSE pais_origen END AS pais_sucursal,
       CASE WHEN canal = 'Online' THEN NULL ELSE estado_origen END AS estado_sucursal
FROM dw.dim_sucursal;
