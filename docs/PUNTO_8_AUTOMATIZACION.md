# Automatización periódica de Empleo

La actualización productiva se ejecuta mediante el cron de Render
`tucoach-empleo-periodic`.

- Repositorio: `Fragarre/TuCoach-Web-Empleo`, rama `main`, directorio `backend`.
- Orden de arranque: `python scripts/run_periodic_http.py --aplicar --dias 7`.
- Frecuencia: diariamente a las 17:00 UTC.
- Servicio de destino: backend unificado `opocoach-web-staging-backend`, bajo
  el prefijo `/empleo`.
- Autenticación: secreto de entorno `EMPLOYMENT_CRON_SECRET`; nunca se guarda
  ni se muestra en el repositorio.

`EMPLOYMENT_API_URL` permite indicar explícitamente la URL de destino. En su
ausencia, el ejecutor usa la ruta unificada. La variable histórica
`NETRETO_EMPLEO_API_URL` se acepta solo como compatibilidad temporal.

## Regla funcional de cobertura

La antigüedad de una convocatoria y el cierre del plazo de solicitudes **no son criterios de exclusión**. Una oportunidad administrativa permanece en el catálogo mientras el proceso selectivo siga activo y cumpla las reglas de inclusión del producto.

Los siete días empleados por el cron son un **solape técnico de consulta** para tolerar fallos temporales y reintentos. No determinan qué oportunidades son válidas ni cuándo dejan de serlo.

En GVA se separan expresamente dos conceptos:

1. `estado_plazo_solicitud`: abierto, pendiente o cerrado;
2. `estado` del proceso selectivo: en curso o finalizado según la etapa oficial.

Además de descubrir nuevas fichas GVA, cada actualización vuelve a consultar las oportunidades GVA ya conocidas que todavía no tienen un estado terminal, aunque su plazo de solicitud haya finalizado.

Las fuentes oficiales continúan siendo la referencia. Las fuentes externas,
cuando se empleen como contraste, no sustituyen los criterios de inclusión.
