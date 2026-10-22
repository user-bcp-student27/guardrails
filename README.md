# Guardrails para equipos

Los agentes parten con instrucciones básicas. Sigue [Mejora tu agente](MEJORA_TU_AGENTE.md) para completar el ejercicio y consultar resultados desde el portal.

Base de trabajo del Lab 4, S2. Clona el repositorio de producto del equipo,
propone una refactorización con seis nodos de LangGraph y genera un PR mediante GitHub CLI.
Conserva el harness, los guardrails de entrada y salida, las evaluaciones y el ciclo de revisión.

## Editar tu agente

Los agentes son básicos: un objetivo breve, una responsabilidad por rol y una salida concreta.
Usa una corrección pequeña, por ejemplo rechazar títulos vacíos al crear tareas.
Ajusta ese ejemplo al issue de tu equipo.
Testing propone un caso válido y otro inválido; Security revisa entradas y secretos;
Chapter compara las entregas con el requisito. Los prompts conservan los formatos
que necesita el flujo para guardar código y revisar resultados.

Cada participante edita el archivo de su rol:

- `agents/architect.py`
- `agents/backend.py`
- `agents/frontend.py`
- `agents/testing.py`
- `agents/security.py`
- `agents/chapter.py`

El orden es `architect → backend → frontend → testing → security → chapter`.
Chapter reemplaza al LT como revisor final. La aprobación humana se conserva entre etapas.
Consulta [TEAM.md](TEAM.md) para crear el repo de tu equipo y repartir los archivos.

Los nodos están implementados en esos archivos. `graph/nodes.py` conecta cada implementación
con el modelo; `graph/graph.py` define el orden y el ciclo. El arquitecto reúne diagnóstico y plan.
El Chapter revisa backend, frontend, pruebas y seguridad. Si devuelve observaciones,
el ciclo repite desde backend hasta `MAX_REVIEW_ROUNDS`.

## Preparar el entorno

Requiere Python 3.11 a 3.13, Git y acceso a GitHub Copilot.
Usa Python 3.12 durante el taller y ejecuta los comandos en Bash. En Windows, abre Git Bash.

```bash
python -m venv .venv
if [ -f .venv/Scripts/activate ]; then
  source .venv/Scripts/activate
else
  source .venv/bin/activate
fi
python -m pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
```

Si en macOS o Linux tu instalación se llama `python3.12`, úsala para crear el entorno. Una vez activado, ejecuta `python`.

Instala [Copilot CLI](https://github.com/features/copilot/cli), abre `copilot` y ejecuta `/login`.
El SDK usa esa sesión con `COPILOT_USE_LOGGED_IN_USER=true`.
El modelo predeterminado es `gpt-6-luna` con esfuerzo `high`. Cada llamada consume uso del plan de Copilot.
Puedes descargar su runtime antes del taller con `python -m copilot download-runtime`.

## Configurar el repositorio de producto

Autentica GitHub CLI con `gh auth login`. Configura `GITHUB_OWNER`, `GITHUB_REPO`
y `GITHUB_ISSUE` en `.env`. Apunta al repo de producto del equipo que vas a refactorizar.
Necesitas permiso para crear una rama y un PR en ese repo.

Este repo guarda los agentes del laboratorio. El repo configurado en `.env` guarda
el código que esos agentes revisan. Puede ser el producto construido en S1 o el de los días anteriores.
El flujo principal usa Git y `gh`; no necesita levantar un servidor MCP Git.

## Ejecutar el flujo

```sh
python main.py
```

Confirma el tipo de proyecto. Después de cada nodo, usa `s` para aceptar su salida,
`r` para reejecutarlo o `n` para detener el flujo. Los cambios de backend, frontend y tests
se extraen en el clon local del producto. Al finalizar, el flujo valida la salida,
publica la rama y abre un PR. Revisa las propuestas antes de aprobar cada etapa.

Los agentes generan texto; las pruebas propuestas deben ejecutarse en el proyecto
y su salida real se adjunta al PR. Llegar al límite de rondas no equivale a una aprobación.
Los guardrails de salida detectan estructura y patrones; no reemplazan esa validación humana.

Los artefactos se guardan en `output/` y `evidence/`:

- `architecture_report.md`: diagnóstico y plan del arquitecto.
- `backend_refactor.md` y `frontend_refactor.md`: propuestas de implementación.
- `testing_report.md`: pruebas propuestas y comandos.
- `security_report.md`: riesgos y verificaciones pendientes.
- `chapter_report.md`: feedback y veredicto final.
- Las rondas adicionales usan el sufijo `_r<N>.md`.
- `.checkpoint.json`: estado local para retomar la ejecución.
- `evidence/`: compliance, resultados de evals y URL del PR.

## Verificar los nodos sin servicios externos

```sh
python tools/verify.py
```

Los checks recorren el grafo real con respuestas simuladas del modelo. Verifican
pausas humanas, los seis roles, feedback, límite de rondas, reanudación y guardrails.
No llaman a Copilot, MCP ni GitHub.

## Evaluar una ejecución real

Con el producto y Copilot configurados:

```sh
python evals/eval_basic.py
```

Este comando ejecuta el pipeline real y consume llamadas al modelo. Guarda los resultados
en `evidence/eval_results.json`. Las evaluaciones HTTP de `evals/contract_evals.py`
requieren además que los servicios del producto estén levantados.

Consulta [SOURCE.md](SOURCE.md) para ver el origen de esta copia.

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
