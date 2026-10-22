# Constitución — ReqFlow (Nuvexa Andina Digital)

Estas reglas son **NO negociables** y se aplican a cada output generado.
El harness las inyecta antes de cada ejecución, junto con los artefactos que
el participante dejó en su repo (Días 1-3): `REGLAS.md`,
`docs/GOOD_PRACTICES.md`, `docs/ENDPOINTS.md`, `HALLAZGOS.md`,
`openspec/specs/` y la Definition of Done. **Si un artefacto del repo es más
específico que esta constitución, prevalece el artefacto.**

Aplican a los tres componentes: `workshop-auth-backend` (Quarkus),
`workshop-requirements-api` (FastAPI) y `workshop-login-frontend` (Angular).

---

## R1 — Contratos preservados

Los contratos funcionales de ReqFlow no se rompen:

```
POST /auth/login
GET/POST /api/tasks
GET /api/summary
POST /api/requirements
```

La forma exacta de request y response es la de `docs/ENDPOINTS.md` y de los
escenarios de `openspec/specs/`. Un cambio de contrato solo es válido si el
Issue en alcance lo pide explícitamente.

---

## R2 — Secretos fuera del código

Ningún secreto, token ni credencial vive en el código ni tiene valor por
defecto fuera del sandbox. La configuración se lee del entorno
(`AUTH_TOKEN_SECRET`, settings, `.env` no versionado). Nunca se registran
tokens, secretos ni contraseñas en logs.

---

## R3 — Datos protegidos

- SQL siempre **parametrizado** (sin concatenar input del usuario).
- Contraseñas siempre **hasheadas** (bcrypt, argon2 o PBKDF2), nunca en texto plano.
- Validación de entrada en el borde (DTO / Pydantic / formulario).

---

## R4 — Tokens firmados y con expiración

El token de autenticación está **firmado** (HMAC o JWT) y tiene expiración
real (`exp`). Quien lo recibe valida firma y expiración antes de confiar en
sus claims. `expiresIn` es coherente con `exp - now`.

---

## R5 — Errores genéricos, sin fugas

- Nunca exponer stack traces, mensajes de excepción internos ni nombres de clases al cliente:
  manejador global de errores (`ExceptionMapper` en Quarkus, `exception_handler` /
  `HTTPException` en FastAPI) con mensajes genéricos.
- En Angular, mensajes como texto plano: sin `[innerHTML]` para contenido dinámico.

---

## R6 — Pruebas y trazabilidad

- Pruebas de caracterización **antes** de cambiar comportamiento; reutilizar las de `docs/TESTING.md`.
- Cobertura del código modificado ≥ 85 %. No se bajan quality gates.
- Solo se resuelve el Issue en alcance. Cada cambio cita su Issue (`#N`) y el
  hallazgo de origen (`H-xx`). Lo que esté fuera de alcance se reporta, no se corrige.

---

## Procedimiento ante violación

Si cualquier output viola una o más reglas:
1. Marcar el hallazgo como `CRÍTICO: R<N> — descripción`.
2. El Chapter lo reporta en su veredicto; `check_security_compliance()` lo
   registra en `evidence/compliance_report.json`.
3. El Chapter **no puede emitir VEREDICTO: APROBADO** si hay violaciones CRÍTICAS pendientes.
