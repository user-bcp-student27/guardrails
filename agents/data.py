"""Datos y persistencia: perfil opcional para el equipo de siete."""
from graph.state import WorkflowState
from graph.context import _ctx, _const_block, _feedback_block

INSTRUCTIONS = 'Eres el responsable de Datos y persistencia. Define tablas, relaciones, restricciones y migraciones.\n'


def data_node(state: WorkflowState, invoke) -> dict:
    prompt = (INSTRUCTIONS
              + _const_block(state, "Conserva los datos existentes y respeta el alcance del Issue.")
              + "\nRepositorio: " + state["repo_name"]
              + "\nPlan aprobado:\n" + _ctx(state.get("out_plan", ""), 2500)
              + "\nEsquema y código existentes:\n" + _ctx(state.get("snapshot", ""), 6000)
              + _feedback_block(state)
              + "\nEntrega modelo y diccionario de datos, cardinalidades, claves primarias y foráneas, "
                "nulabilidad, unicidad e índices necesarios. Propón migraciones que conserven datos, "
                "reversión o recuperación y comprobaciones de integridad. No ejecutes migraciones ni inventes evidencia. "
                "Marca NO EJECUTADO lo no comprobado. Para archivos usa ### ruta/archivo.sql y bloque markdown, "
                "siguiendo las convenciones del producto. Si no requiere cambios de persistencia, justifícalo.")
    return {"out_data": invoke(prompt)}
