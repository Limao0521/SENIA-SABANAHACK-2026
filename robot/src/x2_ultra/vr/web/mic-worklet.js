// Micrófono de las gafas → PCM s16 a 16 kHz en tramas de 20 ms (320 muestras), para el WebSocket.
// Remuestrea con interpolación lineal desde la frecuencia del AudioContext (normalmente 48 kHz).
class Mic16k extends AudioWorkletProcessor {
  constructor() {
    super();
    this.step = sampleRate / 16000;
    this.pos = 0;          // posición fraccionaria en la entrada pendiente
    this.prev = 0;         // última muestra del bloque anterior
    this.frame = new Int16Array(320);
    this.n = 0;
    this.peak = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    while (this.pos < input.length) {
      const i = Math.floor(this.pos);
      const f = this.pos - i;
      const a = i === 0 ? this.prev : input[i - 1];
      const b = input[i];
      const s = Math.max(-1, Math.min(1, a + (b - a) * f));
      this.peak = Math.max(this.peak, Math.abs(s));
      this.frame[this.n++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.n === this.frame.length) {
        this.port.postMessage({ pcm: this.frame.buffer, peak: this.peak }, [this.frame.buffer]);
        this.frame = new Int16Array(320);
        this.n = 0;
        this.peak = 0;
      }
      this.pos += this.step;
    }
    this.pos -= input.length;
    this.prev = input[input.length - 1];
    return true;
  }
}

registerProcessor('mic-16k', Mic16k);
