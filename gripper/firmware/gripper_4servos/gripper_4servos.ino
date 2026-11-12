// gripper con 4 servos MG996R
// comandos por serial: OPEN (abrir), CLOSE (cerrar), STATUS (consultar estado)
// protocolo: comandos ASCII terminados en \n, baud 115200
// LEDs de estado: amarillo = moviendose, verde = abierto, rojo = cerrado
//
// SEGURIDAD (nuevo):
//   - Boton E-STOP en D8 (a GND, pullup interno). Al pulsar frena todo y
//     emite "EMERGENCY" por serial. Al soltar emite "READY".
//   - Sensor ultrasonico HC-SR04 (TRIG D10, ECHO D11). Si detecta un objeto
//     a menos de UMBRAL_CM emite "PROXIMITY_STOP" una sola vez (anti-spam)
//     y vuelve a armarse cuando el objeto se aleja.

#include <Servo.h>

// pines de señal, uno por servo
int pinServo1 = 3;
int pinServo2 = 3;
int pinServo3 = 3;
int pinServo4 = 3;

// LEDs de estado (cada uno con resistencia de 220-330 ohm a GND)
const int LED_AMARILLO = 2;  // moviendose
const int LED_VERDE = 4;     // abierto
const int LED_ROJO = 7;      // cerrado

// boton de emergencia fisico (a GND, pullup interno => LOW = pulsado)
const int BUTTON_ESTOP = 8;

// sensor ultrasonico HC-SR04
const int TRIG_PIN = 10;
const int ECHO_PIN = 11;
const int UMBRAL_CM = 15;          // distancia de seguridad
const unsigned long SENSOR_INTERVALO_MS = 100;  // cada cuanto medir
const unsigned long SENSOR_GRACIA_MS = 600;     // anti-rebote entre avisos

// angulos a los que abren los brazos (boca abierta)
int abierto1 = 170;
int abierto2 = 10;
int abierto3 = 170;
int abierto4 = 10;

// angulos que cierran justo el ancho de la pieza (50 mm, calibrar con pieza real)
int cerrado1 = 110;
int cerrado2 = 70;
int cerrado3 = 110;
int cerrado4 = 70;

// posiciones actuales. arranca abierto
int posicion1 = 170;
int posicion2 = 10;
int posicion3 = 170;
int posicion4 = 10;

// destinos a donde tienen que ir
int destino1 = 170;
int destino2 = 10;
int destino3 = 170;
int destino4 = 10;

bool enMovimiento = false;
String ultimoComando = "OPEN"; // Arranca abierto por defecto

// watchdog: ultima vez que la PC envio un comando valido
unsigned long ultimaComunicacion = 0;
const unsigned long WATCHDOG_MS = 5000;

String textoSerial = "";

// estado del anti-spam de proximidad
bool proximidadArmada = true;   // true = puede volver a avisar
unsigned long ultimoAvisoProx = 0;

Servo servo1;
Servo servo2;
Servo servo3;
Servo servo4;

// Refresca los 3 LEDs segun el estado actual del gripper.
void actualizarLeds() {
  digitalWrite(LED_AMARILLO, enMovimiento ? HIGH : LOW);

  if (enMovimiento) {
    digitalWrite(LED_VERDE, LOW);
    digitalWrite(LED_ROJO, LOW);
  } else {
    digitalWrite(LED_VERDE, (ultimoComando == "OPEN") ? HIGH : LOW);
    digitalWrite(LED_ROJO, (ultimoComando == "CLOSE") ? HIGH : LOW);
  }
}

// fija los destinos y arranca el movimiento suave (1 grado cada ~15 ms)
void empezar(int d1, int d2, int d3, int d4, String cmd) {
  destino1 = d1;
  destino2 = d2;
  destino3 = d3;
  destino4 = d4;
  ultimoComando = cmd;
  enMovimiento = true;
  actualizarLeds();
}

// lee el boton de emergencia (LOW = pulsado con pullup interno)
bool botonPulsado() {
  return digitalRead(BUTTON_ESTOP) == LOW;
}

// mide distancia en cm con el sensor ultrasonico. Devuelve -1 si no hay eco.
long medirDistanciaCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duracion = pulseIn(ECHO_PIN, HIGH, 30000); // timeout ~30 ms
  if (duracion == 0) return -1;
  long distancia = duracion / 58;                 // conversion a cm
  if (distancia < 0) return -1;
  if (distancia > 400) return 400;
  return distancia;
}

// aca se deciden que hacer con cada comando
void procesar(String linea) {
  ultimaComunicacion = millis();

  if (linea == "OPEN") {
    if (enMovimiento) {
      Serial.println("BUSY");
    } else {
      empezar(abierto1, abierto2, abierto3, abierto4, "OPEN");
    }
  } else if (linea == "CLOSE") {
    if (enMovimiento) {
      Serial.println("BUSY");
    } else {
      empezar(cerrado1, cerrado2, cerrado3, cerrado4, "CLOSE");
    }
  } else if (linea == "STATUS") {
    if (enMovimiento) {
      Serial.println("BUSY");
    } else {
      Serial.println("READY");
    }
  } else {
    Serial.println("ERROR");
  }
}

void revisarSerial() {
  while (Serial.available() > 0) {
    char letra = Serial.read();
    if (letra == '\n') {
      textoSerial.trim();
      if (textoSerial.length() > 0) {
        procesar(textoSerial);
      }
      textoSerial = "";
    } else {
      textoSerial += letra;
    }
  }

  // Watchdog: si pasan WATCHDOG_MS sin comando y no esta abierto, abre solo.
  if ((millis() - ultimaComunicacion > WATCHDOG_MS) &&
      (posicion1 != abierto1 || posicion2 != abierto2 ||
       posicion3 != abierto3 || posicion4 != abierto4)) {
    ultimaComunicacion = millis();
    empezar(abierto1, abierto2, abierto3, abierto4, "OPEN");
  }
}

// Verifica el boton de emergencia. Si esta pulsado, frena y avisa una vez.
void revisarEstop() {
  static bool avisado = false;

  if (botonPulsado()) {
    if (!avisado) {
      enMovimiento = false;      // frena los servos de inmediato
      actualizarLeds();
      Serial.println("EMERGENCY");
      avisado = true;
    }
  } else if (avisado) {
    avisado = false;
    Serial.println("READY");     // boton liberado, listo para operar
  }
}

// Verifica proximidad. Avisa una vez al entrar en zona de peligro y se rearma
// cuando el objeto se aleja mas alla del umbral (+ margen).
void revisarProximidad() {
  static unsigned long ultimaMedicion = 0;
  if (millis() - ultimaMedicion < SENSOR_INTERVALO_MS) {
    return;
  }
  ultimaMedicion = millis();

  long dist = medirDistanciaCm();
  if (dist < 0) {
    return; // sin eco valido, ignorar
  }

  if (dist < UMBRAL_CM) {
    if (proximidadArmada) {
      Serial.println("PROXIMITY_STOP");
      proximidadArmada = false;
      ultimoAvisoProx = millis();
    }
  } else if (dist > UMBRAL_CM + 5) {
    // objeto alejado con margen: rearmar
    proximidadArmada = true;
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_AMARILLO, OUTPUT);
  pinMode(LED_VERDE, OUTPUT);
  pinMode(LED_ROJO, OUTPUT);

  pinMode(BUTTON_ESTOP, INPUT_PULLUP);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);

  servo1.attach(pinServo1);
  servo2.attach(pinServo2);
  servo3.attach(pinServo3);
  servo4.attach(pinServo4);

  servo1.write(posicion1);
  servo2.write(posicion2);
  servo3.write(posicion3);
  servo4.write(posicion4);
  actualizarLeds();

  ultimaComunicacion = millis();
  Serial.println("READY");
}

void loop() {
  revisarSerial();
  revisarEstop();
  revisarProximidad();

  // si esta en movimiento, avanzar de a 1 grado por servo para que sea suave
  if (enMovimiento) {
    bool llegaron = true;

    if (posicion1 < destino1) { posicion1++; llegaron = false; }
    else if (posicion1 > destino1) { posicion1--; llegaron = false; }
    servo1.write(posicion1);

    if (posicion2 < destino2) { posicion2++; llegaron = false; }
    else if (posicion2 > destino2) { posicion2--; llegaron = false; }
    servo2.write(posicion2);

    if (posicion3 < destino3) { posicion3++; llegaron = false; }
    else if (posicion3 > destino3) { posicion3--; llegaron = false; }
    servo3.write(posicion3);

    if (posicion4 < destino4) { posicion4++; llegaron = false; }
    else if (posicion4 > destino4) { posicion4--; llegaron = false; }
    servo4.write(posicion4);

    // cuando los cuatro llegan aviso a la pc y actualizo los LEDs
    if (llegaron) {
      enMovimiento = false;
      actualizarLeds();
      Serial.println("DONE");
    }
  }

  delay(15);
}