# Trabajar en equipo

## Crear el repositorio del equipo

El líder crea el repositorio en su cuenta personal desde esta plantilla:

```sh
gh repo create guardrails --template NTTData-Academy/guardrails --public --clone
```

Agrega a tus compañeros como colaboradores con permiso de escritura en el repo del equipo.
Los demás integrantes clonan ese mismo repo:

```sh
gh repo clone <CAPTAIN>/guardrails
cd guardrails
```

## Repartir los archivos

Anota un responsable por archivo. El equipo tiene seis personas y cada una edita su propio agente.

- `agents/architect.py`: responsable pendiente. Define la solución y el contrato de API.
- `agents/backend.py`: responsable pendiente. Implementa el backend.
- `agents/frontend.py`: responsable pendiente. Implementa el frontend.
- `agents/testing.py`: responsable pendiente. Diseña las pruebas y sus comandos de ejecución.
- `agents/security.py`: responsable pendiente. Revisa riesgos y propone correcciones verificables.
- `agents/chapter.py`: responsable pendiente. Revisa todas las entregas y emite el veredicto final.

## Registrar responsables para el dashboard

Copia `team.example.json` a `team.json`, reemplaza los seis valores por usuarios reales de GitHub distintos y publica ese archivo en `main`. El dashboard lee `roles` para asociar cada agente con su responsable. Los nombres de ejemplo no son participantes.

## Entregar un cambio

1. Acuerden un requerimiento y sus criterios de aceptación.
2. Crea tu rama, por ejemplo `git switch -c agents/backend`.
3. Edita las instrucciones de tu agente en su archivo. Coordina los cambios de contratos con el arquitecto.
4. Ejecuta `python tools/verify.py`.
5. Publica tu rama y abre un PR hacia `main` del repo del líder. No envíes el PR al template de la organización.
6. Pide a un compañero que revise el cambio antes de integrarlo.

Una persona ejecuta el pipeline integrado desde `main`. El equipo participa en las aprobaciones
humanas y aporta feedback en cada etapa. Cada integrante conserva su propia `.env`, autenticación
y entorno virtual. No compartan checkpoints entre ejecuciones de distintas computadoras.

Los agentes generan texto y archivos. El agente de pruebas propone tests y comandos;
una persona los ejecuta y adjunta la salida real al PR. Un informe del modelo no demuestra que las pruebas pasaron.

## Datos y persistencia para el grupo de siete

Solo el equipo de siete añade un responsable de `agents/data.py`, con su propio PR.
Consulta [Mejora tu agente](MEJORA_TU_AGENTE.md) para las responsabilidades, puntos de partida y entregables de todos los roles.

El líder configura `GUARDRAILS_INCLUDE_DATA=true` en `.env` antes de un flujo nuevo.
El orden es Arquitectura → Datos → Backend → Frontend → Pruebas → Seguridad → Chapter.
Los equipos de seis dejan la variable en `false`. En `team.json`, solo el equipo
de siete añade `"data": "usuario-real"` dentro de `roles`, con un usuario distinto.

Datos define tablas, relaciones, restricciones y migraciones desde el plan aprobado
y el esquema existente. Entrega `output/data_report.md`; el SQL usa el formato
`### ruta/archivo.sql` y un bloque de código. Tras aprobación humana, el runtime
extrae los archivos a `output/` y al clon temporal del producto; no ejecuta SQL.
Backend usa este contrato. Pruebas, Seguridad y Chapter revisan la propuesta.
Si Chapter deja observaciones, la siguiente ronda vuelve a Datos antes de Backend.
Las rondas siguientes guardan `data_report_r<N>.md` sin pisar el primer informe.

Se mantienen aprobación, rechazo, reintento y reanudación. Conserva `output/` con
otro nombre antes de cambiar entre seis y siete nodos: los checkpoints de modos
distintos no se mezclan. Los archivos del producto siguen requiriendo revisión y
pruebas humanas antes de publicar su PR. No ejecutes migraciones sin verificar
respaldo, integridad y reversión sobre una copia del producto.
