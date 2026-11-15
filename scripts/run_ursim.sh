#!/usr/bin/env bash
# Levanta URSim e-Series (UR5) en el Docker LOCAL y muestra cómo acceder.
# Uso:  ./run_ursim.sh        -> arranca/reutiliza el contenedor
#       ./run_ursim.sh stop   -> detiene el contenedor
#       ./run_ursim.sh logs   -> logs del contenedor
set -euo pipefail

CTX="default"          # SIEMPRE local (no el contexto hostinger-vps)
NAME="ursim"
IMAGE="universalrobots/ursim_e-series:latest"
PORT_VNC=5900
PORT_NOVNC=6080

dc() { docker --context "$CTX" "$@"; }

case "${1:-start}" in
  stop)
    dc stop "$NAME" >/dev/null 2>&1 && echo "URSim detenido." || echo "URSim no estaba corriendo."
    exit 0
    ;;
  logs)
    dc logs --tail 60 "$NAME"
    exit 0
    ;;
esac

if ! dc image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Descargando imagen $IMAGE ..."
  dc pull "$IMAGE"
fi

if dc container inspect "$NAME" >/dev/null 2>&1; then
  echo "Reutilizando contenedor existente '$NAME'..."
  dc start "$NAME" >/dev/null
else
  echo "Creando contenedor '$NAME'..."
  dc run -d --name "$NAME" \
    -e ROBOT_MODEL=UR5 \
    -p ${PORT_VNC}:5900 \
    -p ${PORT_NOVNC}:6080 \
    -p 29999:29999 \
    -p 30001-30004:30001-30004 \
    -p 50001-50003:50001-50003 \
    --shm-size=1g \
    "$IMAGE" >/dev/null
fi

echo "Esperando a que PolyScope levante los puertos..."
for i in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/29999) 2>/dev/null; then
    echo "  listo ($i s)"
    break
  fi
  sleep 2
done

echo
echo "Interfaz del robot (noVNC):  http://localhost:${PORT_NOVNC}/vnc.html?autoconnect=true"
echo "VNC directo:                 127.0.0.1:${PORT_VNC}"
echo "Dashboard Server:            127.0.0.1:29999"
echo "RTDE / interfaces:           127.0.0.1:30001-30004, 50001-50003"
echo
echo "En .env usar:  UR5_IP=127.0.0.1   ROBOT_MODE=real"
