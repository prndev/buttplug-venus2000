#include <Arduino.h>
#include <Servo.h>

Servo servo;

void setup() {
  servo.attach(28); // this is GP28 (physical pin 34 on the Pico)
  Serial.begin(9600);
}

void loop() {
  if (Serial.available() > 0) {
    char incomingByte = Serial.read();
    servo.write(incomingByte);
  }
}
