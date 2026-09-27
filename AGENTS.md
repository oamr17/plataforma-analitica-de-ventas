# AGENTS.md — Sales Analytics Platform

## 1. Objetivo del proyecto

Desarrollar una plataforma de análisis de ventas con una arquitectura por capas:

- ETL con Python.
- Almacenamiento y modelado con PostgreSQL.
- Análisis y visualización con Streamlit.

El proyecto debe demostrar buenas prácticas de ingeniería de software, análisis de datos, SQL, automatización y desarrollo de aplicaciones.

La prioridad es construir un sistema correcto, comprensible, mantenible y verificable.

La simplicidad tiene prioridad sobre la cantidad de código.

---

## 2. Filosofía de desarrollo

Actúa como un ingeniero de software experimentado.

No generes código de manera indiscriminada.

Antes de implementar:

1. Comprende el problema.
2. Inspecciona el código existente.
3. Identifica los componentes afectados.
4. Propón una solución.
5. Explica las decisiones arquitectónicas.
6. Espera aprobación cuando se trate de cambios importantes.

Implementa únicamente lo necesario para resolver el problema actual.

No introduzcas funcionalidades que no han sido solicitadas.

No agregues abstracciones pensando en necesidades hipotéticas.

No reescribas componentes que ya funcionan sin una razón técnica.

---

## 3. Arquitectura

Mantén una separación clara entre:

- Extracción.
- Validación.
- Transformación.
- Persistencia.
- Lógica de negocio.
- Detección de anomalías.
- Presentación.

El pipeline ETL debe funcionar independientemente de Streamlit.

Streamlit no debe ejecutar procesos ETL cuando un usuario abre, actualiza o filtra el dashboard.

Las consultas y definiciones de indicadores deben estar centralizadas.

No dupliques reglas de negocio en Python, SQL y Streamlit.

PostgreSQL debe organizarse mediante los esquemas:

- staging
- dw
- marts
- ops

Cada módulo debe tener una responsabilidad identificable.

---

## 4. Diseño orientado a objetos

Aplica principios de programación orientada a objetos cuando aporten claridad.

Utiliza:

- Encapsulamiento.
- Responsabilidad única.
- Interfaces claras.
- Composición.
- Herencia cuando exista una relación real de especialización.

No crees clases únicamente para envolver una función.

No construyas jerarquías de herencia innecesarias.

Prefiere composición cuando permita reutilizar comportamiento sin generar acoplamiento.

Utiliza funciones simples cuando sean suficientes.

No implementes patrones de diseño solo por demostrar conocimientos técnicos.

Los patrones deben resolver problemas reales.

---

## 5. Reutilización de código

Antes de escribir una función, comprueba si ya existe una implementación equivalente.

Evita:

- Funciones duplicadas.
- Validaciones repetidas.
- Consultas SQL copiadas.
- Transformaciones redundantes.
- Constantes dispersas.
- Lógica de negocio dentro de componentes visuales.

Extrae código compartido únicamente cuando exista una necesidad real de reutilización.

No construyas módulos genéricos que terminen siendo utilizados una sola vez sin aportar claridad.

---

## 6. Calidad y legibilidad

El código debe poder comprenderse sin explicaciones externas.

Utiliza nombres descriptivos para:

- Variables.
- Funciones.
- Clases.
- Módulos.
- Parámetros.

Mantén las funciones pequeñas y cohesionadas.

Evita funciones que realicen múltiples responsabilidades.

Evita archivos excesivamente extensos.

Si un archivo contiene responsabilidades diferentes, evalúa dividirlo.

No fragmentes artificialmente archivos que ya son fáciles de comprender.

Utiliza anotaciones de tipos en interfaces y funciones relevantes.

Mantén convenciones consistentes en todo el repositorio.

---

## 7. Código innecesario

Está prohibido incorporar código sin una finalidad concreta.

No agregues:

- Funciones sin utilizar.
- Clases vacías.
- Variables innecesarias.
- Imports no utilizados.
- Código comentado que ya no se utiliza.
- Configuraciones innecesarias.
- Dependencias no justificadas.
- Funcionalidades especulativas.
- Compatibilidad con escenarios inexistentes.

Elimina el código obsoleto cuando corresponda.

No conserves implementaciones antiguas si ya han sido reemplazadas y no existe una necesidad real de mantenerlas.

Git conserva el historial.

---

## 8. Comentarios y documentación

No escribas comentarios que repitan lo que hace el código.

Evita comentarios como:

# Incrementar contador
counter += 1

Documenta principalmente:

- Decisiones arquitectónicas.
- Reglas de negocio.
- Comportamientos no evidentes.
- Restricciones técnicas.
- Supuestos relevantes.

Los comentarios deben explicar el porqué, no describir literalmente cada instrucción.

Escribe docstrings cuando aporten información útil sobre contratos, parámetros, resultados o excepciones.

No agregues docstrings extensos a funciones evidentes.

La documentación del proyecto debe estar en español.

---

## 9. Estilo de código

Sigue las convenciones de Python y PEP 8.

Utiliza un formateador y un linter consistentes.

Preferentemente:

- Ruff para formato y lint.
- Pytest para pruebas.
- Comprobación estática de tipos cuando corresponda.

No utilices múltiples herramientas que cumplan exactamente la misma función sin justificación.

Mantén un estilo uniforme.

Evita configuraciones excesivas.

---

## 10. Gestión de errores

No ocultes excepciones.

No utilices bloques try/except genéricos sin una razón clara.

Captura los errores en el nivel donde puedan gestionarse correctamente.

Registra información suficiente para diagnosticar fallos.

No expongas contraseñas, secretos ni datos sensibles en logs.

Diferencia claramente entre:

- Errores de infraestructura.
- Errores de extracción.
- Errores de validación.
- Errores de transformación.
- Errores de carga.

Los registros inválidos deben conservar su motivo de rechazo.

Una anomalía estadística no debe tratarse automáticamente como un registro inválido.

---

## 11. Base de datos

Utiliza consultas parametrizadas.

No construyas consultas SQL mediante concatenación insegura de valores.

Define claves primarias, claves foráneas y restricciones cuando correspondan.

Documenta el grano de cada tabla de hechos.

La tabla fact_ventas representa una línea de pedido.

Evita duplicados mediante claves de negocio y operaciones idempotentes.

Define explícitamente la política de actualización de registros.

Utiliza transacciones para garantizar la consistencia de las publicaciones.

Una carga fallida no debe dejar datos parcialmente publicados.

No almacenes información redundante sin justificación.

No crees índices innecesarios.

---

## 12. Indicadores de negocio

Centraliza las definiciones de KPIs en la capa analítica.

Los indicadores deben tener definiciones matemáticas explícitas.

Ejemplo:

Ticket promedio =
Ventas netas / Número de pedidos distintos.

No calcules promedios de promedios cuando produzcan resultados incorrectos.

Las definiciones deben mantenerse consistentes entre SQL, Streamlit y cualquier herramienta BI adicional.

Los indicadores deben poder verificarse mediante pruebas independientes.

---

## 13. Testing

Cada funcionalidad importante debe contar con pruebas apropiadas.

Prioriza:

- Pruebas unitarias para transformaciones.
- Pruebas de validación.
- Pruebas de integración con PostgreSQL.
- Pruebas de idempotencia.
- Pruebas de consistencia de KPIs.
- Pruebas de manejo de errores.

Utiliza fixtures pequeñas, comprensibles y deterministas.

No dependas de datos aleatorios sin una semilla fija.

No escribas pruebas que solamente confirmen que una función fue llamada.

Comprueba resultados y comportamientos observables.

No declares que una funcionalidad funciona si no ha sido verificada.

---

## 14. Dependencias

Antes de agregar una biblioteca:

1. Comprueba si realmente es necesaria.
2. Verifica si una dependencia existente resuelve el problema.
3. Evalúa su mantenimiento y compatibilidad.
4. Explica brevemente su propósito.

No incorpores frameworks pesados para resolver problemas pequeños.

Mantén las dependencias declaradas y reproducibles.

---

## 15. Control de cambios

Trabaja mediante incrementos pequeños y verificables.

No modifiques archivos ajenos a la funcionalidad solicitada.

No reformatees todo el repositorio por un cambio localizado.

No introduzcas refactorizaciones masivas sin aprobación.

No elimines funcionalidades existentes sin comprobar sus dependencias.

Conserva la compatibilidad con los contratos existentes, salvo que se apruebe modificarlos.

---

## 16. Proceso obligatorio de implementación

Para cada nueva funcionalidad:

1. Revisar el estado actual del repositorio.
2. Identificar el problema.
3. Presentar una propuesta breve.
4. Definir los criterios de aceptación.
5. Escribir las pruebas pertinentes.
6. Implementar la solución mínima.
7. Ejecutar las pruebas.
8. Revisar la calidad del código.
9. Eliminar código innecesario.
10. Documentar únicamente lo relevante.

No avances a otra funcionalidad mientras la actual tenga errores conocidos sin resolver, salvo autorización expresa.

---

## 17. Revisión del código

Después de cada implementación, realiza una revisión crítica.

Busca:

- Duplicación.
- Código muerto.
- Responsabilidades mezcladas.
- Acoplamiento innecesario.
- Abstracciones injustificadas.
- Consultas ineficientes.
- Errores ocultos.
- Dependencias innecesarias.
- Funciones demasiado extensas.
- Falta de pruebas.

Corrige los problemas relacionados con el cambio realizado.

No amplíes el alcance innecesariamente.

---

## 18. Comunicación

Comunícate en español.

Sé conciso y técnico.

No generes explicaciones excesivamente largas para cambios simples.

Antes de implementar, presenta:

- Qué se modificará.
- Por qué es necesario.
- Qué componentes se verán afectados.
- Cómo se verificará.

Después de implementar, informa:

- Qué cambió.
- Qué pruebas se ejecutaron.
- Qué resultados se obtuvieron.
- Qué limitaciones permanecen.

No afirmes haber ejecutado pruebas que no se ejecutaron.

No afirmes haber verificado funcionalidades que no fueron verificadas.

---

## 19. Principio final

No optimices para producir más código.

Optimiza para producir la solución más sencilla que satisfaga correctamente los requisitos.

Cada clase, función, archivo, dependencia y abstracción debe tener una razón concreta para existir.

La mantenibilidad, corrección, claridad y trazabilidad tienen prioridad sobre la complejidad técnica.