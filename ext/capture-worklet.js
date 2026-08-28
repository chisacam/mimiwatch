/* 탭에서 들리는 소리를 오디오 스레드에서 받아 메인 스레드로 넘깁니다.
 *
 * 하는 일은 이것뿐입니다. 모아 두기·int16 변환·전송은 전부 app.js가 합니다 --
 * 오디오 콜백은 128 표본마다 돌아오므로 여기서 무거운 일을 하면 소리가
 * 끊깁니다. 그러면 받아 적을 것도 같이 끊깁니다.
 *
 * 표본율 변환은 하지 않습니다. AudioContext를 16000Hz로 열면 크롬이 알아서
 * 리샘플해 주므로, 여기 들어오는 것은 이미 16kHz 모노입니다.
 */
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    // 채널은 하나입니다. 노드를 만들 때 channelCount:1 로 잡아 두어,
    // 스테레오를 접는 일은 Web Audio 가 합니다 -- 여기서 손으로 왼쪽만
    // 집으면 오른쪽에 치우친 목소리를 통째로 놓칩니다.
    const ch = inputs[0] && inputs[0][0];
    // slice(0)는 사본입니다. 이 버퍼는 다음 호출에서 재사용되므로 그대로
    // 넘기면 메인 스레드가 읽기 전에 다른 소리로 덮입니다.
    if (ch && ch.length) this.port.postMessage(ch.slice(0));
    return true;
  }
}
registerProcessor("mimiwatch-capture", CaptureProcessor);
