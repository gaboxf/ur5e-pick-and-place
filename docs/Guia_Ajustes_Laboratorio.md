# Guía de Operación y Ajustes — Desafío 2: UR5

Esta guía contiene los parámetros críticos para configurar y ajustar el sistema en el laboratorio cuando el robot esté conectado.

## 1. Ajustes de Entorno (`.env`)
Modifica este archivo para cambiar entre modo simulación y real:

- `ROBOT_MODE`: Cambiar a `real` para conectar al hardware. `mock` para simulación.
- `UR5_IP`: IP estática del robot (ej. `192.168.1.10`).
- `GRIPPER_PORT`: Puerto COM del Arduino (ej. `COM3` en Windows, `/dev/ttyACM0` en Linux).
- `ROBOT_VEL` / `ROBOT_ACC`: Ajustar para mayor/menor velocidad en los movimientos del UR5.

## 2. Ajustes de Visión y Coordenadas (`vision/config.json`)
Si el robot no está posicionándose correctamente sobre la pieza o las zonas de depósito, ajusta estos valores:

- **ROI (Region of Interest):** Define el recorte de la imagen (`roi_desde_x`, `roi_hasta_x`, etc.).
- **Homografía (`puntos_px` vs `puntos_ur5`):** 
    - `puntos_px`: Coordenadas en la imagen.
    - `puntos_ur5`: Coordenadas reales en metros (cero en la base del robot).
    - *Tip: Si el robot se queda corto o se pasa, ajusta estos puntos.*
- **Zona de Trabajo:** `zona_trabajo_min_x/y` y `max_x/y` define dónde permitimos que el sistema busque piezas.

## 3. Ajustes de Movimiento (`vision/config.json`)
Los parámetros de altura son críticos para no chocar la mesa o el gripper:

- `z_seguro`: Altura de transporte (debe ser mayor a cualquier pieza).
- `z_pick`: Altura exacta para cerrar el gripper sobre la pieza.
- `z_deposit`: Altura para soltar la pieza.

## 4. Vocabulario de Voz (`core/voice_control.py`)
Si el sistema no entiende tus comandos o quieres agregar nuevos:

1. Abrir `core/voice_control.py`.
2. Buscar el método `clasificar_intencion(self, texto)`.
3. Agregar un nuevo patrón `re.search` con las palabras clave deseadas.
4. **Importante:** Si agregas una acción nueva, asegúrate de añadirla en `core/dashboard.py` dentro de `procesar_eventos_voz`.

## 5. Troubleshooting (Lista de chequeo en el laboratorio)
- **¿No detecta piezas?** Revisar los umbrales de color HSV en `vision/config.json` (`rojo_bajo1`, `azul_bajo`, etc.) si la iluminación del laboratorio es distinta a la de tu casa.
- **¿Robot bloqueado?** Si `core/dashboard.py` se congela al arrancar, es probable que la cámara esté siendo usada por otra app o el puerto COM del Arduino esté ocupado.
- **¿No escucha bien?** Ajusta `recognizer.energy_threshold` en el `__init__` de `core/voice_control.py`. Si hay mucho ruido, auméntalo.

## 6. Comandos rápidos (Cheat Sheet)
| Comando de voz | Acción en modo REAL |
|---|---|
| *"Clasificar"* | Ejecuta el ciclo completo |
| *"Agarra la pieza roja"* | Clasifica en Categoría A |
| *"Agarra la pieza azul"* | Clasifica en Categoría B |
| *"Abri gripper"* | Abre pinza física |
| *"Cerra gripper"* | Cierra pinza física |
| *"Detener"* / *"Alto"* | Parada de seguridad inmediata |
| *"Estado"* | Informe vocal de la mesa |

---
*Nota: Siempre realice las primeras pruebas con el UR5 en modo de velocidad reducida (Speed Slider en el Teach Pendant).*
