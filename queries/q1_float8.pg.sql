-- Variante de verificación metodológica (Sección 2/5 del reporte):
-- Evalúa el costo de CPU de ::numeric frente a ::float8 (equivalente a IEEE 754 double de MongoDB).
SELECT data->'customer'->'geo'->>'country' AS country,
       item->>'category' AS category,
       SUM((item->>'qty')::float8 * (item->>'price')::float8) AS total_revenue,
       AVG((item->>'qty')::float8 * (item->>'price')::float8) AS avg_revenue,
       COUNT(*) AS count
FROM orders, jsonb_array_elements(data->'items') AS item
WHERE (item->>'price')::float8 > 50
GROUP BY 1, 2;
