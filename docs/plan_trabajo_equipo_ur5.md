# Plan de trabajo del equipo — UR5 Pick and Place (3 integrantes)

> Meta: que los tres avancen a la par, cada uno dueño de UN dominio, **sin repetir trabajo** y sin bloquearse entre sí.

---

## 1. Reglas de oro (anti-duplicación)

1. **Un dominio = un único dueño.** Nadie toca el módulo ajeno sin avisar.
2. **Los módulos se comunican por contratos definidos UNA VEZ** (sección 4). Si hay que cambiarlos, se conversa y se actualiza este documento — nadie cambia el contrato "de su lado".
3. **Un solo repo** (GitHub) con una carpeta por módulo: nadie programa "sobre el módulo del otro".
4. **Checkpoint diario de 10 min** (WhatsApp o presencial): qué hice, con qué me trabé, si necesito a alguien.
5. Todo lo que cruce 2 dominios se resuelve **sentados los dos**, no por separado.

---

## 2. Roles

| | Persona | Dominio (dueño) | NO se mete en |
|---|---|---|---|
| **A** | *(nombre)* | **Visión**: cámara, detección HSV, clasificación, homografía píxel→UR5 | Trayectorias, electrónica |
| **B** | *(nombre)* | **Robot + Integración**: URSim/URScript, máquina de estados, unión de todo, config.json | Fotometría, servo wiring |
| **C** | *(nombre)* | **Gripper + piezas**: Arduino, protocolo serial, calibración de abertura, piezas físicas | Cálculo de coords, homografía |

*(Completar nombres en E1; A/B/C son códigos para este documento.)*

---

## 3. Mapa de código — dueño por carpeta

```
repo/
├── vision/        → A   (segmentación, centroides, homografía)
├── robot/         → B   (URScript, URSim, funciones mover())
├── integracion/   → B   (máquina de estados, orquestador principal)
├── gripper/       → C   (arduino/robot_motores.ino, cliente serial Python)
├── config.json    → B   (parámetros de TODOS los módulos — una sola fuente)
├── piezas/        → C   (STL, medidas, fotos de ref.)
├── tests/         → cada uno testea su propio módulo (A→tests/vision/, etc.)
└── README.md      → B arma el esqueleto; cada uno escribe SU sección
```

Repo en GitHub, un branch por persona (`vision`, `robot`, `gripper`), merge a `main` con **revisión del dueño del módulo afectado**. Nada se mergea directo a `main`.

---

## 4. Contratos entre módulos (definir UNA VEZ, sentados los 3 en el Encuentro 1)

### 4.1 Visión → Integración (A entrega, B consume)
`deteccion.json` / dict:
```json
{
  "ok": true,
  "categoria": "A|B",
  "x_px": 502, "y_px": 318,
  "x_ur5": 0.412, "y_ur5": 0.085,
  "conf": 0.91,
  "motivo_fallo": "sin pieza | pieza fuera de zona | segmentación débil"
}
```
Regla: si `ok=false`, integración **no** mueve el robot.

### 4.2 Visión (calibración) → Homografía
`homografia.json` con los 4 puntos de referencia: imagen (px) ↔ robot (m). Dueño A; B solo lo **lee**.

### 4.3 Integración → Gripper (B ↔ C, definido por ambos)
Serial (baud acordado, ej. 115200):
```
OPEN\n            → "DONE\n" (confirma apertura)
CLOSE\n           → "DONE\n" (confirma agarre)
STATUS\n          → "READY|BUSY|ERROR\n"
(no responde en 2 s → integración considera ERROR y detiene el ciclo)
```
Dueño del sketch Arduino: C. Dueño del cliente Python: C entrega `gripper_client.py` con funciones `open()`, `close()`, `status()` que B usa **sin mirar adentro**.

### 4.4 Integración → Robot
B expone funciones herramientas: `mover_a(x, y, z, r)` donde z/r salen del plano calibrado (config.json). El UR5 recibe comandos por ur_rtde o socket puerto 30002 (elección de B, documentada en README del módulo robot).

---

## 5. Matriz de tareas por encuentro

### Encuentro 1 — Arranque
| Persona | Tareas | "Listo" cuando… |
|---|---|---|
| A | Fotos de prueba de piezas (HSV revisado en luz del lab); esbozo de homografía | Segmenta rojo/azul con 2 fotos reales |
| B | Leer docs UR5; instalar/pruebar URSim; crear repo y branches; esqueleto de config.json | Robot se mueve a un waypoint en URSim |
| C | Conseguir/pedir Arduino + fuente 5 V; medir span del gripper; diseñar/comprar piezas | Tengo 1 pieza por categoría en mano |

**Junto:** definir los contratos de la sección 4 (1 h, los 3 sentados). Designar interlocutor con el docente por módulo: A → visión, B → robot, C → gripper/electrónica.

### Encuentro 2 — Primer prototipo
| Persona | Tareas | "Listo" cuando… |
|---|---|---|
| A | Homografía calibrada con los 4 puntos + test de error en posiciones nuevas (error < tolerancia) | La app tira (x_ur5, y_ur5) correcto con pieza donde quiera |
| B | Trayectorias dry-run en URSim; mover() recibiendo coords de config; check de límites de zona | Los 4 puntos de ref. existen como posiciones del robot |
| C | Arduino responde OPEN/CLOSE/STATUS con ACK + watchdog; gripper físico probado | Gripper abre/cierra por comando desde Python |

### Encuentro 3 — Integración
| Persona | Tareas | "Listo" cuando… |
|---|---|---|
| A | Módulo visión end to end + monitoreo en pantalla (marcar la pieza detectada) | `deteccion.json` sale estable, 10 detecciones seguidas |
| B | Máquina de estados completa; integrar detección → trayectoria | Handoff completo detectado→movimiento en dry-run |
| C | Ajuste de abertura con pieza real; protocolo de seguridad revisado; lote de piezas de repuesto | Agarre→transporte→suelta con pieza real (con B) |

**Junto:** primera corrida integrada completa (los 3 en el lab), analizada con pasos, sin apuros.

### Encuentro 4 — Demo
Juntos: ensayo del flujo completo ×3 (posiciones nuevas, ambas categorías, sin pieza → mensaje "no evaluable"). Cada uno explica su módulo Y puede describir los otros dos (para las entrevistas del docente).

---

## 6. Tareas compartidas — un dueño, todos ayudan

| Tarea | Dueño | Los demás |
|---|---|---|
| Seguridad en el lab (protocolos, soporte de cámara firme, zona delimitada) | **C** | todos respetan; prohibido mover la cámara tras la calibración |
| README + documentación | esqueleto **B**; cada uno su sección | revisan entre todos |
| Video demo | film: **C**; edición: **A** | todos en la filmación |
| Repositorio (branches, PRs) | **B** | todos suben solo a su branch |
| Contacto docente (horario robot, dudas) | turnos: 1° A, 2° B, 3° C — anotar en README | informe después de cada toma |

---

## 7. Control de ritmo (para "ir parejos")

- Fechas internas **2 días antes** de cada encuentro, para cada módulo.
- Checkpoint diario mínimo: una línea de avance y bloqueos (WhatsApp grupal).
- Si alguien se cuelga >2 días con su módulo: checkpoint presencial de pareja para desbloquear (nunca se termina el módulo del otro sin avisarlo).
- Review cruzada para aprender el sistema completo: A revisa el gripper, B revisa la visión, C revisa el robot (lo exigen las entrevistas del docente).

---

## 8. Fechas internas (completar)

| Hito | Fecha |
|---|---|
| Contratos definidos (sección 4) | ___ |
| Piezas físicas listas | ___ |
| Homografía calibrada + test error | ___ |
| UR5 sirviendo mover() en dry-run | ___ |
| Gripper por serial funcional | ___ |
| Integración end-to-end | ___ |
| Pruebas completas ×3 corridas | ___ |
| Video + README final | ___ |
| Prototipo mínimo demostrable antes del Encuentro 2 | ___ |
