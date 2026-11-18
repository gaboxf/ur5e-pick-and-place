// gripper con 4 servos MG996R movidos por UNA sola senal
// los 4 cables naranjas se puentean juntos a D3; los servos 2 y 4 van
// montados con el cuerno invertido 180 grados en el eje, para que el mismo
// pulso mueva los dos lados de forma simetrica
// comandos por serial: OPEN (abrir), CLOSE (cerrar), ZERO (puesta a cero
// mecanica: cierra despacio hasta que las mordazas se tocan y se queda ahi),
// STOP (parada suave: redirige a abierto desde cualquier estado), STATUS
// (consultar estado) y PING (latido: responde PONG)
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
//
// SINCRONIA DE LOS 4 SERVOS
// Sale UN solo pulso por D3: los cuatro servos reciben el mismo ancho de
// pulso en el mismo frame, asi que el firmware no puede moverlos a distinta
// velocidad ni desincronizarlos. Si van desparejos, la causa es mecanica o
// electrica (cuernos desalineados, caida de tension, senal repartida a 4
// entradas). El codigo solo puede ayudar en dos puntos:
//   1) pulsos que no esten contra el tope de fabrica. El valor anterior
//      (write(140), 1987 us con Servo 1.3.0) estaba a 13 us de los 2000 us
//      del MG996R: con la tolerancia de potenciometro de cada unidad, algunos
//      servos saturaban antes que otros y la boca abierta quedaba despareja.
//      Ahora se trabaja en microsegundos con 100 us de margen.
//   2) rampa + asentamiento. La rampa mantiene a los cuatro con el mismo
//      pulso mientras se mueven; el asentamiento sostiene el pulso final
//      antes de avisar DONE, para que la PC (y el UR5) no arranque con una
//      mordaza todavia en camino.
// ZERO es la referencia comun: con las mordazas tocandose, la tolerancia de
// cada servo queda absorbida por el tope mecanico y ahi se alinean los
// cuernos. Solo con la boca vacia.

#include <Servo.h>

// pin de la senal unica
const int pinSenal = 3;

// --------------------------------------------------------------- calibracion
// pulsos en microsegundos, rango de fabrica del MG996R: 1000-2000 us.
// Se usa writeMicroseconds() y no write(grados) porque el mapeo de grados
// cambia con la version de la libreria Servo (1.3.0: 544..2400 us;
// 1.1.8: angulo*10+544). Con microsegundos el pulso es el mismo en cualquier
// maquina que compile este sketch.
//
// US_ABIERTO: el valor anterior era write(140) = 1987 us (Servo 1.3.0), a
// 13 us del tope de fabrica. 1900 us deja 100 us (10%) de margen, que es la
// tolerancia tipica de potenciometro del MG996R: asi ningun servo arranca
// saturado y la boca abre pareja. Si con 1900 no alcanza la apertura, subir
// de a 10 us y frenar apenas alguno de los cuatro empiece a zumbar.
// US_CERRADO: 1680 us (el valor anterior, write(110) = 1678 us). Calibrar
// con la pieza real de 50 mm (README 4.2, Fase 3).
// pulsos en microsegundos. Rango nominal del MG996R: 1000-2000 us.
// 900 y 750 estan fuera de ese rango nominal, pero son los valores probados
// en este montaje sin carga y con carga liviana: no forzan el tope mecanico.
// Si algun servo empieza a zumbar, volver hacia 1000 us (cerrado) / 1000+ us.
const int US_ABIERTO = 900;
const int US_CERRADO = 750;

// ------------------------------------------------------------ estado actual
// posicion actual y destino, en microsegundos
int posicion = US_ABIERTO;
int destino = US_ABIERTO;

// rampa: 10 us cada PASO_MS. 10 us equivalen al grado que usaba la version
// anterior (~10.3 us con Servo 1.3.0), asi que la velocidad es la misma:
// la carrera completa (220 us) tarda ~0.7 s
const int PASO_US = 10;
const unsigned long PASO_MS = 30;

// asentamiento: el firmware no tiene realimentacion de posicion. Cuando la
// rampa llega al destino, el servo con mas carga todavia no llego. Se
// sostiene el pulso final este tiempo antes de avisar DONE: los cuatro
// quedan quietos en la misma posicion recien ahi
const unsigned long ASENTAMIENTO_MS = 250;

bool enMovimiento = false;   // rampa en curso
bool asentando = false;      // rampa terminada, esperando que los servos lleguen
unsigned long finAsentamiento = 0;

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

// momento del ultimo paso de movimiento (loop no bloqueante: sin delay, el
// serial y el watchdog se chequean en cada vuelta)
unsigned long ultimoPaso = 0;

String textoSerial = "";

// un solo objeto Servo: el pulso sale por D3 y llega a los 4 servos
Servo servoGripper;

// hay rampa o asentamiento pendiente (para STATUS y para aceptar/rechazar
// comandos igual que antes)
bool ocupado() {
  return enMovimiento || asentando;
}

// fija el destino y arranca el movimiento suave. El clamp evita que una
// calibracion futura (o un valor fuera de rango) deje al servo pegado contra
// el tope mecanico mientras el firmware cree que todavia no llego
void empezar(int d, bool porWatchdog = false) {
  destino = constrain(d, min(US_ABIERTO, US_CERRADO), max(US_ABIERTO, US_CERRADO));
  enMovimiento = true;
  asentando = false;
  movimientoPorWatchdog = porWatchdog;
  ultimoPaso = millis();
}

// aca se decide que hacer con cada comando
void procesar(String linea) {
  bool valido = true;

  if (linea == "OPEN") {
    if (ocupado()) {
      // redirige en pleno movimiento (por ej. sale de un cierre a mitad).
      // La PC ve BUSY, espera READY y reintentara el OPEN
      empezar(US_ABIERTO);
      Serial.println("BUSY");
    } else {
      // sin respuesta inmediata: el DONE llega al completar la carrera
      // (se va en el acto si ya estaba abierto)
      empezar(US_ABIERTO);
    }
  } else if (linea == "CLOSE") {
    if (ocupado()) {
      // un cierre a mitad de movimiento no se acepta: primero tiene que
      // quedar quieto (o llegar a abierto). La PC reintenta tras READY
      Serial.println("BUSY");
    } else {
      empezar(US_CERRADO);
    }
  } else if (linea == "ZERO") {
    // puesta a cero mecanica: cierra despacio hasta el contacto
    // mordaza-mordaza y se queda ahi (DONE al asentar). Es la referencia
    // comun de los 4 servos: al tocarse las mordazas, la diferencia de
    // montaje de cada cuerno queda absorbida por el tope mecanico. Desde aca
    // se alinean los cuernos o se arranca un ciclo. Con la boca vacia: si
    // hay una pieza, la aprieta. Para volver a abrir: OPEN o STOP (o el
    // watchdog a los 15 s si la PC deja de mandar comandos)
    if (ocupado()) {
      Serial.println("BUSY");
    } else {
      empezar(US_CERRADO);
    }
  } else if (linea == "STOP") {
    // parada suave: desde cualquier estado redirige a abierto
    if (ocupado()) {
      empezar(US_ABIERTO);
      Serial.println("BUSY");
    } else if (posicion != US_ABIERTO) {
      // arranca la apertura; DONE al terminar la carrera
      empezar(US_ABIERTO);
    } else {
      Serial.println("DONE");
    }
  } else if (linea == "STATUS") {
    // STATUS es solo consulta de movimiento; tambien mantiene vivo el
    // watchdog (PC viva = no abrir solo)
    Serial.println(ocupado() ? "BUSY" : "READY");
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
  if ((millis() - ultimaComunicacion > WATCHDOG_MS) && (destino != US_ABIERTO)) {
    Serial.println("WATCHDOG_OPEN");
    empezar(US_ABIERTO, true);
  }
}

// avanza la rampa (o cierra el asentamiento) sin bloquear el loop
void actualizarMovimiento() {
  if (enMovimiento) {
    if (millis() - ultimoPaso < PASO_MS) {
      return;
    }
    ultimoPaso = millis();

    if (posicion < destino) {
      posicion = min(posicion + PASO_US, destino);
    } else if (posicion > destino) {
      posicion = max(posicion - PASO_US, destino);
    }
    servoGripper.writeMicroseconds(posicion);

    if (posicion == destino) {
      enMovimiento = false;
      asentando = true;
      finAsentamiento = millis() + ASENTAMIENTO_MS;
    }
  } else if (asentando && millis() >= finAsentamiento) {
    // los 4 servos tuvieron tiempo de llegar: recien ahora la PC puede
    // seguir con el ciclo sin encontrarse una mordaza a mitad de camino
    asentando = false;
    Serial.println(movimientoPorWatchdog ? "WATCHDOG_DONE" : "DONE");
    movimientoPorWatchdog = false;
  }
}

// Puesta a cero al arrancar: si se deja en true, antes de avisar READY el
// firmware cierra despacio hasta el tope mecanico, asienta y vuelve a abrir.
// Deja a los 4 servos partiendo de la misma referencia en cada encendido.
// Se deja en false a proposito: el UNO tambien se resetea cada vez que se
// abre el puerto serie (DTR), y un reset en medio de un ciclo cerraria sobre
// la pieza. Para poner a cero: comando ZERO con la boca vacia.
// Solo con la boca vacia si se activa.
const bool CERO_AL_ARRANCAR = false;

// rampa bloqueante, solo para setup(): misma velocidad que la del loop
void rampaBloqueante(int hacia) {
  while (posicion != hacia) {
    if (posicion < hacia) {
      posicion = min(posicion + PASO_US, hacia);
    } else {
      posicion = max(posicion - PASO_US, hacia);
    }
    servoGripper.writeMicroseconds(posicion);
    delay(PASO_MS);
  }
  delay(ASENTAMIENTO_MS);
  destino = posicion;
}

void setup() {
  Serial.begin(115200);

  servoGripper.attach(pinSenal);

  // arranca abierto. Ojo: el primer pulso hace que los 4 vayan a US_ABIERTO
  // a su velocidad maxima, vengan de donde vengan (el firmware no sabe donde
  // quedaron al cortar la alimentacion). Es el arranque mas seguro: abierto
  // y sin cerrar sobre nada
  servoGripper.writeMicroseconds(posicion);

  if (CERO_AL_ARRANCAR) {
    rampaBloqueante(US_CERRADO);
    rampaBloqueante(US_ABIERTO);
  }

  ultimaComunicacion = millis();
  ultimoPaso = millis();
  Serial.println("READY");
}

void loop() {
  revisarSerial();
  actualizarMovimiento();
}
