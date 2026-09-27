# TuCoach Web — Empleo

Módulo de descubrimiento y seguimiento de oportunidades de empleo público
administrativo en la Comunidad Valenciana, servido en `netexamenes.com/empleo`.

## Arquitectura actual

- Este repositorio contiene los recolectores, la lógica de normalización y el
  ejecutor periódico de Empleo.
- La aplicación web y el backend público activos se despliegan desde
  `Fragarre/TuCoach-Web`.
- El backend público `opocoach-web-staging-backend` integra este repositorio
  como submódulo y publica sus rutas bajo `/empleo`.
- La base de datos de Empleo se mantiene separada de la base de datos general.

Consulta [docs/PUNTO_8_AUTOMATIZACION.md](docs/PUNTO_8_AUTOMATIZACION.md) para
la operación periódica y sus variables de entorno.

## Principios de seguridad

- Las fuentes oficiales son la referencia principal.
- No se infiere la clasificación de una plaza por su denominación: grupo,
  subgrupo y escala solo se incorporan cuando el documento oficial los declara.
- La revisión histórica de clasificaciones no sobrescribe información ya
  existente y se ejecuta en modo solo lectura salvo indicación explícita.
- No se incluyen secretos ni URLs privadas en el repositorio.
