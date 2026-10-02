-- ============================================================
--  Tiendas Valdora (ficticia) · Vistas de control de gestión
--  Tablas base (las carga modelo.py):
--    dim_tienda, dim_categoria, dim_fecha,
--    fact_ventas (diaria), fact_presupuesto (mensual), fact_gastos (mensual, escenario real/ppto)
-- ============================================================

-- Venta real mensual por tienda y categoría
DROP VIEW IF EXISTS v_venta_mes;
CREATE VIEW v_venta_mes AS
SELECT f.año, f.mes, v.tienda_id, v.categoria_id,
       SUM(v.unidades)    AS unidades,
       SUM(v.venta_neta)  AS venta,
       SUM(v.costo_venta) AS costo,
       COUNT(DISTINCT v.fecha) AS dias_con_venta
FROM fact_ventas v
JOIN dim_fecha f ON f.fecha = v.fecha
GROUP BY f.año, f.mes, v.tienda_id, v.categoria_id;

-- Real vs presupuesto a nivel tienda x categoría x mes (base del análisis precio/volumen/mix)
DROP VIEW IF EXISTS v_real_vs_ppto;
CREATE VIEW v_real_vs_ppto AS
SELECT COALESCE(r.año, p.año)                   AS año,
       COALESCE(r.mes, p.mes)                   AS mes,
       COALESCE(r.tienda_id, p.tienda_id)       AS tienda_id,
       COALESCE(r.categoria_id, p.categoria_id) AS categoria_id,
       COALESCE(r.unidades, 0)    AS unidades_real,
       COALESCE(r.venta, 0)       AS venta_real,
       COALESCE(r.costo, 0)       AS costo_real,
       COALESCE(p.unidades_ppto, 0) AS unidades_ppto,
       p.precio_ppto,
       COALESCE(p.venta_ppto, 0)  AS venta_ppto,
       COALESCE(p.costo_ppto, 0)  AS costo_ppto
FROM v_venta_mes r
FULL OUTER JOIN fact_presupuesto p
  ON p.año = r.año AND p.mes = r.mes AND p.tienda_id = r.tienda_id AND p.categoria_id = r.categoria_id;

-- Estado de resultados mensual por tienda (real y presupuesto)
DROP VIEW IF EXISTS v_eerr_tienda_mes;
CREATE VIEW v_eerr_tienda_mes AS
WITH ventas AS (
    SELECT año, mes, tienda_id,
           SUM(venta_real) AS venta_real, SUM(costo_real) AS costo_real,
           SUM(venta_ppto) AS venta_ppto, SUM(costo_ppto) AS costo_ppto
    FROM v_real_vs_ppto
    GROUP BY año, mes, tienda_id
),
gastos AS (
    SELECT año, mes, tienda_id,
           SUM(CASE WHEN escenario = 'real' THEN monto END) AS gastos_real,
           SUM(CASE WHEN escenario = 'ppto' THEN monto END) AS gastos_ppto
    FROM fact_gastos
    GROUP BY año, mes, tienda_id
)
SELECT v.año, v.mes, v.tienda_id, t.tienda, t.zona, t.formato,
       v.venta_real, v.venta_ppto,
       v.venta_real - v.costo_real AS margen_real,
       v.venta_ppto - v.costo_ppto AS margen_ppto,
       COALESCE(g.gastos_real, 0) AS gastos_real,
       COALESCE(g.gastos_ppto, 0) AS gastos_ppto,
       v.venta_real - v.costo_real - COALESCE(g.gastos_real, 0) AS ebitda_real,
       v.venta_ppto - v.costo_ppto - COALESCE(g.gastos_ppto, 0) AS ebitda_ppto
FROM ventas v
JOIN dim_tienda t ON t.tienda_id = v.tienda_id
LEFT JOIN gastos g ON g.año = v.año AND g.mes = v.mes AND g.tienda_id = v.tienda_id;

-- Gastos por cuenta, real vs presupuesto, con peso sobre la venta
DROP VIEW IF EXISTS v_gastos_cuenta;
CREATE VIEW v_gastos_cuenta AS
SELECT g.año, g.mes, g.tienda_id, g.cuenta,
       SUM(CASE WHEN g.escenario = 'real' THEN g.monto END) AS real,
       SUM(CASE WHEN g.escenario = 'ppto' THEN g.monto END) AS ppto,
       e.venta_real, e.venta_ppto
FROM fact_gastos g
JOIN v_eerr_tienda_mes e ON e.año = g.año AND e.mes = g.mes AND e.tienda_id = g.tienda_id
GROUP BY g.año, g.mes, g.tienda_id, g.cuenta;

-- Venta diaria total (para la proyección de cierre de mes)
DROP VIEW IF EXISTS v_venta_diaria;
CREATE VIEW v_venta_diaria AS
SELECT f.fecha, f.año, f.mes, f.dia, f.dia_semana, SUM(v.venta_neta) AS venta
FROM fact_ventas v
JOIN dim_fecha f ON f.fecha = v.fecha
GROUP BY f.fecha;
