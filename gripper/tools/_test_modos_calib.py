"""Simula la maquina de estados del sketch de calibracion (loop + procesar).

Replica faithfully el codigo del .ino para verificar QUE HACE REALMENTE cada
modo: que pulsos se envian al servo y cuando.
"""

import sys

PULSO_MIN, PULSO_MAX = 900, 2100
PASO_RAMP_MS = 20
PASO_RAMP_US = 10
INTERVALO_SWEEP_MS = 1500
PASO_SWEEP_US = 25
TAP_DURACION_MS = 60
TAP_PAUSA_MS = 440


class Sketch:
    def __init__(self):
        # --- estado inicial (setup) ---
        self.modo = "HOLD"
        self.pin = 3
        self.adjunto = True
        self.pulsoObjetivo = 1500
        self.pulsoActual = 1500
        self.ultimoPaso = 0
        self.ultimoSweep = 0
        self.tapSiguiente = 0
        self.tapDuracion = TAP_DURACION_MS
        self.tapPausa = TAP_PAUSA_MS
        self.sweepObjetivo = 1500
        self.sweepIni = 1500
        self.sweepFin = 2000
        self.pasoSweep = 1
        self.t = 0
        self.aplicados = []      # (t_ms, pulso enviado al servo)
        self.anexos = []         # eventos de attach/detach
        self.log = []
        # setup(): el servo arranca conectado a 1500 us
        self.aplicarPulso(self.pulsoActual)

    # ---- helpers (identicos al .ino) ----
    def aplicarPulso(self, us):
        us = max(PULSO_MIN, min(PULSO_MAX, us))
        self.aplicados.append((self.t, us))

    def conectar(self):
        if self.adjunto:
            return
        self.adjunto = True
        self.anexos.append((self.t, "attach", self.pin))
        self.aplicarPulso(self.pulsoActual)

    def desconectar(self):
        if not self.adjunto:
            return
        self.adjunto = False
        self.anexos.append((self.t, "detach", self.pin))

    # ---- procesar() (comandos relevantes) ----
    def cmd(self, texto):
        partes = texto.split()
        if not partes:
            return
        cmd = partes[0]
        args = [int(p) if p.lstrip("-").isdigit() else 0 for p in partes[1:]]
        n = len(args) + 1
        if cmd == "P" and n >= 2:
            self.pulsoObjetivo = max(PULSO_MIN, min(PULSO_MAX, args[0]))
            self.modo = "RAMP"
            if not self.adjunto:
                self.conectar()
        elif cmd == "G":
            self.modo = "HOLD"
            self.pulsoActual = self.pulsoObjetivo
            if not self.adjunto:
                self.conectar()
            self.aplicarPulso(self.pulsoActual)
        elif cmd == "T":
            self.modo = "HOLD"
            self.pulsoObjetivo = self.pulsoActual
        elif cmd == "OFF":
            self.modo = "HOLD"
            self.desconectar()
        elif cmd == "ON":
            self.modo = "HOLD"
            self.conectar()
        elif cmd == "TAP" and n >= 2:
            self.pulsoObjetivo = max(PULSO_MIN, min(PULSO_MAX, args[0]))
            if n >= 3:
                self.tapDuracion = args[1]
            if n >= 4:
                self.tapPausa = args[2]
            self.modo = "TAP"
            self.tapSiguiente = 0
        elif cmd == "SW" and n >= 3:
            ini = max(PULSO_MIN, min(PULSO_MAX, args[0]))
            fin = max(PULSO_MIN, min(PULSO_MAX, args[1]))
            if fin < ini:
                ini, fin = fin, ini
            self.pulsoObjetivo = ini
            self.sweepObjetivo = ini
            self.sweepIni = ini
            self.sweepFin = fin
            self.pasoSweep = PASO_SWEEP_US if fin > ini else -PASO_SWEEP_US
            self.modo = "SWEEP"
            if not self.adjunto:
                self.conectar()
        else:
            pass

    # ---- barrido automatico (igual al .ino) ----
    def bloque_sweep(self, ahora):
        if self.modo == "SWEEP" and ahora - self.ultimoSweep >= INTERVALO_SWEEP_MS:
            self.ultimoSweep = ahora
            self.sweepObjetivo += self.pasoSweep
            if self.sweepObjetivo >= self.sweepFin:
                self.sweepObjetivo = self.sweepFin
                self.pasoSweep = -PASO_SWEEP_US
            if self.sweepObjetivo <= self.sweepIni:
                self.sweepObjetivo = self.sweepIni
                self.pasoSweep = PASO_SWEEP_US
            self.pulsoObjetivo = self.sweepObjetivo
            self.pulsoActual = self.pulsoObjetivo
            self.aplicarPulso(self.pulsoActual)
            self.log.append((self.t, f"sweep -> {self.pulsoActual}"))

    # ---- loop() ----
    def loop(self):
        ahora = self.t

        self.bloque_sweep(ahora)

        # modo TAP
        if self.modo == "TAP":
            if ahora >= self.tapSiguiente:
                if not self.adjunto:
                    self.adjunto = True
                    self.anexos.append((self.t, "attach", self.pin))
                    self.aplicarPulso(self.pulsoObjetivo)
                    self.tapSiguiente = ahora + self.tapDuracion
                else:
                    self.adjunto = False
                    self.anexos.append((self.t, "detach", self.pin))
                    self.tapSiguiente = ahora + self.tapPausa
            return

        # rampa
        if self.modo == "RAMP" and ahora - self.ultimoPaso >= PASO_RAMP_MS:
            self.ultimoPaso = ahora
            if self.pulsoActual != self.pulsoObjetivo:
                if self.pulsoActual < self.pulsoObjetivo:
                    self.pulsoActual = min(self.pulsoActual + PASO_RAMP_US, self.pulsoObjetivo)
                else:
                    self.pulsoActual = max(self.pulsoActual - PASO_RAMP_US, self.pulsoObjetivo)
                self.aplicarPulso(self.pulsoActual)
            else:
                self.modo = "HOLD"

    def run(self, ms, comando=None, t_cmd=0):
        """Corre 'ms' milisegundos, opcionalmente mandando un comando en t_cmd."""
        for i in range(ms):
            self.t = i
            if comando and i == t_cmd:
                self.cmd(comando)
            self.loop()


fallos = []


def check(nombre, cond, detalle=""):
    if cond:
        print(f"  OK   {nombre}")
    else:
        print(f"  FAIL {nombre} {detalle}")
        fallos.append(nombre)


print("== MODO RAMP: P 1900 desde 1500 ==")
s = Sketch()
s.run(1500, comando="P 1900", t_cmd=0)
enviados = [p for _, p in s.aplicados if p != 1500]
print(f"  pulsos distintos enviados: {len(set(enviados))}, ultimo: {enviados[-1] if enviados else None}")
check("el servo LLEGA al objetivo 1900", 1900 in set(enviados))
check("la rampa es gradual (muchos pasos, no un salto)",
      len(set(enviados)) > 30, f"pasos={len(set(enviados))}")
check("no hay saltos > 10 us entre pasos consecutivos",
      all(abs(b - a) <= 10 for a, b in zip(enviados, enviados[1:])))

print("== MODO SWEEP: SW 1200 2000 ==")
s2 = Sketch()
s2.run(7000, comando="SW 1200 2000", t_cmd=0)
valores = [p for _, p in s2.aplicados]
distintos = sorted(set(valores))
print(f"  announcements de sweep: {len(s2.log)}")
for t, m in s2.log[:6]:
    print(f"    t={t} ms  {m}")
print(f"  pulsos REALES enviados al servo: {distintos}")
check("el sweep ANUNCIA el barrido", len(s2.log) >= 3, f"n={len(s2.log)}")
check("el servo REALMENTE se mueve durante el sweep",
      len([p for p in valores if p != 1500]) > 3,
      f"pulsos distintos de 1500: {len([p for p in valores if p != 1500])}")
check("el sweep se mantiene DENTRO del rango pedido (1200-2000)",
      all(1200 <= p <= 2000 for p in valores if p != 1500),
      f"fuera: {[p for p in valores if p != 1500 and not (1200 <= p <= 2000)]}")

print("== MODO TAP: TAP 2100 60 440 ==")
s3 = Sketch()
s3.run(2000, comando="TAP 2100 60 440", t_cmd=0)
attach = [e for e in s3.anexos if e[1] == "attach"]
detach = [e for e in s3.anexos if e[1] == "detach"]
print(f"  attaches: {len(attach)}, detaches: {len(detach)}")
if attach:
    print(f"    ciclo: attach t={attach[0][0]}, detach t={detach[0][0]} -> duro {detach[0][0]-attach[0][0]} ms")
check("TAP alterna attach/detach", len(attach) >= 2 and len(detach) >= 2)
check("el servo queda DESCONECTADO entre pulsos (no se estanca)",
      len(detach) >= 2)

print("== OFF / ON ==")
s4 = Sketch()
s4.run(100, comando="OFF", t_cmd=0)
check("OFF desconecta el servo", any(e[1] == "detach" for e in s4.anexos))
s4.run(100, comando="ON", t_cmd=0)
check("ON reconecta el servo", any(e[1] == "attach" for e in s4.anexos for e in [e]))

print("== G: golpe seco ==")
s5 = Sketch()
s5.run(50, comando="P 2000", t_cmd=0)
antes = len(s5.aplicados)
s5.run(50, comando="G", t_cmd=0)
check("G aplica el objetivo de una", s5.aplicados[-1][1] == 2000, str(s5.aplicados[-1]))
check("G no pasa por los pasos intermedios", len(s5.aplicados) - antes == 1)

print()
if fallos:
    print(f"FALLARON {len(fallos)}: {fallos}")
    sys.exit(1)
print("TODAS LAS PRUEBAS PASARON")
