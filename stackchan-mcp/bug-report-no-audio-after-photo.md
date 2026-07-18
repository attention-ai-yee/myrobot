# [bug] TTS audio goes silent after `camera.take_photo` during speech — audio stream sequence desync (v1.4.4)

## Summary

When the AI agent calls `self.camera.take_photo` **while a TTS answer is being streamed**, the speaker goes silent: the device state machine stays in `speaking`, subtitles keep scrolling on the LCD, but no audio comes out. The audio receiver appears to reset its MQTT sequence state during the photo flow and then drops every subsequent audio packet as "wrong sequence".

## Environment

- Device: M5Stack StackChan (K151, CoreS3 main unit)
- Firmware: factory `stack-chan` **v1.4.4** (compiled Jul 10 2026, ESP-IDF v5.5.4)
- Protocol: MQTT (WiFi, RSSI ≈ -42 dBm, so signal strength is not the issue)
- Serial log captured over USB (115200 baud) during reproduction

## Steps to reproduce

1. Enter AI Agent mode.
2. Ask a question that triggers vision, e.g. "你现在看到了什么" ("What do you see?").
3. The agent starts speaking, then calls `self.camera.take_photo` mid-speech.
4. When the vision answer is played back, the LCD shows the text and the avatar is animated, but the speaker is silent.

## Expected behavior

TTS audio keeps playing normally before/during/after a photo capture.

## Actual behavior

Speaker is silent after the photo flow. From the serial log, audio packets are dropped with `wrong sequence` warnings starting immediately after the image is uploaded.

## Serial log evidence (annotated)

```
I (72369) StateMachine: State: listening -> speaking          <- TTS streaming starts
I (72699) Application: << % self.camera.take_photo...         <- photo taken DURING speech
I (72769) AudioService: Resampling audio from 48000 to 24000  <- audio pipeline reconfigured
W (72899) StackChanCamera: mmap_buffers_[buf.index].length = 153600, frame.width = 320, frame.height = 240
I (73309) HttpClient: Established new connection to api.xiaozhi.me:80
I (79609) StackChanCamera: Explain image size=153600 bytes, compressed size=10487, remain stack size=4896
W (80619) MQTT: Received audio packet with wrong sequence: 151, expected: 1
                                                            <- device expects seq 1 (stream state was RESET),
                                                               server is already at seq 151 -> everything dropped
I (87419) SystemInfo: free sram: 34027 minimal sram: 4927   <- SRAM bottomed out at ~4.9 KB during photo
```

Sporadic `wrong sequence` warnings (one dropped packet each) also continue in later speaking sessions:

```
W (209219) MQTT: Received audio packet with wrong sequence: 6, expected: 5
W (209519) MQTT: Received audio packet with wrong sequence: 11, expected: 10
W (209989) MQTT: Received audio packet with wrong sequence: 19, expected: 18
... (22 occurrences within ~3.5 minutes of speech)
```

## Root cause analysis (hypothesis)

1. The photo flow allocates a 153600-byte frame buffer, runs JPEG compression and a ~6.3 s HTTP upload. Free SRAM bottoms out at **4927 bytes** during this window.
2. At the same time the audio pipeline is reconfigured (`Resampling audio from 48000 to 24000`), and the MQTT audio receive/sequence state appears to be reset.
3. When TTS streaming resumes, the device expects sequence number 1 while the server is already at 151, so every packet is discarded as out-of-order → state machine says `speaking`, subtitles scroll, but the DAC receives nothing.

The persistent sporadic packet loss in later sessions may share the same handling weakness (dropped packet → no resync request / no graceful recovery). This may also be related to the choppy-TTS report in another issue.

## Suggestions

- On TTS stream start/resume, re-sync the expected sequence number to the first received packet instead of assuming 1, or request retransmission on desync.
- Avoid resetting the audio receive path when the camera capture reconfigures sampling (48000 → 24000).
- Consider a dedicated memory pool (or PSRAM) for the camera frame + JPEG buffer so audio buffers cannot be starved (free SRAM hit 4.9 KB).
- The camera warning `frame.width = 320, frame.height = 240` while `mmap_buffers_` expects 153600 bytes also looks unintended (GC0308 is 640x480).

Happy to provide the full serial log or run further tests if useful.
