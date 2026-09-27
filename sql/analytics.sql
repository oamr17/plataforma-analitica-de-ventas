-- name: metadatos
SELECT e.run_id, e.publication_id, e.version_reglas,
       e.fecha_pedido_min, e.fecha_pedido_max, e.fin AS publicado_en,
       false AS cobertura_comercial_verificada,
       coalesce((SELECT jsonb_agg(jsonb_build_object(
           'regla', i.regla, 'motivo', i.motivo, 'evidencia', i.evidencia::jsonb)
           ORDER BY i.incidencia_id)
           FROM ops.incidencias i WHERE i.run_id=e.run_id AND i.regla='W5'),
           '[]'::jsonb) AS advertencias_cobertura
FROM ops.ejecuciones e
WHERE e.publication_id IS NOT NULL
ORDER BY e.publication_id DESC LIMIT 1;

-- name: indicadores
WITH vigente AS (
    SELECT run_id, fecha_pedido_min, fecha_pedido_max
    FROM ops.ejecuciones WHERE publication_id IS NOT NULL
    ORDER BY publication_id DESC LIMIT 1
), limites AS (
    SELECT %(inicio)s::date AS inicio, %(fin)s::date AS fin,
           (date_trunc('month', %(inicio)s::date) - interval '1 month')::date AS anterior
), meses AS (
    SELECT d::date AS mes, (d + interval '1 month - 1 day')::date AS fin_mes
    FROM limites, LATERAL generate_series(anterior,
        date_trunc('month', fin)::date, interval '1 month') d
), tiendas AS (
    SELECT * FROM marts.sucursales
    WHERE (%(store_keys)s::integer[] IS NULL OR store_key=ANY(%(store_keys)s))
      AND (%(canales)s::text[] IS NULL OR canal=ANY(%(canales)s))
      AND (%(paises_sucursal)s::text[] IS NULL OR pais_sucursal=ANY(%(paises_sucursal)s))
), lineas AS (
    SELECT v.*, c.primera_compra
    FROM marts.ventas_base v
    JOIN marts.clientes_primera_compra c USING (customer_key)
    JOIN tiendas t USING (store_key)
    CROSS JOIN limites l
    WHERE v.fecha_pedido BETWEEN l.anterior AND l.fin
      AND (%(product_keys)s::integer[] IS NULL OR v.product_key=ANY(%(product_keys)s))
      AND (%(categoria_codigos)s::text[] IS NULL OR v.categoria_codigo=ANY(%(categoria_codigos)s))
      AND (%(customer_keys)s::integer[] IS NULL OR v.customer_key=ANY(%(customer_keys)s))
      AND (%(paises_cliente)s::text[] IS NULL OR v.pais_cliente=ANY(%(paises_cliente)s))
      AND (%(monedas)s::text[] IS NULL OR v.moneda_pedido=ANY(%(monedas)s))
), grupos AS (
    -- Una línea participa en cada ruta; nunca se suman los conteos entre rutas.
    SELECT v.*, g.grupo, g.clave
    FROM lineas v CROSS JOIN limites l
    CROSS JOIN LATERAL (
        SELECT 'mes'::text AS grupo, date_trunc('month', v.fecha_pedido)::date::text AS clave
        WHERE v.fecha_pedido>=l.inicio OR v.fecha_pedido<date_trunc('month',l.inicio)::date
        UNION ALL
        SELECT x.grupo, x.clave FROM (VALUES
            ('resumen', ''), ('producto', v.product_key::text),
            ('sucursal', v.store_key::text), ('canal', v.canal)
        ) x(grupo,clave) WHERE v.fecha_pedido BETWEEN l.inicio AND l.fin
    ) g
), compradores AS (
    SELECT grupo, clave, customer_key, min(primera_compra) AS primera_compra,
           count(DISTINCT numero_pedido) AS pedidos
    FROM grupos GROUP BY grupo, clave, customer_key
), clientes AS (
    SELECT grupo, clave, count(*) FILTER (WHERE pedidos>=2) AS recurrentes,
           count(*) FILTER (WHERE primera_compra BETWEEN
               CASE WHEN grupo='mes' THEN greatest(clave::date,l.inicio) ELSE l.inicio END AND
               CASE WHEN grupo='mes' THEN least((clave::date + interval '1 month - 1 day')::date,l.fin)
                    ELSE l.fin END) AS nuevos
    FROM compradores CROSS JOIN limites l GROUP BY grupo, clave
), agregados AS (
    SELECT grupo, clave, count(*) AS lineas,
           count(DISTINCT numero_pedido) AS pedidos,
           count(DISTINCT customer_key) AS clientes,
           sum(cantidad) AS unidades, sum(ingresos_usd) AS ingresos_usd,
           sum(costos_usd) AS costos_usd, sum(margen_usd) AS margen_usd,
           min(producto) AS producto
    FROM grupos GROUP BY grupo, clave
), objetivos AS (
    SELECT 'resumen'::text AS grupo, ''::text AS clave
    UNION ALL SELECT 'producto', clave FROM agregados WHERE grupo='producto'
    UNION ALL SELECT 'sucursal', store_key::text FROM tiendas
    UNION ALL SELECT DISTINCT 'canal', canal FROM tiendas
    UNION ALL SELECT 'mes', mes::text FROM meses
), totales AS (
    SELECT o.grupo,o.clave,
           coalesce(a.lineas,0) AS lineas, coalesce(a.pedidos,0) AS pedidos,
           coalesce(a.clientes,0) AS clientes, coalesce(a.unidades,0) AS unidades,
           coalesce(a.ingresos_usd,0::numeric) AS ingresos_usd,
           coalesce(a.costos_usd,0::numeric) AS costos_usd,
           coalesce(a.margen_usd,0::numeric) AS margen_usd,
           coalesce(c.nuevos,0) AS nuevos, coalesce(c.recurrentes,0) AS recurrentes,
           a.producto
    FROM objetivos o LEFT JOIN agregados a USING(grupo,clave)
    LEFT JOIN clientes c USING(grupo,clave)
), cobertura AS (
    SELECT m.*, v.fecha_pedido_min, v.fecha_pedido_max,
           coalesce(m.mes >= v.fecha_pedido_min AND m.fin_mes <= v.fecha_pedido_max,
                    false) AS mes_entero_publicado,
           EXISTS(SELECT 1 FROM ops.incidencias i
               WHERE i.run_id=v.run_id AND i.regla='W5'
               AND (i.evidencia::jsonb->>'inicio')::date <= m.fin_mes
               AND (i.evidencia::jsonb->>'fin')::date >= (m.mes-interval '1 month')::date
           ) AS huecos_conocidos,
           EXISTS(SELECT 1 FROM ops.incidencias i WHERE i.run_id=v.run_id
               AND i.clasificacion='error_critico') AS fallo_cobertura,
           (SELECT count(DISTINCT b.fecha_pedido) FROM marts.ventas_base b
            WHERE b.fecha_pedido BETWEEN m.mes AND m.fin_mes) AS dias_con_registros
    FROM meses m LEFT JOIN vigente v ON true
), mensual AS (
    SELECT t.*, c.mes,c.fin_mes,c.mes_entero_publicado,c.huecos_conocidos,
           c.dias_con_registros,
           lag(t.ingresos_usd) OVER (ORDER BY c.mes) AS ingresos_anterior_usd,
           c.fallo_cobertura OR NOT c.mes_entero_publicado
           OR (c.mes-interval '1 month')::date < c.fecha_pedido_min
           OR l.inicio>c.mes OR l.fin<c.fin_mes
           OR (l.inicio>(c.mes-interval '1 month')::date AND l.inicio<c.mes)
           OR (c.huecos_conocidos AND NOT %(accept_coverage_warnings)s::boolean)
           AS no_comparable
    FROM totales t JOIN cobertura c ON t.clave=c.mes::text
    CROSS JOIN limites l WHERE t.grupo='mes'
), resultados AS (
    SELECT t.*, NULL::date AS mes, NULL::date AS fin_mes,
           NULL::boolean AS mes_entero_publicado, NULL::boolean AS huecos_conocidos,
           NULL::bigint AS dias_con_registros, NULL::numeric AS ingresos_anterior_usd,
           NULL::numeric AS crecimiento_pct
    FROM totales t WHERE grupo<>'mes'
    UNION ALL
    SELECT grupo,clave,lineas,pedidos,clientes,unidades,ingresos_usd,costos_usd,
           margen_usd,nuevos,recurrentes,producto,mes,fin_mes,mes_entero_publicado,
           huecos_conocidos,dias_con_registros,ingresos_anterior_usd,
           CASE WHEN NOT no_comparable AND ingresos_anterior_usd>0
                THEN 100::numeric*(ingresos_usd-ingresos_anterior_usd)/ingresos_anterior_usd
           END AS crecimiento_pct
    FROM mensual CROSS JOIN limites l WHERE mes>=date_trunc('month',l.inicio)::date
)
SELECT r.*,
       ingresos_usd/nullif(pedidos,0) AS ticket_usd,
       100::numeric*margen_usd/nullif(ingresos_usd,0) AS margen_pct,
       100::numeric*recurrentes/nullif(clientes,0) AS tasa_recurrencia_pct,
       lineas=0 AS sin_registros,
       CASE WHEN grupo='producto' THEN clave::integer END AS product_key,
       CASE WHEN grupo='sucursal' THEN clave::integer END AS store_key,
       CASE WHEN grupo='canal' THEN clave WHEN grupo='sucursal' THEN s.canal END AS canal,
       s.pais_sucursal
FROM resultados r LEFT JOIN tiendas s ON r.grupo='sucursal' AND r.clave=s.store_key::text
ORDER BY grupo, CASE WHEN grupo='producto' THEN ingresos_usd END DESC,
         CASE WHEN grupo IN ('producto','sucursal') THEN clave::integer END,
         mes, clave;
