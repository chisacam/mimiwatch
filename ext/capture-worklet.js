/* Takes the tab's sound on the audio thread and hands it to the main thread.
 *
 * That is all it does. Buffering, int16 conversion and upload are app.js's job
 * entirely -- the audio callback comes back every 128 samples, so heavy work
 * here breaks the sound up. And what breaks up the sound breaks up what there
 * is to transcribe with it.
 *
 * It does no sample-rate conversion. Open the AudioContext at 16000Hz and
 * Chrome resamples on its own, so what arrives here is already 16kHz mono.
 */
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    // There is one channel. The node is built with channelCount:1, so folding
    // stereo down is Web Audio's job -- picking the left channel by hand here
    // would lose a voice panned right entirely.
    const ch = inputs[0] && inputs[0][0];
    // slice(0) is a copy. This buffer is reused on the next call, so handing it
    // over as it is has another sound overwrite it before the main thread reads.
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("mimiwatch-capture", CaptureProcessor);
