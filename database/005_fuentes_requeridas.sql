-- TuCoach Empleo · 005 · Fuentes que el código resuelve por identidad y que
-- seed_initial.sql no creaba. Sin ellas, en cada ciclo fallan con
-- "RuntimeError: Fuente no encontrada":
--   * 'Diari Oficial de la Generalitat Valenciana' / DOGV  (fuente "gva")
--   * 'Diputación Alicante - Otras oposiciones' / SEDE      (fuente "bop_alicante")
-- Idempotente: puede ejecutarse más de una vez sin duplicar nada.

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Diari Oficial de la Generalitat Valenciana', 'DOGV', 'https://dogv.gva.es/', 20
FROM organismos o
WHERE o.nombre = 'Generalitat Valenciana'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE LOWER(TRIM(f.nombre)) = LOWER(TRIM('Diari Oficial de la Generalitat Valenciana'))
        AND UPPER(f.tipo) = 'DOGV'
  );

INSERT INTO fuentes (organismo_id, nombre, tipo, url, prioridad)
SELECT o.id, 'Diputación Alicante - Otras oposiciones', 'SEDE',
       'https://sede.diputacionalicante.es/empleo-otras-oposiciones/', 20
FROM organismos o
WHERE o.nombre = 'Diputación Provincial de Alicante'
  AND NOT EXISTS (
      SELECT 1 FROM fuentes f
      WHERE LOWER(TRIM(f.nombre)) = LOWER(TRIM('Diputación Alicante - Otras oposiciones'))
        AND UPPER(f.tipo) = 'SEDE'
  );
