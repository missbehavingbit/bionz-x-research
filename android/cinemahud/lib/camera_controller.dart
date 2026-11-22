import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/foundation.dart';

import 'sony_ptp.dart';
import 'latency_stats.dart';

const int _wifiFrameMagic = 0xA6405646;
const int _wifiFrameHdrSize = 24;
const int _wifiMaxFrameBytes = 2 * 1024 * 1024;

class _ReceivedFrame {
  final Uint8List bytes;
  final int arrivedUs;
  _ReceivedFrame(this.bytes, this.arrivedUs);
}

class CameraController extends ChangeNotifier {
  CameraController() {
    videoHttp.connectionFactory = (uri, proxyHost, proxyPort) async {
      final task = await Socket.startConnect(
        proxyHost ?? uri.host,
        proxyPort ?? uri.port,
      );
      return ConnectionTask.fromSocket(
        task.socket.then((socket) {
          socket.setOption(SocketOption.tcpNoDelay, true);
          return socket;
        }),
        task.cancel,
      );
    };
  }
  final usb = UsbPtp();
  final http = HttpClient()..connectionTimeout = const Duration(seconds: 5);
  final videoHttp = HttpClient()
    ..connectionTimeout = const Duration(seconds: 5)
    ..maxConnectionsPerHost = 1;
  final latency = LatencyStats();
  final _pipelineClock = Stopwatch()..start();
  _ReceivedFrame? _pendingFrame;
  bool _decoding = false;
  int droppedBeforeDecode = 0;
  int bridgeSequence = -1;
  String? _bridgeStreamId;
  bool _statusPending = false;
  bool get latestProtocol => state['frame_transport'] == 'latest-longpoll-v1';
  Map<String, dynamic> state = {};
  ui.Image? image;
  String? bridge;
  String? wifiHost;
  int wifiPort = 9640;
  Socket? _wifiSocket;
  bool get isWifiDirect => wifiHost != null;
  String message = 'Connect Sony α6400 · 5 GHz Wi-Fi or USB';
  bool connected = false, busy = false, connecting = false;
  int _epoch = 0;
  double fps = 0;
  DateTime? lastFrame;
  Map<String, dynamic> get controls =>
      Map<String, dynamic>.from(state['controls'] as Map? ?? {});

  Future<Map<String, dynamic>> _json(
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final uri = Uri.parse('$bridge$path');
    final req = body == null ? await http.getUrl(uri) : await http.postUrl(uri);
    if (body != null) {
      req.headers.contentType = ContentType.json;
      final bytes = utf8.encode(jsonEncode(body));
      req.contentLength = bytes.length;
      req.add(bytes);
    }
    final response = await req.close().timeout(const Duration(seconds: 15));
    final result = jsonDecode(
      await response.transform(utf8.decoder).join(),
    ) as Map<String, dynamic>;
    if (response.statusCode != 200) {
      throw StateError(
        result['message']?.toString() ?? 'HTTP ${response.statusCode}',
      );
    }
    return result;
  }

  (String, int) _parseWifiAddress(String raw) {
    var s = raw.trim();
    if (s.startsWith('wifi://')) {
      s = s.substring(7);
    } else if (s.startsWith('tcp://')) {
      s = s.substring(6);
    }
    s = s.replaceFirst(RegExp(r'/.*$'), '');
    if (s.isEmpty) {
      throw const FormatException('Enter camera IP address (e.g. 192.168.100.17:9640)');
    }
    final idx = s.lastIndexOf(':');
    if (idx > 0 && idx < s.length - 1) {
      final host = s.substring(0, idx).trim();
      final port = int.tryParse(s.substring(idx + 1).trim());
      if (host.isNotEmpty && port != null && port > 0 && port <= 65535) {
        return (host, port);
      }
    }
    return (s, 9640);
  }

  Future<void> connect({String? bridgeUrl, String? wifiAddress}) async {
    if (connecting || connected) return;
    connecting = true;
    bridgeSequence = -1;
    _bridgeStreamId = null;
    _pendingFrame = null;
    droppedBeforeDecode = 0;
    bridge = null;
    wifiHost = null;
    if (wifiAddress != null) {
      message = 'Connecting 5 GHz Wi-Fi…';
    } else if (bridgeUrl != null) {
      message = 'Connecting to PC…';
    } else {
      message = 'Waiting for USB camera permission…';
    }
    notifyListeners();
    final epoch = ++_epoch;
    try {
      if (wifiAddress != null) {
        final (host, port) = _parseWifiAddress(wifiAddress);
        final sock = await Socket.connect(
          host,
          port,
          timeout: const Duration(seconds: 5),
        );
        sock.setOption(SocketOption.tcpNoDelay, true);
        if (epoch != _epoch) {
          sock.destroy();
          return;
        }
        wifiHost = host;
        wifiPort = port;
        _wifiSocket = sock;
        connected = true;
        message = '5 GHZ WI-FI';
        unawaited(_wifiLoop(epoch, sock));
      } else if (bridgeUrl != null) {
        bridge = bridgeUrl.replaceFirst(RegExp(r'/+$'), '');
        final uri = Uri.parse(bridge!);
        if (!['http', 'https'].contains(uri.scheme) || uri.host.isEmpty) {
          throw const FormatException('Enter a valid server URL');
        }
        state = await _json('/api/status');
        if (epoch != _epoch) return;
        connected = true;
        message = 'PC BRIDGE';
        unawaited(_loop(epoch));
      } else {
        await usb.connect();
        state = usb.snapshot();
        if (epoch != _epoch) return;
        connected = true;
        message = 'USB PTP';
        unawaited(_loop(epoch));
      }
    } catch (e) {
      message = e.toString();
      if (bridge == null && wifiHost == null) {
        try {
          await usb.disconnect();
        } catch (_) {}
      }
    } finally {
      connecting = false;
      notifyListeners();
    }
  }

  Future<void> _wifiLoop(int epoch, Socket initialSocket) async {
    Socket? sock = initialSocket;
    var frames = 0;
    final clock = Stopwatch()..start();
    var lastArriveUs = _pipelineClock.elapsedMicroseconds;

    while (connected && epoch == _epoch) {
      try {
        if (sock == null) {
          final nextSock = await Socket.connect(
            wifiHost!,
            wifiPort,
            timeout: const Duration(seconds: 4),
          );
          nextSock.setOption(SocketOption.tcpNoDelay, true);
          if (epoch != _epoch || !connected) {
            nextSock.destroy();
            break;
          }
          sock = nextSock;
          _wifiSocket = sock;
          message = '5 GHZ WI-FI';
          notifyListeners();
        }

        var buf = Uint8List(0);
        var offset = 0;

        await for (final chunk in sock) {
          if (epoch != _epoch || !connected) break;
          final avail = buf.length - offset;
          if (avail == 0) {
            buf = chunk;
            offset = 0;
          } else {
            final merged = Uint8List(avail + chunk.length);
            merged.setRange(0, avail, buf, offset);
            merged.setRange(avail, avail + chunk.length, chunk);
            buf = merged;
            offset = 0;
          }

          while (buf.length - offset >= _wifiFrameHdrSize) {
            final bd = ByteData.sublistView(buf, offset, offset + _wifiFrameHdrSize);
            final magic = bd.getUint32(0, Endian.little);
            final jpegLen = bd.getUint32(8, Endian.little);
            if (magic != _wifiFrameMagic ||
                jpegLen == 0 ||
                jpegLen > _wifiMaxFrameBytes) {
              throw StateError(
                'Invalid 5 GHz frame header magic=0x${magic.toRadixString(16)} len=$jpegLen',
              );
            }
            final total = _wifiFrameHdrSize + jpegLen;
            if (buf.length - offset < total) break;

            final frameBytes = Uint8List.fromList(
              buf.sublist(offset + _wifiFrameHdrSize, offset + total),
            );
            offset += total;

            final nowUs = _pipelineClock.elapsedMicroseconds;
            latency.add('camera_to_phone_fetch', (nowUs - lastArriveUs) / 1000);
            lastArriveUs = nowUs;

            if (_pendingFrame != null) droppedBeforeDecode++;
            _pendingFrame = _ReceivedFrame(frameBytes, nowUs);
            _kickDecode(epoch);
            frames++;
            fps = frames * 1000 / clock.elapsedMilliseconds.clamp(1, 1 << 30);
          }

          if (offset == buf.length) {
            buf = Uint8List(0);
            offset = 0;
          }
        }
      } catch (e) {
        if (epoch != _epoch || !connected) break;
        message = 'Reconnecting 5 GHz Wi-Fi… ($e)';
        notifyListeners();
      } finally {
        try {
          sock?.destroy();
        } catch (_) {}
        if (_wifiSocket == sock) _wifiSocket = null;
        sock = null;
      }
      if (connected && epoch == _epoch) {
        await Future<void>.delayed(const Duration(milliseconds: 500));
      }
    }
  }

  Future<void> _refreshBridgeStatus(int epoch) async {
    if (_statusPending) return;
    _statusPending = true;
    try {
      final next = await _json('/api/status');
      if (epoch == _epoch && connected && !busy) {
        final previousStamp =
            (state['settings_updated'] as num?)?.toDouble() ?? 0;
        final nextStamp = (next['settings_updated'] as num?)?.toDouble() ?? 0;
        if (nextStamp >= previousStamp) {
          state = next;
          notifyListeners();
        }
      }
    } catch (_) {
      // A slow status endpoint must not stall or invalidate a healthy video stream.
    } finally {
      _statusPending = false;
    }
  }

  Future<void> _loop(int epoch) async {
    var lastSettings = DateTime.fromMillisecondsSinceEpoch(0),
        errors = 0,
        frames = 0,
        noFrameStreak = 0;
    final clock = Stopwatch()..start();
    while (connected && epoch == _epoch) {
      try {
        if (!busy &&
            DateTime.now().difference(lastSettings).inMilliseconds > 1000) {
          if (bridge != null) {
            unawaited(_refreshBridgeStatus(epoch));
          } else {
            await usb.refresh();
            state = usb.snapshot();
          }
          lastSettings = DateTime.now();
        }
        Uint8List? bytes;
        final fetchClock = Stopwatch()..start();
        if (bridge != null) {
          final request = await videoHttp.getUrl(
            Uri.parse(
              latestProtocol
                  ? '$bridge/frame.jpg?after=$bridgeSequence'
                  : '$bridge/frame.jpg',
            ),
          );
          final response = await request.close().timeout(
            const Duration(seconds: 5),
          );
          if (epoch != _epoch || !connected) {
            await response.drain<void>();
            break;
          }
          final streamId = response.headers.value('x-cinema-stream-id');
          if (streamId != null) {
            if (_bridgeStreamId != streamId) {
              bridgeSequence = -1;
            }
            _bridgeStreamId = streamId;
          }
          if (response.statusCode == 200) {
            bytes = await consolidateHttpClientResponseBytes(response);
            final sequence = int.tryParse(
              response.headers.value('x-cinema-sequence') ?? '',
            );
            if (sequence != null) {
              if (sequence <= bridgeSequence) {
                bytes = null;
              } else {
                bridgeSequence = sequence;
              }
            }
            final age = double.tryParse(
              response.headers.value('x-cinema-host-age-ms') ?? '',
            );
            final ptp = double.tryParse(
              response.headers.value('x-cinema-fetch-ms') ?? '',
            );
            if (age != null) latency.add('pc_age_at_send', age);
            if (ptp != null) latency.add('camera_to_pc_fetch', ptp);
          } else {
            await response.drain<void>();
          }
        } else {
          bytes = await usb.frame();
        }
        if (epoch != _epoch || !connected) break;
        latency.add(
          bridge != null ? 'bridge_wait_and_transfer' : 'camera_to_phone_fetch',
          fetchClock.elapsedMicroseconds / 1000,
        );
        if (bytes != null && bytes.isNotEmpty) {
          noFrameStreak = 0;
          if (_pendingFrame != null) droppedBeforeDecode++;
          _pendingFrame = _ReceivedFrame(
            bytes,
            _pipelineClock.elapsedMicroseconds,
          );
          _kickDecode(epoch);
          frames++;
          fps = frames * 1000 / clock.elapsedMilliseconds.clamp(1, 1 << 30);
        } else {
          noFrameStreak++;
          await Future<void>.delayed(
            Duration(milliseconds: (5 * noFrameStreak).clamp(5, 50)),
          );
        }
        errors = 0;
      } catch (e) {
        errors++;
        message = e.toString();
        notifyListeners();
        if (errors >= 3) {
          await disconnect();
          message = 'Disconnected: $e';
          notifyListeners();
          break;
        }
      }
      // No fixed post-frame delay. The source/long-poll provides pacing.
      // Legacy bridges lack sequence-aware wait; cap their duplicate polling.
      if (bridge != null && !latestProtocol) {
        await Future<void>.delayed(const Duration(milliseconds: 8));
      }
    }
  }

  void _kickDecode(int epoch) {
    if (_decoding || _pendingFrame == null || !connected) return;
    _decoding = true;
    unawaited(_decodeLatest(epoch));
  }

  Future<void> _decodeLatest(int epoch) async {
    var failures = 0;
    try {
      while (_pendingFrame != null && connected && epoch == _epoch) {
        final packet = _pendingFrame!;
        _pendingFrame = null;
        latency.add(
          'phone_decode_queue',
          (_pipelineClock.elapsedMicroseconds - packet.arrivedUs) / 1000,
        );
        try {
          final decodeClock = Stopwatch()..start();
          final codec = await ui.instantiateImageCodec(packet.bytes);
          ui.FrameInfo frame;
          try {
            frame = await codec.getNextFrame();
          } finally {
            codec.dispose();
          }
          latency.add('phone_decode', decodeClock.elapsedMicroseconds / 1000);
          if (epoch != _epoch || !connected) {
            frame.image.dispose();
            break;
          }
          latency.add(
            'phone_ready_after_receive',
            (_pipelineClock.elapsedMicroseconds - packet.arrivedUs) / 1000,
          );
          final old = image;
          image = frame.image;
          if (old != null) {
            Future<void>.delayed(
              const Duration(milliseconds: 100),
              old.dispose,
            );
          }
          lastFrame = DateTime.now();
          failures = 0;
          notifyListeners();
        } catch (e) {
          failures++;
          message = 'Image decode: $e';
          notifyListeners();
          if (failures >= 3) {
            await disconnect();
            break;
          }
        }
      }
    } finally {
      _decoding = false;
      // A reconnect may have queued a frame while an old codec was finishing.
      if (connected && _pendingFrame != null) _kickDecode(_epoch);
    }
  }

  Future<void> apply(String name, int value) async {
    if (!connected || busy) return;
    busy = true;
    notifyListeners();
    try {
      if (bridge != null) {
        final result = await _json('/api/control', {
          'param': name,
          'value': value,
        });
        if (result['applied'] != true) {
          throw StateError('Camera did not confirm value');
        }
        state = await _json('/api/status');
      } else {
        await usb.apply(name, value);
        state = usb.snapshot();
      }
      message = '${name.toUpperCase()} confirmed by camera';
    } catch (e) {
      message = e.toString();
      if (bridge == null) {
        state = usb.snapshot();
      }
      rethrow;
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> disconnect() async {
    connected = false;
    ++_epoch;
    _pendingFrame = null;
    final ws = _wifiSocket;
    _wifiSocket = null;
    final wasUsb = bridge == null && wifiHost == null;
    wifiHost = null;
    bridge = null;
    if (ws != null) {
      try {
        ws.destroy();
      } catch (_) {}
    }
    if (wasUsb) {
      try {
        await usb.disconnect();
      } catch (_) {}
    }
    state = {};
    message = 'Camera disconnected';
    notifyListeners();
  }
}
