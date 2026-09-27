# Ejecución de E5

Registro histórico del 19/09/2026. Describe el estado de E5, antes de las entregas posteriores. La instalación y las pruebas actuales se ejecutan según el [README](../README.md); las fixtures mutables usan exclusivamente `sales_analytics_e6_test`, sin vaciar las bases de aceptación.

Alcance autorizado: transformación y primera publicación transaccional, con repetición
inmediata inocua. Rigen architecture.md, data_model.md, business_rules.md y E5 de
implementation_plan.md. E6 y las vistas/KPIs/interfaz permanecen fuera del alcance.

## Secuencia y aceptación

- [x] Inspeccionar ambas bases sin escribir: PostgreSQL 17.11, ningún DW publicado.
  El intento E4 c72eb197-9913-4015-97be-761003d5064d existe solo en sales_analytics_test.
- [x] Escribir pruebas de transformación en tests/unit/test_transform.py; observar
  el fallo e implementar transform.py reutilizando convert_records. Comprobar Decimal,
  calendario continuo, ceros iniciales, NA, Online, nulos y conservación del argumento.
- [x] Escribir tests/integration/test_load.py con CSV sintéticos independientes,
  exclusivamente en sales_analytics_test. Observar el fallo e implementar load.py:
  bloqueo transaccional común, comprobaciones después del bloqueo, COPY de dimensiones,
  resolución de claves, hechos, conciliación y metadatos en un único commit.
- [x] Probar desde otra conexión la invisibilidad de datos/identidad antes del commit;
  inyectar error después de escribir, comprobar rollback y diagnóstico separado;
  repetir con otro intento y exigir sin_cambios con publication_id nulo.
- [x] Añadir cli.py para ejecución manual completa o continuación de un run local
  validado. Probar salida/errores sin secretos. No añadir dependencias ni objetos SQL.
- [x] Ejecutar suite E1–E5 antes de la aceptación del snapshot, Ruff y dependencias.
- [x] Publicar el intento E4 en pruebas, conciliar contra controles independientes y
  repetir los originales mediante un nuevo intento propio. Verificar firmas de origen
  y staging, identidades estables, 62884 líneas, 26326 pedidos, 197757 unidades,
  USD 55755479.59 y USD 23092791.21.
- [x] Solo tras superar esas comprobaciones, extraer/validar/publicar en sales_analytics
  con nuevo run_id, sin copiar filas de staging ni auditoría de la otra base.
- [x] Documentar resultados, archivos y limitaciones; detenerse antes de E6.

## Decisiones de implementación

La conexión escritora usa READ COMMITTED: la consulta posterior al bloqueo obtiene
el estado recién confirmado, sin fijar antes una instantánea antigua. Un advisory
lock transaccional común serializa publicadores; bloqueos de tablas protegen staging
y DW durante las comprobaciones y escrituras. No se crean tablas de coordinación.
La identidad se calcula a partir de publicaciones confirmadas y solo se devuelve
después del commit. Los diagnósticos operativos no incluyen filas ni credenciales.

E5 admite primera carga y repetición del vigente; los cambios materiales se detectan
y se bloquean, aunque traigan autorización, porque su aplicación/revaloración y la
recuperación avanzada corresponden a E6. Esto no altera las políticas D1–D6.
El candidato se contrasta con los bytes identificados por E3 para no publicar un
staging alterado después de validar. No se normalizan atributos textuales informados;
solo vacíos opcionales pasan a nulo fuera de staging.

Las pruebas sintéticas de publicación requieren DW vacío; limpian únicamente sus
propias publicaciones confirmadas. La aceptación del snapshot se ejecuta después
de la suite y conserva sus datos. No se vacían automáticamente datos de aceptación.

## Cierre verificado (2026-09-19)

245 pruebas aprobadas (147 unitarias y 98 de integración), Ruff y dependencias sin
incidencias. Primera publicación confirmada con identidad 1 en cada base y repetición
real sin cambios en pruebas. Detalle de intentos, conciliación y archivos en
[portafolio](portfolio.md#resultados-del-snapshot). Evidencia local ignorada:
.verification/e5-test-result.json y .verification/e5-project-result.json.
Se añadió load_checks.py para separar controles de persistencia y se compartió la
fixture staged_run de E4 en tests/integration/conftest.py. Sin nuevas dependencias.
E6 no se implementó.
