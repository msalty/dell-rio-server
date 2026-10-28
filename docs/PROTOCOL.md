# Rio Receiver protocols

This page collects what is known about how the Rio Receiver and Dell Digital
Audio Receiver talk to their server. RioServer NG implements all of it. Most
of the details come from JReceiver's source (Reed Esau, 2001–2003), RioPlay's
source (David Flowerday, 2002) and the kernel in RioPlay's receiver image.

## 1. Discovery (SSDP-like, UDP 21075)

The receiver sends a datagram to `255.255.255.255:21075` from port 21075.
The server answers with a single line, sent to the source address and port.

| Request starts with | Sent by | Reply |
|---|---|---|
| `upnp:uuid:1D274DB1-F053-11d3-BF72-0050DA689B2F` followed by the MAC | the kernel, looking for its root filesystem | `http://<server-ip>/descriptor.xml\r\n` |
| `upnp:uuid:1D274DB0-F053-11d3-BF72-0050DA689B2F` | the player, looking for music | `http://<server-ip>:<port>/descriptor.xml\r\n` |

Dell-branded units send a different tail on the UUID, so servers should only
match the prefix up to `…0050DA689B`. The kernel parses the reply as
`http://<ip>[:<port>]/…`. Without a port it asks the portmapper.

## 2. Network boot (NFSv2 over UDP)

The receiver's Linux 2.2 kernel (`2.2.14-rmk4-mercury`) mounts its root
filesystem with NFS version 2:

1. portmapper (100000 v2, port 111): `GETPORT` for mountd (100005 v1) and
   nfsd (100003 v2). If no portmapper answers, it uses 627 and 2049.
2. mountd `MNT` of `/tftpboot/<receiver-ip>`. The original Windows server
   used `/export/mercury` on port 21078. RioServer NG accepts any path.
3. `LOOKUP`/`READ`/`READLINK`/`GETATTR` as it runs `/sbin/init`.

A firmware image (`receiver.arf`) is a tar archive of that filesystem:

| Path | |
|---|---|
| `/zImage` | the kernel |
| `/il-binary.o` | Broadcom iLine10 HomePNA driver |
| `/sbin/init` | the player (or a wrapper that starts it) |
| `/dev/display` | char 244,0: 4096-byte frame buffer (mmap), 128×64, a nibble per pixel, values 0–3 |
| `/dev/ir` | char 242,0: 32-bit key codes |
| `/dev/audio`, `/dev/dsp` | char 245,4 / 245,3: 16-bit stereo 44.1 kHz PCM, written in 4608-byte blocks |
| `/dev/mixer` | char 245,0: OSS `MIXER_WRITE(SOUND_MIXER_VOLUME)` |
| `/dev/console` | the serial port |

Display ioctls (type `'d'`): `0` refreshes the screen from the frame buffer,
`1` (value) powers it on or off, and `11` (pointer to int; 4 = on, 0 = off)
sets the backlight.

Key codes. Front-panel buttons send *press* (even) and *release* (odd) codes.
Remote codes have bit 31 set on auto-repeat.

| Key | Panel | Remote |
|---|---|---|
| Play | 0x10004 | 0x185 |
| Stop | 0x10002 | 0x190 |
| Next | 0x10008 | 0x187 |
| Previous | 0x1000a | 0x183 |
| Menu | 0x10012 | 0x188 |
| Knob push / Enter | 0x10010 | 0x182 |
| Random | 0x10014 | 0x189 |
| Power | 0x10018 / 0x10019 | 0x184 |
| Knob clockwise / anticlockwise | 0x28000 / 0x28001 | — |
| Up / Down | — | 0x18c / 0x186 |
| Volume + / – | — | 0x18a / 0x181 |
| Search / List | — | 0x19f / 0x19d |

## 3. Music protocol (HTTP, port 12078)

Every object has a 32-bit ID ("fid"), written in hex in URLs. `0x100` means
"all playlists". Responses carry `Server: Mercury-Content-Server/0.11`.

### Menus

`GET /query?<field>=<prefix>`, where field is `artist`, `source` (album),
`genre` or `title`. The prefix may be a regular expression, which is what the
remote's phone-keypad search sends (`[5jkl][5jkl][4ghi]`). `_begin`/`_end`
select a range.

```
matches=
0=5,0,0:The Beatles
1=2,0,0:Björk
```

Each line is `index=count,0,0:name`.

### Tracks for a menu entry

`GET /results?<field>=<exact name>&_extended=N`

- `_extended=1` returns text: `<fid hex>=T<title>\r\n` per track.
- `_extended=0` returns binary: little-endian `fid, size` pairs.
- `_extended=2` returns binary: little-endian `fid, size, data offset` triples.

### Tags

`GET /tags` lists the tag names (`fid`, `title`, `artist`, `source`, `year`,
`comment`, `length`, `type`, `path`, `genre`, `bitrate`, `playlist`, `codec`,
`offset`, `duration`, `tracknr`).

`GET /tags/<fid>` returns a binary record: repeated `key, length, bytes`
entries terminated by `0xFF`. The keys are 0 fid, 1 title, 2 artist, 3 album,
4 year, 6 size, 7 type (`tune`/`playlist`), 8 path, 9 genre, 10 bitrate
(e.g. `fs128`, `vs192`), 12 codec, 13 offset, 14 duration (ms) and 15 track
number.

### Playlists

- `GET /content/100?_extended=1` lists all playlists as `<fid>=P<name>`.
- `GET /content/<playlist fid>?_extended=1` lists a playlist's tracks as
  `<fid>=T<title>`. Without `_extended` it returns binary fids.
- `GET /list/<fid>?_extended=2` returns binary triples for a playlist or a
  single track.

### Audio

`GET /content/<track fid>` with `Range: bytes=a-b` returns
`206 Partial Content` and `Content-Range: bytes=a-b/*`. Note the `=`, which
is what JReceiver sent to the stock player. Without a Range header the
response is the whole stream. Offsets start after the ID3v2 tag.

### Display layouts

`GET /layout/en_UK/index` lists the stock player's screen layouts, and
`/layout/en_UK/<name>` returns each one as little-endian dwords. David
Schuetz decoded the format; the field list is in
`server/rioserver/rio_classic.py`.

## 4. RioServer NG firmware protocol

The NG firmware uses a few extra endpoints. Responses are Latin-1 text whose
first line is `RNG 1`, followed by tab-separated records.

- `GET /ng/menu/<node>?b=&n=` returns `title`, `total`, `begin`, then
  `i <kind> <ref> <label> <detail>` per item. Kind `M` is a submenu, `T` a
  track and `S` "shuffle this node".
- `GET /ng/queue/<node>?first=&shuffle=` returns `total`, then one track fid
  per line.
- `GET /ng/track/<fid>` returns key/value lines: title, artist, album, track,
  tracks, dur, kbps, size and so on.
- `GET /ng/poll?mac=&st=&id=&pos=&vol=&qi=&qn=&wake=&stall=` reports the
  receiver's state every 3 seconds. The reply carries `lib <version>` and any
  `cmd …` lines queued by the web interface. The firmware runs the poll
  alongside playback, so a slow reply never holds up the audio. `stall` is
  the longest time (ms) the player loop was held up during playback since the
  previous poll. The server logs a warning above 250 ms, which probably means
  an audible skip.

The firmware listens on UDP port 21076 (or another free port, reported as
`wake`). When the web interface queues a command, the server sends a datagram
there and the receiver polls at once, so commands take effect within one HTTP
round trip instead of up to 3 seconds later. The firmware ignores datagrams
from any address other than the music server's. After running commands it
polls again 300 ms later to report its new state. While a slider is dragged,
the server keeps only the newest `vol` (or `seek`) command the receiver hasn't
collected yet.

Nodes are paths that narrow step by step: `artists`, `ar:12`, `ar:12/al:40`,
`ar:12/all`, `ge:3/ar:12/al:40`, `pl:2`, and `q:7` for a queue created by the
web interface.
