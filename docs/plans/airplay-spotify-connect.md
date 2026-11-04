# Implementation prompt: receivers as AirPlay and Spotify Connect speakers

Paste everything below the line into a new Claude Code session on this
repository to start the work.

---

Make each Rio receiver show up as an **AirPlay speaker** and a **Spotify
Connect speaker**. A phone picks "Kitchen" in its AirPlay menu or in
Spotify's device picker, and the music plays on the Kitchen receiver. The
server receives the audio, encodes it as a live MP3 stream and feeds it to
the receiver, the same way it already does the internet work for podcasts
so the receivers only ever see plain HTTP and MP3.

Read `README.md`, `docs/PROTOCOL.md` (sections 3 and 4), `firmware/README.md`
(especially "CPU budget") and `docs/IDEAS.md` first. The code that matters
most: `server/rioserver/players.py` (QueuePlayer), `receivers.py`
(ReceiverRegistry, commands, wake), `rio_ng.py` (the NG firmware protocol),
`rio_classic.py` (`send_audio`), `cast.py` (how an outside app taking over a
device is shown, `_other_app`), `web_api.py`, `web/app.js`, and the firmware's
`firmware/src/player.c`, `net.c` and `ui.c`.

## What exists today, and what's missing

- Receivers running the NG firmware fetch tracks with
  `GET /content/<fid>`, after reading `size` and `dur` from
  `GET /ng/track/<fid>`. `player.c` assumes a finite file: it seeks by
  converting milliseconds to a byte offset with `size` and `dur`, shows
  position against duration, and when the connection drops early it
  reconnects with a `Range` request (up to 3 times) before treating it as
  the end of the track.
- The server tells a receiver what to do through `cmd …` lines in the reply
  to its 3-second `/ng/poll`, and a UDP datagram on the receiver's wake port
  makes it poll at once. Firmware 1.7 wakes a receiver from standby when
  music is started from the web.
- The receiver's CPU is a 74 MHz ARM720T. A 320 kbps MP3 keeps it about 97%
  busy, with dropouts. Live streams must use a modest constant bitrate
  (start at 160 kbps CBR, 44.1 kHz stereo, and confirm on hardware with the
  poll's `cpu`, `gaps` and `under` figures).
- Only receivers running the RioServer NG firmware can take part. Receivers
  on the original SonicBlue/Dell firmware or RioPlay are left out.

## What to build

### 1. A live MP3 stream on the server (shared groundwork)

A small, generic "live source" layer that internet radio (in
`docs/IDEAS.md`) can reuse later:

- Takes raw PCM (s16le, 44.1 kHz, stereo) from a source process's stdout
  or a pipe and encodes it to CBR MP3 with ffmpeg or LAME (add whichever you
  choose to the Docker image).
- Serves it on an endless HTTP response to any number of listeners. One
  slow listener must never block the encoder or other listeners: give each
  listener a bounded buffer and drop it if it falls too far behind.
- Starts the encoder when a session begins and stops it when the last
  session ends, so nothing runs while nobody is streaming.
- Carries now-playing metadata (title, artist, album, and cover art where
  the source provides it) and the sender's volume.

### 2. AirPlay, using shairport-sync

- One shairport-sync instance per enabled receiver, named after the
  receiver (its label, such as "Kitchen"), with its own port, using the
  pipe/stdout backend into the live source, and its metadata pipe for track
  info and cover art.
- **Start with classic AirPlay (AirPlay 1).** Debian's shairport-sync
  package supports it, iPhones and Macs still list classic receivers, and
  several instances on one host are straightforward. AirPlay 2 needs a
  source build with `nqptp`, and running several AirPlay 2 instances on one
  host needs verifying. Treat it as a follow-up, and note what you find.
- Map AirPlay's volume to the receiver's `vol` command.

### 3. Spotify Connect, using librespot

- One librespot instance per enabled receiver, named after the receiver,
  with the pipe backend into the live source and `--onevent` (or its event
  hook) for track changes and volume.
- Use zeroconf discovery mode, so the person's Spotify app authorises the
  device and the server stores no Spotify credentials. Spotify Connect needs
  a Spotify Premium account. Say so in the README.
- librespot isn't packaged in Debian bookworm, which `python:3.12-slim` is
  based on. Pick a way to get a pinned, recent release into the image for
  both amd64 and arm64, which the publish workflow builds (`.github/workflows/publish.yml`):
  a separate Docker build stage, or a checksummed prebuilt binary. Spotify
  changed its login APIs in 2024–2025, so older librespot versions no longer
  connect. Use a current release and record the version.

### 4. Running the receivers from these sources

- When a session starts on a receiver's AirPlay or Spotify instance, the
  server wakes the receiver if needed and switches it to the live stream,
  replacing what it was playing. Save the receiver's queue and position
  first, so the web interface can offer "Resume Up Next" afterwards. Don't
  resume it automatically.
- When the session ends (the phone stops or picks another speaker), stop
  the receiver and clear the live display.
- If a second sender takes over a receiver, the newest one wins, the way
  AirPlay and Spotify behave elsewhere.
- Starting the receiver's own music (from its menus or the web interface)
  ends the live session for that receiver: stop the shairport-sync or
  librespot playback where they allow it.
- On the receiver, Stop ends the live session. Volume keys change the
  receiver's volume as now. Sending pause, next and previous back to the
  phone (DACP for AirPlay; librespot has no equivalent hook) is a stretch
  goal. In v1, pause can behave like Stop.
- Play history and Top 25 don't count live audio.

### 5. Firmware: a live-stream mode (version 1.8)

Add a generic live mode to `player.c`, which internet radio will reuse:

- A new command, for example `cmd live <url-path> <label>`, plays an endless
  stream from the server. Document whatever you choose in `docs/PROTOCOL.md`.
- No `size` or `dur`: seeking is disabled, Now Playing shows elapsed time
  (or none) instead of a progress bar, and the stream ending isn't "the next
  track".
- When the connection drops, reconnect from scratch (there's nothing to
  resume with `Range`), a few times with a short backoff, then stop with a
  message on the display.
- A smaller start-up buffer than for files, to keep the delay down,
  without causing underruns. Measure it with the existing `under` and
  `gaps` counters.
- Now Playing shows the source ("AirPlay" or "Spotify"), the sender's name if
  available, and the title and artist, updated when the track changes. Add a
  way for the server to send metadata changes, such as a `meta` command in
  the poll reply, and make sure a change goes out at once through the wake
  datagram.
- Bump `VERSION` in `firmware/src/ui.c` to 1.8. Firmware older than 1.8
  doesn't get live sessions: its instance shouldn't be advertised, or the
  web interface should say the receiver needs a power cycle to update.
- Watch the CPU budget: anything per MP3 frame must stay cheap, and the
  metadata display must not redraw more than the title scroll already does.

### 6. Settings and the web interface

- Off by default. An environment variable turns the whole feature off
  (`RIO_AIRPLAY=0`, `RIO_SPOTIFY=0`) for people who don't want the extra
  processes, and the README's configuration table lists them.
- Per receiver, in the web interface: switches for "AirPlay" and "Spotify
  Connect". The advertised name follows the receiver's label, and renaming
  the receiver restarts its instances with the new name.
- Receiver cards and the "Play on" menu show a live session, such as
  "AirPlay from iPhone · Song – Artist", with Stop and the volume
  slider working. Reuse what Chromecasts show when another app has the
  device.

### 7. Network and discovery

The container uses host networking. AirPlay and Spotify Connect are found
over mDNS, and the server already uses python-zeroconf for Chromecasts.
Decide and document how shairport-sync and librespot advertise themselves
without fighting over port 5353 with each other, the server's own zeroconf
or the host's avahi-daemon (Umbrel runs one). Options include shairport-sync's
mDNS backends, librespot's built-in mDNS or dns-sd backend, or advertising
the services from the server with python-zeroconf. Test that the speakers
appear on an iPhone and in the Spotify app while the server runs on a host
that has avahi.

## Out of scope for this change

- Sending live audio to Chromecasts or browser receivers (keep the design
  open to it: it's just another listener on the stream URL).
- AirPlay 2 multi-room and Spotify group sessions.
- Internet radio itself. Building the firmware live mode and the server's
  live source generically is enough.

## Testing

- Unit tests for the live source: a fake PCM source (a generated tone)
  feeding the encoder, several listeners, a slow listener being dropped
  without stalling the others, start and stop with sessions, and metadata
  updates.
- Tests for session handling with fake shairport-sync and librespot
  processes (scripts that write PCM and emit the same events/metadata):
  receiver switched to live, queue saved, session end stops it, a second
  sender takes over, local playback ends the session.
- Firmware end to end under qemu (`server/tests/test_firmware_e2e.py`):
  the receiver plays a live stream for well past its buffer, shows the
  metadata, survives a dropped connection by reconnecting, and stops on
  Stop and when the session ends.
- `make -C firmware` and the full `python -m pytest` in `server/` pass, and
  the Docker image builds (CI runs both).
- Write a short checklist in the PR for testing on a real receiver: CPU and
  dropouts at the chosen bitrate, delay from phone to sound, speakers
  showing up on an iPhone and in Spotify, volume from the phone, and
  switching between AirPlay, Spotify and local music.

## Documentation

- README: a section next to "Chromecast and Google speakers" describing how
  to turn it on, the Spotify Premium requirement, the delay of a few
  seconds (fine for music, not for video), and that only NG firmware
  receivers take part. Add the new variables to the configuration table and
  any new ports to "Requirements on your network".
- `docs/PROTOCOL.md`: the live command, metadata, and how the live stream
  is served.
- `firmware/README.md`: the live mode and its CPU cost.

Work on a feature branch, commit in logical steps, and open a pull request
when it's ready.
