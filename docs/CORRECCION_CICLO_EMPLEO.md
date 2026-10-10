# TuCoach Empleo · corrección del ciclo de actualización

> Integrado sobre `main` en el commit `c6f57a7` ("Rebuild canonical Empleo database schema"). Las fuentes se ejecutan ahora a través de `app/organismos_cron.py`; el ciclo reservado, las ventanas por fuente y la reconciliación de cierres viven en `app/periodic.py`. **`python -m app.organismos_cron --organismo X` ejecuta una fuente suelta y NO reserva ciclo ni reconcilia cierres**: para el cron diario usa `scripts/run_periodic_http.py --aplicar`.

Definición de negocio aplicada: **una convocatoria está activa mientras no se haya publicado su resultado final.** Es independiente de la fecha de publicación y del plazo de inscripción. El cron detecta altas nuevas y da de baja las que ya tienen resultado.

## Qué fallaba y qué se ha cambiado

| # | Problema | Solución | Fichero |
|---|----------|----------|---------|
| 1 | El código no tenía el concepto "resultado publicado": una relación de aprobados nunca cerraba una convocatoria. | Definición única del ciclo de vida, con reglas para resultado final, nombramiento, desierto, anulación y desistimiento **del proceso**. | `app/ciclo_vida.py` |
| 2 | Cerraba por error: "anulación de una pregunta", "desistimiento de un aspirante" o una corrección de errores marcaban el proceso como ANULADO/DESISTIDO. | Esas reglas exigen que el objeto sea la convocatoria/proceso. Los resultados parciales, provisionales y las bolsas no cierran. | `app/ciclo_vida.py` |
| 3 | Siete definiciones distintas de "estado terminal" (una sin `anulado`). | Todas usan la misma. | `procesos.py`, `gva_*`, `bop_valencia_*` |
| 4 | Un cierre ingerido pero no vinculado en su momento quedaba sin aplicar para siempre. | **Reconciliación** sobre las publicaciones guardadas, con cualquier fuente; idempotente y reversible. Último paso de cada ciclo. | `app/reconciliar_cierres.py` |
| 5 | Ventana fija de 7 días: tras una caída del cron se perdían cierres. | La ventana de cada fuente se amplía desde su último éxito (tope ordinario 30 días, con aviso si se excede). | `app/ciclos.py`, `periodic.py` |
| 6 | El cron mostraba "verde" aunque fallaran las 11 fuentes. | Código de salida real; tolerancia opcional. | `scripts/run_periodic_http.py`, `app/cron_actualizacion.py` |
| 7 | Sin lock: un reintento podía lanzar un 2º ciclo simultáneo. | Un único ciclo `EN_CURSO` (índice único parcial; no usa advisory locks por el pooler de Supabase). | `database/004`, `app/ciclos.py` |
| 8 | La URL por defecto del cron apuntaba a **staging**. | Sin valor por defecto: falla si falta. Tampoco reintenta tras un timeout de lectura. | `app/cron_actualizacion.py` |
| 9 | El DOGV (que cierra las convocatorias de la Generalitat) salía **sin proxy**; sin reintentos ni caché. | Cliente DOGV con proxy (`DOGV_USAR_PROXY`), reintentos y caché de diarios por ciclo. | `gva_estatal_source.py`, `gva_dogv_diagnostico.py` |
| 10 | Festivos solo de 2026: desde enero de 2027 los plazos por literal dejaban de calcularse, y un plazo que cruza de año salía hasta 4 días antes. | Calendario por reglas (Pascua incluida) consultado día a día con el año de cada día. | `app/festivos.py` |
| 11 | `date.today()` en servidor UTC: de 00:00 a 02:00 hora española devolvía el día anterior. | `hoy_es()` (Europe/Madrid) en todos los conectores. | `app/festivos.py` |
| 12 | "Convocatorias en plazo": el límite de 200 se aplicaba **antes** de filtrar por plazo. | Parámetro `en_plazo=true` en `/public/procesos` y `/procesos`. | `procesos.py`, `main.py` |
| 13 | (Ya resuelto en tu `main`: `schema.sql` reconstruido incluye `origen_dato`, `coaching_*` y `temarios_empleo`.) | — | `database/schema.sql` |

## Puesta en marcha (orden recomendado)

1. **Copia de seguridad** y trabaja en una rama.
2. Aplica `database/004_ciclos_periodicos.sql` (repetible). **No ejecutes `schema.sql` sobre una base que ya tenga tablas**: no es repetible (`CREATE TABLE` sin `IF NOT EXISTS`) y falla en la primera línea.
3. Despliega el backend y configura las variables (tabla más abajo).
4. Comprueba conectividad: `GET /admin/debug/gva-conectividad` (cabecera `X-Import-Secret`). Ahora prueba también el DOGV y dice si el proxy está activo.
5. **Revisión sin escribir** de la carga histórica (ver el JSON; `estado_fuentes` debe estar sin ERROR):
   `python scripts/run_periodic_http.py --historico --dias 730`
6. **Carga histórica** (una sola vez; no envía notificaciones):
   `python scripts/run_periodic_http.py --aplicar --historico --dias 730`
7. **Audita los cierres con tus títulos reales**: `python scripts/reconciliar_cierres.py` (una línea por decisión). Si una regla cierra algo que no debe, ajusta `ciclo_vida.py` y añade el título a `tests/test_ciclo_vida.py`.
8. Crea el cron diario (ver `docs/render.yaml.propuesta`). Tras la carga histórica, la primera ejecución ordinaria parte del último éxito registrado.

## Variables de entorno

| Variable | Uso |
|----------|-----|
| `DATABASE_URL` | BD de Empleo (cron directo y backend). |
| `GVA_PROXY_URL`, `GVA_PROXY_USER`, `GVA_PROXY_PASSWORD` | Proxy Decodo (los tres o ninguno). |
| `DOGV_USAR_PROXY` | `true` por defecto. `false` si compruebas que el DOGV es accesible sin proxy y quieres ahorrar tráfico. |
| `EMPLOYMENT_CRON_ERRORES_TOLERADOS` | Fuentes en ERROR admitidas antes de que el cron falle (por defecto `0`). |
| `EMPLOYMENT_API_URL`, `EMPLOYMENT_CRON_SECRET`, `EMPLOYMENT_CRON_TIMEOUT` | Solo para el modo HTTP. |

## Qué no se ha podido comprobar

- **Nada se ha ejecutado contra las fuentes reales** (BOP, BOE, DOGV, sede GVA, proxy). El scraping y los parsers no se han tocado; sí la decisión de cierre, las ventanas, el proxy del DOGV y el cron.
- **El clasificador está validado con ~55 títulos escritos por mí**, no con tus datos. Es el punto a revisar en el paso 7. Un cierre solo se detecta si la publicación llega a `publicaciones` vinculada al proceso; un ayuntamiento que publique el resultado solo en su sede, fuera de los boletines que rastreáis, no se cerrará con ningún cambio de este paquete.
- La **carga histórica completa** no está probada: 730 días por varias fuentes pueden tardar y consumir proxy. Empieza por la revisión sin escribir (paso 5).
- Tests: 201 pasan en un entorno con las librerías externas sustituidas por dobles (la base original: 116). El SQL nuevo y la reconciliación se ejecutaron contra **PostgreSQL 16 real**; `psycopg`/`httpx` reales no estaban disponibles. Conviene lanzar `pytest` en tu entorno.
- Calendario de festivos 2027 en adelante: calculado por reglas, **no verificado** contra el DOGV (el resultado marca `calendario_verificado: false`). Para fijar un año, rellena `CALENDARIOS_VERIFICADOS` en `app/festivos.py`. No incluye festivos locales.
- Los plazos en "días naturales" siguen sin calcularse (hay un test del autor que lo fija así).
- La web (`tucoach-web`) no se ha revisado: para usar el filtro nuevo, el botón debe pasar `en_plazo=true`; si sigue filtrando en cliente por `estado_inscripcion`, también funciona ahora que el catálogo ya excluye lo resuelto.
- Las tablas antiguas siguen sin RLS. Si la base es de Supabase, actívalo (las tablas nuevas ya lo llevan).
