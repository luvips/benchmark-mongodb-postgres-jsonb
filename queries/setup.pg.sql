-- Esquema e indexación para PostgreSQL JSONB (Sección 2 y Enmienda 9.3 del Playbook).
-- El contenedor corre con TZ=UTC y todos los timestamps son ISO-8601 UTC con sufijo 'Z'.
-- Marcar timestamptz_in como IMMUTABLE permite indexar directamente la expresión exacta
-- ((data->>'timestamp')::timestamptz) usada en queries/q2.pg.sql sin modificar su SQL
-- (fidelidad literal a las Secciones 2 y 5.2 del playbook).
--
-- LIMITACIÓN DOCUMENTADA: modificar pg_catalog requiere superusuario y no es posible en
-- PostgreSQL administrado (RDS, Cloud SQL, Azure). En la nube la alternativa es un wrapper
-- IMMUTABLE propio (p. ej. iso_timestamptz(text)) indexado y referenciado desde la consulta,
-- lo que exige cambiar el SQL de Q2 (ver Sección 7 del reporte).
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY,
    data JSONB NOT NULL
);

ALTER FUNCTION pg_catalog.timestamptz_in(cstring, oid, integer) IMMUTABLE;

CREATE INDEX IF NOT EXISTS orders_timestamp_idx
    ON orders (((data->>'timestamp')::timestamptz));
