"""Verifica la logica del parser del sketch de calibracion replicando el C++.

Reproduce separar() y el despacho de comandos para confirmar que cada
comando se interpreta como se espera (en especial TAP, que antes chocaba
con la rama 'T').
"""

import sys

PULSO_MIN, PULSO_MAX = 900, 2100


def servo_us(angulo):
    """Mapeo exacto de la libreria Servo (v1.2.x) en un UNO.

    Servo.h: MIN_PULSE_WIDTH = 544, MAX_PULSE_WIDTH = 2400 y
    write(ang) hace map(ang, 0, 180, 544, 2400) con division entera.
    Con attach(pin) por defecto, min = max = 0 (resolucion de 4 us).
    """
    return (angulo - 0) * (2400 - 544) // (180 - 0) + 544


def separar(linea):
    """Replica exacta de la funcion separar() del sketch."""
    linea = linea.strip()
    args = []
    i = 0
    n = len(linea)
    while i < n and len(args) < 4:
        while i < n and linea[i] == " ":
            i += 1
        if i >= n:
            break
        ini = i
        while i < n and linea[i] != " ":
            i += 1
        args.append(linea[ini:i])
    return args


def constring(v, lo, hi):
    return max(lo, min(hi, v))


def despachar(linea):
    """Replica del despacho de procesar(). Devuelve (cmd, accion, valor)."""
    linea = linea.strip()
    if not linea:
        return None, None, None

    args = separar(linea)
    if not args:
        return None, None, None
    cmd = args[0]
    rest = args[1:]

    def num(s):
        try:
            return int(s)
        except ValueError:
            return 0

    if cmd in ("H", "?"):
        return cmd, "ayuda", None
    if cmd == "P" and len(rest) >= 1:
        return cmd, "pulso", constring(num(rest[0]), PULSO_MIN, PULSO_MAX)
    if cmd == "A" and len(rest) >= 1:
        ang = constring(num(rest[0]), 0, 180)
        return cmd, "angulo", 1000 + (ang * 1000) // 180
    if cmd == "G":
        return cmd, "golpe", None
    if cmd == "X":
        return cmd, "rampa", None
    if cmd == "T":
        return cmd, "sostener", None
    if cmd == "OFF":
        return cmd, "soltar", None
    if cmd == "ON":
        return cmd, "conectar", None
    if cmd == "TAP" and len(rest) >= 1:
        us = constring(num(rest[0]), PULSO_MIN, PULSO_MAX)
        dur = num(rest[1]) if len(rest) >= 2 else 60
        pausa = num(rest[2]) if len(rest) >= 3 else 440
        return cmd, "tap", (us, dur, pausa)
    if cmd == "SW" and len(rest) >= 2:
        ini = constring(num(rest[0]), PULSO_MIN, PULSO_MAX)
        fin = constring(num(rest[1]), PULSO_MIN, PULSO_MAX)
        if fin < ini:
            ini, fin = fin, ini
        return cmd, "sweep", (ini, fin)
    if cmd == "M":
        return cmd, "unificado", None
    if cmd == "N" and len(rest) >= 1:
        return cmd, "individual", constring(num(rest[0]), 1, 4)
    if cmd == "R":
        return cmd, "informe", None
    return cmd, "desconocido", None


fallos = []


def check(nombre, cond, detalle=""):
    if cond:
        print(f"  OK   {nombre}")
    else:
        print(f"  FAIL {nombre} {detalle}")
        fallos.append(nombre)


print("== Parser del sketch de calibracion ==")
casos = [
    ("H", "ayuda", None),
    ("P 1900", "pulso", 1900),
    ("P 3000", "pulso", 2100),      # clamp arriba
    ("P 100", "pulso", 900),        # clamp abajo
    ("A 90", "angulo", 1500),       # 90 deg -> 1500 us (centro)
    ("A 0", "angulo", 1000),
    ("A 180", "angulo", 2000),
    ("G", "golpe", None),
    ("X", "rampa", None),
    ("T", "sostener", None),
    ("OFF", "soltar", None),
    ("ON", "conectar", None),
    ("TAP 2100 60 440", "tap", (2100, 60, 440)),
    ("TAP 1900", "tap", (1900, 60, 440)),
    ("SW 1000 2000", "sweep", (1000, 2000)),
    ("SW 2000 1000", "sweep", (1000, 2000)),  # invertido -> ordena
    ("M", "unificado", None),
    ("N 3", "individual", 3),
    ("N 9", "individual", 4),        # clamp
    ("R", "informe", None),
]
for linea, accion_esp, valor_esp in casos:
    cmd, accion, valor = despachar(linea)
    ok = accion == accion_esp and valor == valor_esp
    check(f"'{linea}' -> {accion_esp} {valor_esp}", ok, f"-> {accion} {valor}")

print("== TAP no choca con el comando T ==")
cmd, accion, valor = despachar("TAP 2100 60 440")
check("TAP se interpreta como TAP, no como 'T' (parar)", accion == "tap", accion)
cmd, accion, valor = despachar("T")
check("T a secas sigue siendo 'sostener'", accion == "sostener", accion)

print("== Comandos mal formados no rompen nada ==")
# Una linea vacia o con espacios se IGNORA (no es un error: el firmware de
# produccion hace lo mismo con lineas en blanco).
for vacio in ["", "   "]:
    cmd, accion, valor = despachar(vacio)
    check(f"'{vacio}' se ignora sin error", accion is None, str(accion))

for malo in ["P", "A", "N", "TAP", "SW", "SW 1500", "XYZ", "P abc"]:
    cmd, accion, valor = despachar(malo)
    check(f"'{malo}' no rompe", accion is not None)

print("== Mapeo de angulo: el 170° problematico ==")
_, _, us_170 = despachar("A 170")
_, _, us_90 = despachar("A 90")
print(f"  A 170 -> {us_170} us  (la libreria Servo daria {servo_us(170)} us)")
print(f"  A 90  -> {us_90} us  (centro mecanico)")
check("A 170 queda dentro del rango de fabrica", us_170 <= 2000, str(us_170))
check("A 90 cae en el centro mecanico", us_90 == 1500, str(us_90))

print("== Mapeo real de Servo.write con los valores de produccion ==")
for ang, esperado in ((140, 1987), (110, 1678)):
    us = servo_us(ang)
    print(f"  Servo.write({ang}) -> {us} us (esperado {esperado})")
    check(f"Servo.write({ang}) da {esperado} us", us == esperado, str(us))
    check(f"Servo.write({ang}) queda en rango de fabrica (1000-2000)",
          1000 <= us <= 2000, str(us))

print()
if fallos:
    print(f"FALLARON {len(fallos)}: {fallos}")
    sys.exit(1)
print("TODAS LAS PRUEBAS PASARON")
