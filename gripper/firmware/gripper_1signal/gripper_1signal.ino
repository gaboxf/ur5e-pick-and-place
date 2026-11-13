// gripper con 4 servos MG996R movidos por UNA sola senal
// los 4 cables naranjas se puentean juntos a D3; los servos 2 y 4 van
// montados con el cuerno invertido 180 grados en el eje, para que el mismo
// pulso mueva los dos lados de forma simetrica
// comandos por serial: OPEN (abrir), CLOSE (cerrar), STOP (parada suave:
// redirige a abierto desde cualquier estado), STATUS (consultar estado) y
// PING (latido: responde PONG)
// protocolo: comandos ASCII en mayuscula, terminados en \n, baud 115200
// OPEN siempre gana: si llega en pleno movimiento redirige el destino a
// abierto y responde BUSY (el DONE llega al terminar la carrera). CLOSE no
// se acepta a mitad de movimiento: responde BUSY sin cambiar el destino
// el watchdog, al dispararse, emite "WATCHDOG_OPEN" antes de abrirse solo y
// "WATCHDOG_DONE" al terminar: queda como evidencia en el log serie
// (GripperSerial) del lado Python
// PING existe para que el heartbeat de la PC no se confunda con la respuesta
// a un OPEN/CLOSE en vuelo: PONG es un token propio, no "READY"/"BUSY"
// sin LEDs, sin E-STOP fisico, sin HC-SR04 y sin LCD: solo los 4 servos
// la seguridad de persona queda del lado del operador y del UR5

#include <Servo.h>

// pin de la senal unica
const int pinSenal = 3;

// angulos de boca abierta y de cierre (50 mm, calibrar con pieza real)
// con la libreria Servo 1.3.0: write(140) -> 1987 us, write(110) -> 1678 us
// (rango de fabrica del MG996R: 1000-2000 us. No subir de 140: con 170 el
// pulso sale a 2296 us y los servos empujan contra el tope mecanico)
int abierto = 140;
int cerrado = 110;

// posicion actual. arranca abierto
int posicion = abierto;

// destino a donde tiene que ir
int destino = abierto;

bool enMovimiento = false;

// true mientras el movimiento en curso lo empezo el watchdog (no la PC):
// al terminar responde WATCHDOG_DONE en vez de DONE, para que el log
// Python distinga "abrio la PC" de "abrio solo porque la PC murio"
bool movimientoPorWatchdog = false;

// watchdog: ultima vez que la PC envio un comando valido. 15 s: margen para
// que el heartbeat de la PC (STATUS cada ~1.5 s, hilo GripperHeartbeat del
// GripperClient) lo mantenga vivo durante los movimientos largos del UR5,
// pero sigue soltando la pieza si la PC muere.
unsigned long ultimaComunicacion = 0;
const unsigned long WATCHDOG_MS = 15000;

// cadencia del movimiento suave: 1 grado cada PASO_MS. Con 30 ms y una
// carrera de ~60 grados el ciclo completo dura ~2 s
const unsigned long PASO_MS = 30;

// momento del ultimo paso de movimiento (loop no bloqueante: sin delay, el
// serial y el watchdog se chequean en cada vuelta)
unsigned long ultimoPaso = 0;

String textoSerial = "";

// un solo objeto Servo: el pulso sale por D3 y llega a los 4 servos
Servo servoGripper;

// fija el destino y arranca el movimiento suave. El clamp evita que una
// calibracion futura (o un valor fuera de rango) deje al servo pegado contra
// el tope mecanico mientras el firmware cree que todavia no llego
void empezar(int d, bool porWatchdog = false) {
  destino = constrain(d, min(abierto, cerrado), max(abierto, cerrado));
  enMovimiento = true;
  movimientoPorWatchdog = porWatchdog;
  ultimoPaso = millis();
}

// aca se decide que hacer con cada comando
void procesar(String linea) {
  bool valido = true;

  if (linea == "OPEN") {
    if (enMovimiento) {
      // redirige en pleno movimiento (por ej. sale de un cierre a mitad).
      // La PC ve BUSY, espera READY y reintentara el OPEN
      empezar(abierto);
      Serial.println("BUSY");
    } else {
      // sin respuesta inmediata: el DONE llega al completar la carrera
      // (instantaneo si ya estaba abierto)
      empezar(abierto);
    }
  } else if (linea == "CLOSE") {
    if (enMovimiento) {
      // un cierre a mitad de movimiento no se acepta: primero tiene que
      // quedar quieto (o llegar a abierto). La PC reintenta tras READY
      Serial.println("BUSY");
    } else {
      empezar(cerrado);
    }
  } else if (linea == "STOP") {
    // parada suave: desde cualquier estado redirige a abierto
    if (enMovimiento) {
      empezar(abierto);
      Serial.println("BUSY");
    } else if (posicion != abierto) {
      // arranca la apertura; DONE al terminar la carrera
      empezar(abierto);
    } else {
      Serial.println("DONE");
    }
  } else if (linea == "STATUS") {
    // STATUS es solo consulta de movimiento; tambien mantiene vivo el
    // watchdog (PC viva = no abrir solo)
    Serial.println(enMovimiento ? "BUSY" : "READY");
  } else if (linea == "PING") {
    // latido del heartbeat de la PC. Token propio ("PONG") para que no se
    // confunda con la respuesta a un OPEN/CLOSE en vuelo: el cliente Python
    // lo enruta a una cola aparte
    Serial.println("PONG");
  } else {
    Serial.println("ERROR");
    valido = false;
  }

  // solo un comando reconocido mantiene vivo el watchdog: la basura serial
  // (o un puerto abierto a otro baud) no puede hacer pasar una PC muerta
  // por viva con el gripper cerrado sosteniendo una pieza
  if (valido) {
    ultimaComunicacion = millis();
  }
}

// lee el serial y procesa lineas terminadas en \n (tolera \r)
void revisarSerial() {
  while (Serial.available() > 0) {
    char letra = Serial.read();
    if (letra == '\n' || letra == '\r') {
      textoSerial.trim();
      if (textoSerial.length() > 0) {
        procesar(textoSerial);
      }
      textoSerial = "";
    } else if (textoSerial.length() < 16) {
      // tope de 16 chars (los comandos validos miden <= 6): si llega basura
      // sin \n (puerto abierto a otro baud, cable flojo) no deja crecer el
      // heap de 2 KB hasta colgar el UNO
      textoSerial += letra;
    }
  }

  // seguridad: si pasan WATCHDOG_MS sin comandos validos de la PC y el
  // destino no es ya la apertura, el gripper vuelve a abrir solo. Asi si la
  // PC se cae mientras sujeta una pieza, no queda trabado en una posicion de
  // cierre que podria aplastar la pieza.
  // El aviso "WATCHDOG_OPEN" sale ANTES de arrancar: es la evidencia en el
  // log de que el firmware abrio solo. Se compara contra destino (no
  // posicion): si ya esta abriendo por watchdog no lo repite en cada paso, y
  // al completar la carrera el destino queda en abierto y la condicion deja
  // de cumplirse sola.
  if ((millis() - ultimaComunicacion > WATCHDOG_MS) && (destino != abierto)) {
    Serial.println("WATCHDOG_OPEN");
    empezar(abierto, true);
  }
}

void setup() {
  Serial.begin(115200);

  servoGripper.attach(pinSenal);

  // arranca abierto
  servoGripper.write(posicion);

  ultimaComunicacion = millis();
  ultimoPaso = millis();
  Serial.println("READY");
}

void loop() {
  revisarSerial();

  // si esta en movimiento, avanzar de a 1 grado cada PASO_MS para que sea
  // suave. Sin delay(): el loop gira libre y el serial y el watchdog se
  // chequean seguido (el buffer de 64 B de la UART ya no se desborda ante
  // comandos que llegan mientras un paso espera su turno)
  if (enMovimiento && (millis() - ultimoPaso >= PASO_MS)) {
    ultimoPaso = millis();

    if (posicion < destino) { posicion++; }
    else if (posicion > destino) { posicion--; }
    servoGripper.write(posicion);

    // cuando llega aviso a la pc. Si el movimiento lo empezo el watchdog
    // (la PC estaba muerta) el token es WATCHDOG_DONE, no DONE: asi el log
    // Python deja claro que el gripper se abrio solo
    if (posicion == destino) {
      enMovimiento = false;
      Serial.println(movimientoPorWatchdog ? "WATCHDOG_DONE" : "DONE");
      movimientoPorWatchdog = false;
    }
  }
}
