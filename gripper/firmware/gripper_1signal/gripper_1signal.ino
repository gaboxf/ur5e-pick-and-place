// gripper con 4 servos MG996R movidos por UNA sola senal
// los 4 cables naranjas se puentean juntos a D3; los servos 2 y 4 van
// montados con el cuerno invertido 180 grados en el eje, para que el mismo
// pulso mueva los dos lados de forma simetrica
// comandos por serial: OPEN (abrir), CLOSE (cerrar), STATUS (consultar estado)
// protocolo: comandos ASCII en mayuscula, terminados en \n, baud 115200
// sin LEDs, sin E-STOP fisico, sin HC-SR04 y sin LCD: solo los 4 servos
// la seguridad de persona queda del lado del operador y del UR5

#include <Servo.h>

// pin de la senal unica
const int pinSenal = 3;

// angulos de boca abierta y de cierre (50 mm, calibrar con pieza real)
int abierto = 170;
int cerrado = 110;

// posicion actual. arranca abierto
int posicion = abierto;

// destino a donde tiene que ir
int destino = abierto;

bool enMovimiento = false;

// watchdog: ultima vez que la PC envio un comando valido. 15 s: margen para
// que el heartbeat de la PC (STATUS cada ~1.5 s) lo mantenga vivo durante los
// movimientos largos del UR5, pero sigue soltando la pieza si la PC muere.
unsigned long ultimaComunicacion = 0;
const unsigned long WATCHDOG_MS = 15000;

// cadencia del movimiento suave: 1 grado cada PASO_MS. Con 30 ms y una
// carrera de ~60 grados el ciclo completo dura ~2 s
const unsigned long PASO_MS = 30;

String textoSerial = "";

// un solo objeto Servo: el pulso sale por D3 y llega a los 4 servos
Servo servoGripper;

// fija el destino y arranca el movimiento suave
void empezar(int d) {
  destino = d;
  enMovimiento = true;
}

// aca se decide que hacer con cada comando
void procesar(String linea) {
  bool valido = true;

  if (linea == "OPEN") {
    if (enMovimiento) {
      Serial.println("BUSY");
    } else {
      empezar(abierto);
    }
  } else if (linea == "CLOSE") {
    if (enMovimiento) {
      Serial.println("BUSY");
    } else {
      empezar(cerrado);
    }
  } else if (linea == "STATUS") {
    // STATUS es solo consulta de movimiento; tambien mantiene vivo el
    // watchdog (PC viva = no abrir solo)
    Serial.println(enMovimiento ? "BUSY" : "READY");
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

  // seguridad: si pasan WATCHDOG_MS sin comandos validos de la PC, el
  // gripper vuelve a abrir solo. Asi si la PC se cae mientras sujeta una pieza, no
  // queda trabado en una posicion de cierre que podria aplastar la pieza.
  // Solo dispara cuando no esta ya completamente abierto, y resetea el
  // timer para que no lo haga cada iteracion.
  if ((millis() - ultimaComunicacion > WATCHDOG_MS) && (posicion != abierto)) {
    ultimaComunicacion = millis();
    empezar(abierto);
  }
}

void setup() {
  Serial.begin(115200);

  servoGripper.attach(pinSenal);

  // arranca abierto
  servoGripper.write(posicion);

  ultimaComunicacion = millis();
  Serial.println("READY");
}

void loop() {
  revisarSerial();

  // si esta en movimiento, avanzar de a 1 grado para que sea suave
  if (enMovimiento) {
    if (posicion < destino) { posicion++; }
    else if (posicion > destino) { posicion--; }
    servoGripper.write(posicion);

    // cuando llega aviso a la pc
    if (posicion == destino) {
      enMovimiento = false;
      Serial.println("DONE");
    }
  }

  delay(PASO_MS);
}
