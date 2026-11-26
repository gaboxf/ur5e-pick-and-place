# Manual de Usuario — Celda de Pick and Place UR5 guiada por visión artificial

> **Proyecto:** Desafío 2 — UPrO · Robótica con Orientación IA
> **Integrantes:** Alfredo Becerra · Claudio Flores · Lucio Vita
> **Sistema:** UR5 + gripper de 4 servos (Arduino) + cámara fija + dashboard web Flask

Este manual explica cómo instalar, calibrar, operar y diagnosticar la celda
robotizada completa. Está dirigido a un operario que nunca vio el proyecto:
si seguís los pasos en orden, la celda funciona.

---

## 1. Qué es el sistema

Una celda que **identifica, localiza, recoge y clasifica piezas** con un robot
industrial UR5, sin que nadie le enseñe la posición de cada pieza:

1. Una **cámara fija** (montada cenital) toma imágenes de la zona de trabajo.
2. Un **pipeline de visión en Python + OpenCV** detecta la pieza, determina su
   categoría (por color, segmentación HSV) y su posición.
3. Una **homografía píxel → UR5** convierte la posición en píxeles a
   coordenadas en metros del sistema de referencia del robot.
4. El **UR5** va a la posición, y un **gripper de 4 servomotores con puntas
   flexibles impresas en TPU** (controlado por un Arduino por puerto serie)
   agarra la pieza.
5. El robot transporta la pieza al **destino correspondiente a su categoría**
   y queda listo para un nuevo ciclo.

Todo el ciclo se dispara desde un **dashboard web** (Flask) que también muestra
el estado del hardware, la detección en vivo y las estadísticas de producción.

### Categorías de piezas

| Categoría | Color | Destino |
|---|---|---|
| A | Roja | Depósito A |
| B | Azul | Depósito B |

---

## 2. Requisitos e instalación

### 2.1 Hardware

- Robot **Universal Robots UR5** (o UR5e) en la red, o emulador **URsim**
  escuchando en `127.0.0.1:30004`.
- **Arduino UNO** con el firmware del gripper, conectado por USB (puerto COM).
- **Cámara USB** (webcam) montada **cenital y fija** sobre la mesa.
- Piezas de al menos dos categorías diferenciables por color.

### 2.2 Software (Windows)

1. Instalá **Python 3.10–3.12 (64 bits)** con "Add Python to PATH".
2. En PowerShell, dentro de la carpeta del proyecto:

   ```powershell
   py -3.12 -m venv .venv    # o py -3.11 / py -3.10 (nunca 3.13+)
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. Instalá el driver **CH340** si el Arduino no aparece en el Administrador
   de dispositivos (detalles en `docs/GUIA_WINDOWS.md`).

### 2.3 Configuración

Toda la configuración vive en **`vision/config.json`** (única fuente de
parámetros, compartida por visión, robot y gripper):

| Clave | Qué define |
|---|---|
| `indice_camara` | Índice de la webcam (0, 1, …) |
| `roi` | Recorte de la zona de trabajo (x, y, ancho, alto) |
| `rangos_hsv` | Rangos HSV por categoría (A, B, C, D) |
| `puntos_px` / `puntos_ur5` | Los 4 puntos de la homografía |
| `gripper_port` | Puerto COM del Arduino (ej. `COM3`) |
| `destinos` | Coordenadas UR5 de cada depósito de categoría |
| `z_seguro`, `z_pick`, `z_deposit` | Alturas de tránsito, agarre y depósito |

Para el emulador del robot (`URsim`), la IP es `127.0.0.1` y el puerto `30004`.

---

## 3. Arranque

1. Conectá el Arduino por USB y verificá el puerto COM.
2. Iniciá el URsim (o verificá que el UR5 real esté en la red).
3. En PowerShell, en la carpeta del proyecto:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   python core/dashboard_web.py
   ```

4. Abrí el navegador en <http://localhost:5000>
   (desde otro equipo de la red: `http://<IP-de-la-PC>:5000`).

Al arrancar, la consola informa el estado del hardware:

```
WebDashboard: Hardware -> cámara:True gripper:False robot:False
```

Es normal ver `gripper:False` (modo SIMULADO) si no hay Arduino, y
`robot:False` (modo MOCK) si no hay URsim/UR5: el sistema degrada y sigue
funcionando a nivel software, sin enviar movimientos físicos.

---

## 4. Calibración (orden obligatorio)

La cámara, la ROI y la homografía forman una **cadena de dependencias**.
Calibrar en desorden produce recogidas desplazadas o rechazos de
"pieza fuera de la zona de trabajo autorizada".

```
1. Cámara fija y enfocada (no se mueve NUNCA después)
2. Fijar la ROI          → panel "Ajustar ROI"
3. Calibrar homografía   → panel "Homografía (píxel → UR5)"
4. Calibrar HSV          → panel "Colores (calibración HSV)"
5. Verificar con una pieza apoyada en la mesa
6. Recién entonces: ejecutar ciclos
```

### Reglas que no se pueden saltear

1. **La cámara no se mueve después de calibrar.** Un golpe o un reencuadre
   invalida toda la homografía.
2. **La ROI se fija ANTES de la homografía.** Los clics del asistente se
   guardan relativos al recorte; si la ROI cambia después, recalibrá.
3. **Si cambiás la ROI, recalibrá la homografía.**
4. **La homografía solo vale para piezas apoyadas en la mesa.** Una pieza en
   el aire se proyecta a una coordenada desplazada y es rechazada (correcto).
5. **Verificá el error antes de operar:** apoyá una pieza en una posición no
   usada para calibrar y compará la coordenada del panel Detección con la
   posición real.

### 4.1 Ajustar ROI (panel "Ajustar ROI")

Define el recorte de la imagen donde se buscan piezas. Todo lo que está fuera
del rectángulo se ignora (evita falsas detecciones del fondo).

- Mover: botones **Izquierda / Arriba / Abajo / Derecha**.
- Tamaño: **+ Grande / − Pequeña**.
- **Mostrar / ocultar ROI** dibuja el rectángulo sobre el video.
- **Aplicar (guardar)** guarda la ROI en `vision/config.json` para que
  persista al reiniciar. **Reset** vuelve al valor de fábrica.

### 4.2 Homografía (panel "Homografía (píxel → UR5)")

Traduce píxeles de la imagen a metros del robot. Necesitás **4 marcas
físicas** en la mesa (por ejemplo impresiones 3D o calcos) cuyas coordenadas
UR5 conozcas.

1. Apretá **Iniciar calibración**. El panel muestra `CALIBRANDO — 0/4`.
2. Hacé clic sobre el video en cada marca, en este orden:
   **1 sup-izq → 2 sup-der → 3 inf-der → 4 inf-izq**. El asistente convierte
   cada clic a coordenadas de la ROI automáticamente.
3. **Deshacer punto** corrige el último clic; **Cancelar** aborta.
4. **Aplicar (guardar)** recalcula la matriz y la guarda.

Si la homografía está mal, el robot va siempre desplazado hacia un lado.

### 4.3 Colores (panel "Colores (calibración HSV)")

Define qué rangos de color corresponden a cada categoría. Depende de la
iluminación del laboratorio: recalibrá si cambia la luz.

1. Seleccioná el color con **Rojo (A) / Azul (B) / Verde (C) / Violeta (D)**
   (el botón elegido queda resaltado).
2. Activá **Máscara en vivo sí/no** y poné una pieza en la mesa.
3. Ajustá con **Matiz − / Matiz +**, **+ Ancho / − Ancho** (tolerancia),
   **+ Sensible / − Sensible**, **− Área mín. / + Área mín.** y
   **− Confianza / + Confianza**, hasta que en la máscara **solo se pinte la
   pieza real**.
4. **Aplicar (guardar)** guarda los rangos en `vision/config.json`.

---

## 5. El dashboard: qué muestra cada sección

Al abrir <http://localhost:5000> se ve el panel de monitoreo. En el header hay
**3 LEDs de hardware** (CÁM · GRP · UR5): verde = conectado, rojo = no
conectado. Se ven sin scrollear, para diagnóstico a distancia.

### 5.1 Estado de la celda

Chequeo de salud en vivo de los 3 subsistemas. Antes de ejecutar cualquier
ciclo, mirá este panel para saber si el sistema puede operar.

| Fila | Qué muestra | Cómo lo sabe |
|---|---|---|
| Visión | `PIEZA DETECTADA` o `SIN PIEZA` | Resultado del último frame procesado (pieza con confianza ≥ umbral) |
| Cámara | `OK (índice N)` o `SIN CÁMARA` | Si OpenCV pudo abrir la webcam |
| Gripper | `OK` o `SIMULADO` | Si el Arduino respondió por puerto serie |
| Robot UR5 | `OK` o `MOCK` | Si se conectó al puerto 30004 (real o URsim) |
| Último comando | `ciclo`, `abrir`, `home`… | Eco del último comando recibido |

Si apretás "Ejecutar ciclo" y el robot no se mueve, este panel te dice por qué
en 2 segundos. Si hay una **parada de seguridad activa**, acá aparece la
alerta roja y todos los botones quedan bloqueados salvo "Reiniciar parada".

### 5.2 Control (columna lateral)

Los botones que operan la celda. Todos envían un comando a `/api/command`:

| Botón | Acción |
|---|---|
| **Ejecutar ciclo** | Ciclo automático completo con la pieza detectada |
| **Abrir / Cerrar gripper** | Accionamiento manual del gripper |
| **Home** | Mueve el robot a la posición segura de espera |
| **Parar** | Parada de seguridad (bloquea los comandos) |
| **Soltar pieza** | Invierte el movimiento en curso y abre el gripper de forma controlada |
| **Reiniciar parada** | Libera una parada activa |
| **Ciclo roja (A) / Ciclo azul (B)** | Ciclo filtrado a una sola categoría |
| **Escanear / Usar** | Detecta y cambia la cámara activa |

### 5.3 Detección

Detalle de la última detección de visión: categoría (A/B), posición en metros
en coordenadas del robot (`x_ur5`, `y_ur5` — producto de la homografía),
confianza (0–1) y motivo de fallo si no detectó nada. Es el panel para
**calibrar y depurar la visión**: apoyás una pieza y verificás que la
coordenada reportada coincida con la real.

### 5.4 Producción

Contadores de la sesión: **ciclos OK**, **errores** y piezas clasificadas por
categoría (A/B). Es la evidencia cuantitativa de la demo.

### 5.5 Paneles de calibración (colapsables)

**Homografía**, **Ajustar ROI** y **Colores (HSV)** arrancan plegados para no
ocupar pantalla durante la operación; se despliegan solo al calibrar. El
estado de cada editor (puntos, rangos, píxeles captados) se muestra dentro
del panel y se refresca en tiempo real.

---

## 6. Flujo de operación (día a día)

```
1. Estado de la celda → ¿todo conectado?      (si no: no sigas)
2. Homografía + ROI + HSV → ¿calibrado?       (una vez por sesión)
3. Detección → ¿ve la pieza bien?             (prueba con una pieza)
4. Control → Ejecutar ciclo                   (operación)
5. Producción → resultado                     (evidencia)
```

Para la demo con el docente:

1. Colocá la pieza en una posición cualquiera dentro de la zona (puede ser
   una posición distinta cada vez — el sistema lo resuelve).
2. Verificá en **Detección** que la pieza se identifica con la categoría y
   coordenada correctas.
3. **Ejecutar ciclo** (o **Ciclo roja (A)** / **Ciclo azul (B)** para forzar
   una categoría).
4. El robot desciende con precisión RTDE ($\le 3\text{ mm}$), cierra el gripper y eleva la herramienta.
5. **Confirmación visual automática (estado `VERIFICANDO`):** La cámara revisa que la pieza ya no esté en la mesa.
   - Si la pieza fue levantada: se transporta y deposita en su destino; **Producción** suma el ciclo.
   - Si la pieza resbaló y quedó en la mesa: el robot abre el gripper, vuelve a Home y cancela el viaje en vacío para evitar colisiones.

**Si no se detecta una pieza válida, el sistema NO inicia trayectoria de
recogida con coordenadas inexistentes** — el panel Detección muestra el
motivo y el ciclo no arranca. Este es un requisito de la consigna.

---

## 7. Seguridad

- **El E-STOP físico del UR5e es la seguridad primaria.** ⚠️ **ACTUALIZACIÓN:** Arduino **NO** tiene botón físico de parada.
- La única parada de emergencia válida es el E-Stop del Teach Pendant UR5.
- La parada por software del dashboard es un complemento, no un reemplazo.
- En caso de emergencia: Usar E-Stop Teach Pendant inmediatamente.
- Nadie ingresa al espacio de movimiento del robot durante la ejecución.
- Piezas se colocan o retiran solo con el robot en Home o detenido.
- Todos los movimientos respetan la zona de trabajo autorizada: la visión
  rechaza coordenadas fuera de la ROI/zona calibrada.
- Toda prueba física se realiza bajo supervisión del docente.
- El firmware del gripper incluye un watchdog: si la PC deja de responder,
  el gripper no queda accionado indefinidamente.

---

## 8. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| LED CÁM rojo, video negro | Cámara mal indexada u ocupada por otro programa | Escanear cámaras en Control; cerrar otras apps |
| LED GRP amarillo (`SIMULADO`) | Arduino no conectado o COM incorrecto | Verificar COM en `vision/config.json` y cable USB |
| **Botón físico Arduino** | **NO EXISTE** (eliminado según especificaciones finales) | **Parar SOLO con E-Stop Teach Pendant** - No buscar botón Arduino |
| LED UR5 amarillo (`MOCK`) | URsim apagado o IP/puerto incorrecto | Iniciar URsim en 127.0.0.1:30004 |
| "pieza fuera de la zona de trabajo autorizada" con pieza apoyada | ROI cambiada después de calibrar | Recalibrar homografía (regla 3) |
| Robot recoge desplazado | Homografía desactualizada o cámara movida | Repetir calibración de homografía |
| Detección con mala categoría | Rangos HSV fuera de la luz actual | Recalibrar HSV con la máscara en vivo |
| Nada detecta y conf 0 | Área mínima o confianza muy altas | Bajar umbral en el panel Colores |
| Botones bloqueados | Parada de seguridad activa | Revisar causa, luego "Reiniciar parada" |

---

## 9. Documentación relacionada

| Documento | Contenido |
|---|---|
| `README.md` | Arquitectura del módulo de visión y reglas de calibración |
| `docs/GUIA_WINDOWS.md` | Instalación en las PC del laboratorio |
| `docs/Arranque.md` | Arranque completo del sistema paso a paso |
| `docs/Seguridad_E_Interfaz_Web.md` | Interfaz web y capa de seguridad |
| `docs/Guia_Ajustes_Laboratorio.md` | Ajustes finos de parámetros |
| `docs/plan_desafio2_ur5.md` | Plan de trabajo del equipo |
