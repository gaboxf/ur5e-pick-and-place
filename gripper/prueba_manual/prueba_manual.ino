// PRUEBA MANUAL del gripper: los 4 servos MG996R por UNA sola senal (D3).
// Version minima de gripper_1signal: solo dos estados, ABRIR y CERRAR.
// ARRANCA CERRADO (ver ARRANCAR_CERRADO mas abajo).
//
// - Los 4 cables naranjas van puenteados juntos a D3 (mismo pulso para los 4).
// - Los servos 2 y 4 van con el cuerno invertido 180 grados, como siempre.
// - Fuente externa 5-6 V para los servos + GND comun con el UNO (ver README).
//
// Comandos por serial (115200, terminados en \n):
//   OPEN   -> abrir     (tambien con la letra O sola)
//   CLOSE  -> cerrar    (tambien con la letra C sola)
// Se aceptan en minuscula tambien (se pasan a mayuscula).
//
// LOGS: solo cambios de estado, con texto descriptivo, y el proceso del
// movimiento (posicion intermedia) igual que antes. Al arrancar, una sola
// linea ("ARRANQUE: CERRADO (750 us)"); en cada movimiento, "ABRIENDO ..." o
// "CERRANDO ...", el avance cada 10 pasos y al final "ESTADO: ABIERTO ...".
// Si el comando no cambia el estado (OPEN estando abierto) no se imprime
// nada. Los comandos invalidos responden "ERROR ...".
//
// No hay watchdog, ni LEDs, ni E-STOP: es solo para verificar a mano que los
// cuatro servos se muevan juntos. Para la version de produccion usar
// firmware/gripper_1signal.
//
// SEGURIDAD: el recorrido es CORTO a proposito (150 us de pulso, ~15 grados).
// OJO: al abrir el monitor serie el UNO se resetea (DTR) y vuelve a CERRADO.

#include <Servo.h>

// pin de la senal unica (los 4 naranjas puenteados)
const int PIN_SENAL = 3;

// pulsos en microsegundos. Rango nominal del MG996R: 1000-2000 us.
// 900 y 750 estan fuera de ese rango nominal, pero son los valores probados
// en este montaje sin carga y con carga liviana: no forzan el tope mecanico.
// Si algun servo empieza a zumbar, volver hacia 1000 us (cerrado) / 1000+ us.
const int US_ABIERTO = 900;
const int US_CERRADO = 750;

// rampa suave: 2 us cada 15 ms => la carrera completa tarda ~1 s. Con 4
// servos, arrancar y frenar despacio evita el pico de corriente que hace
// caer la fuente.
const int PASO_US = 2;
const unsigned long PASO_MS = 15;

// asentamiento: sostiene el pulso final un momento antes de dar por terminado
// el movimiento (los 4 servos terminan de llegar)
const unsigned long ASENTAMIENTO_MS = 300;

// un solo objeto Servo: el pulso sale por D3 y llega a los 4 servos
Servo servoGripper;

// estado inicial. true = arranca cerrado, false = arranca abierto.
// OJO: el UNO se resetea cada vez que se abre el puerto serie (DTR), asi que
// con true el gripper se cierra solo al abrir el monitor. Con la boca vacia.
const bool ARRANCAR_CERRADO = true;

// posicion actual en microsegundos
int posicion = ARRANCAR_CERRADO ? US_CERRADO : US_ABIERTO;

String textoSerial = "";

// mueve despacio hasta el pulso pedido y se queda ahi. Bloqueante a
// proposito: en una prueba manual no importa no leer el serial mientras dura.
// verbo: "ABRIENDO" / "CERRANDO"; estadoFinal: "ABIERTO" / "CERRADO".
void moverA(int objetivo, const char* verbo, const char* estadoFinal) {
  objetivo = constrain(objetivo, min(US_ABIERTO, US_CERRADO),
                                 max(US_ABIERTO, US_CERRADO));

  Serial.print(verbo);
  Serial.print(": ");
  Serial.print(posicion);
  Serial.print(" -> ");
  Serial.print(objetivo);
  Serial.println(" us");

  int paso = 0;
  while (posicion != objetivo) {
    if (posicion < objetivo) {
      posicion = min(posicion + PASO_US, objetivo);
    } else {
      posicion = max(posicion - PASO_US, objetivo);
    }
    servoGripper.writeMicroseconds(posicion);
    delay(PASO_MS);

    // avance cada 10 pasos, para ver que la rampa progresa
    if (++paso % 10 == 0) {
      Serial.print("  pos=");
      Serial.print(posicion);
      Serial.println(" us");
    }
  }

  delay(ASENTAMIENTO_MS);

  Serial.print("ESTADO: ");
  Serial.print(estadoFinal);
  Serial.print(" (");
  Serial.print(posicion);
  Serial.println(" us)");
}

// aca se decide que hacer con cada comando. Solo se imprime si el estado
// realmente cambia (el log final sale al terminar el movimiento)
void procesar(String linea) {
  linea.trim();
  linea.toUpperCase();

  if (linea.length() == 0) {
    return;
  }

  // atajos de una letra: O = OPEN, C = CLOSE
  if (linea == "O") {
    linea = "OPEN";
  } else if (linea == "C") {
    linea = "CLOSE";
  }

  if (linea == "OPEN") {
    if (posicion != US_ABIERTO) {
      moverA(US_ABIERTO, "ABRIENDO", "ABIERTO");
    }
  } else if (linea == "CLOSE") {
    if (posicion != US_CERRADO) {
      moverA(US_CERRADO, "CERRANDO", "CERRADO");
    }
  } else {
    Serial.println("ERROR (usar OPEN o CLOSE)");
  }
}

// lee el serial y procesa lineas terminadas en \n (tolera \r)
void revisarSerial() {
  while (Serial.available() > 0) {
    char letra = Serial.read();
    if (letra == '\n' || letra == '\r') {
      if (textoSerial.length() > 0) {
        procesar(textoSerial);
      }
      textoSerial = "";
    } else if (textoSerial.length() < 16) {
      textoSerial += letra;
    }
  }
}

void setup() {
  Serial.begin(115200);

  servoGripper.attach(PIN_SENAL);

  // primer pulso: manda los 4 servos al estado inicial elegido
  // (con ARRANCAR_CERRADO = true, cerrar con la boca vacia)
  servoGripper.writeMicroseconds(posicion);

  // unica linea de arranque. Si vuelve a aparecer sola despues de un
  // movimiento, la placa se reinicio (tipico: caida de tension al arrancar
  // los 4 servos a la vez)
  Serial.print("ARRANQUE: ");
  Serial.print(ARRANCAR_CERRADO ? "CERRADO" : "ABIERTO");
  Serial.print(" (");
  Serial.print(posicion);
  Serial.println(" us)");
}

void loop() {
  revisarSerial();
}
