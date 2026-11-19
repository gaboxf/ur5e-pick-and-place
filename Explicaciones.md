1. ¿Qué son las medidas en amarillo arriba en el HUD?
Arriba a la derecha del video dice: B (302.8, 272.0) mm
Significado:
B: Categoría de la pieza detectada (Azul).
(302.8, 272.0) mm: Son las coordenadas horizontales calculadas por la homografía expresadas en milímetros, medidas en el sistema de coordenadas de la base del robot UR5.
𝑋
=
302.8
 mm
X=302.8 mm y 
𝑌
=
272.0
 mm
Y=272.0 mm.
2. En el panel DETECCIÓN: ¿Qué son X (m) e Y (m)?
En el panel ves: X (m): 0.303 e Y (m): 0.272.
Son exactamente las mismas coordenadas de arriba, pero expresadas en metros (que es la unidad estándar que usa la librería ur_rtde del UR5):
0.303
 m
=
30.3
 cm
0.303 m=30.3 cm
0.272
 m
=
27.2
 cm
0.272 m=27.2 cm
¿A qué se refieren físicamente en la mesa?
El origen 
(
0
,
0
,
0
)
(0,0,0) es el centro de la base del robot UR5.
𝑋
X (eje longitudinal): Indica qué tan adelante del robot está la pieza (
+
30.3
 cm
+30.3 cm hacia el frente).
𝑌
Y (eje transversal): Indica qué tan hacia el lateral está la pieza (
+
27.2
 cm
+27.2 cm hacia un costado).
Es la coordenada 
(
𝑋
,
𝑌
)
(X,Y) exacta a donde el efector del robot viaja para situarse verticalmente sobre la pieza.
3. En el MAPA DE TRABAJO:
POS 0.400, -0.200, 0.250 m:
Es la posición actual del efector (TCP) del robot en 3D: 
(
𝑋
,
𝑌
,
𝑍
)
(X,Y,Z) en metros.
Fijate que como acabás de ejecutar un ciclo de la pieza azul (B), el robot la llevó a su destino:
𝑋
=
0.400
 m
X=0.400 m (40 cm hacia adelante).
𝑌
=
−
0.200
 m
Y=−0.200 m (zona de descarga asignada a la Categoría B).
𝑍
=
0.250
 m
Z=0.250 m (la cota z_seguro de tránsito, 25 cm de altura sobre la mesa).
VEL 0 mm/s:
Es la velocidad lineal actual del TCP. Como el robot completó el movimiento y está detenido esperando órdenes, marca 
0
 mm/s
0 mm/s.
DEST 0 mm:
Es la distancia que le falta recorrer para llegar a la meta. Como ya llegó a destino, la distancia restante es 
0
 mm
0 mm.
Limpiar rastro:
La línea azul que ves en el mapa es la trayectoria histórica que recorrió el robot en la mesa. Al tocar el botón Limpiar rastro, se borra ese dibujo para que el mapa quede limpio si querés monitorear un nuevo movimiento desde cero.