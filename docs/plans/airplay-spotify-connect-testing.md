# Testing AirPlay and Spotify Connect (branch `claude/airplay-spotify-connect`)

Notes for trying the branch on real receivers before deciding whether to
merge it. The plan is in `airplay-spotify-connect.md`. No pull request
has been opened.

## 1. Get a test image

1. On GitHub, open **Actions › Publish image › Run workflow**, choose the
   branch `claude/airplay-spotify-connect` under "Use workflow from", and
   run it. (The workflow only runs by itself for `main` and tags, but
   `workflow_dispatch` works from any branch.)
2. It pushes, for amd64 and arm64:
   - `ghcr.io/msalty/dell-rio-server:claude-airplay-spotify-connect`.
     `docker/metadata-action`'s `type=ref,event=branch` turns the `/` in the
     branch name into `-`.
   - `ghcr.io/msalty/dell-rio-server:sha-<7-character commit>`.

   It doesn't touch `:main`, `:latest` or any version tag, so the published
   app keeps its image.

**Build time.** CI's Docker job (amd64 only, no cache) built the whole image
in **about 3 minutes** (09:21:43 → 09:24:42 on the first push). The Publish
workflow builds both architectures in one job on the same 4-CPU runner.
librespot and shairport-sync are cross-compiled on the runner, not under
QEMU, so the arm64 build costs about as much as the amd64 one. Here
(4 CPUs) the arm64 cross-builds took **51 s** for shairport-sync with its
FFmpeg and **171 s** for librespot. Expect roughly **6–10 minutes** for the
first Publish run, and much less afterwards thanks to the GHA layer cache.
Only the final stage's `apt-get install lame libconfig11 libpopt0` and
`pip install` run under emulation for arm64, as `pip install` did before.
I didn't run the Publish workflow myself (it pushes to ghcr.io), so those
numbers are an estimate: please note the real time when you run it.

## 2. Point Docker Compose or Umbrel at it

**Docker Compose.** In `docker-compose.yml`, replace `build: .` and the
`image:` line with:

```yaml
    image: ghcr.io/msalty/dell-rio-server:claude-airplay-spotify-connect
```

then `docker compose pull && docker compose up -d`. To go back, restore the
lines and `docker compose up -d --build`.

**Umbrel.** The community store app pins its image in the app's compose
file. Over SSH (`ssh umbrel@umbrel.local`):

1. Find the app's folder: `ls ~/umbrel/app-data/`. It is named after the
   app's id in the msalty store, for example `msalty-rioserver-ng`.
2. Edit `~/umbrel/app-data/<app-id>/docker-compose.yml` and change the
   `image:` of the server service to
   `ghcr.io/msalty/dell-rio-server:claude-airplay-spotify-connect`. Drop any
   `@sha256:…` digest after it.
3. Restart the app: in umbrelOS, right-click the app › Restart, or
   `umbreld client apps.restart.mutate --appId <app-id>`.

An app update from the store puts the store's compose file back, and with it
the normal image. I couldn't try these Umbrel steps here, so check the
folder name and the restart command on your box. The data volume stays as
it is, and the branch only adds one file there, `speakers.json`.

The image tag is mutable: run the workflow again after new commits on the
branch, then pull again (Compose: `docker compose pull`; Umbrel: restart the
app after `docker pull` of the tag, or use the `sha-` tag in the compose
file to pin a build).

## 3. Turn it on

1. **Power-cycle each receiver** so it loads firmware 1.8. Settings › About
   on the receiver should say `v1.8`, and the web interface's card says
   "RioServer NG firmware 1.8".
2. Under **Receivers**, each NG receiver's card now has **AirPlay** and
   **Spotify Connect** switches, both off. Switch them on. Nothing extra
   runs until you do, and the switches are kept in `/data/speakers.json`.
   If the receiver still runs older firmware, the card says it needs a
   power cycle, and no speaker is started for it.
3. The speakers are named after the receiver. Give it a name first (pencil
   icon), for example "Kitchen". Renaming restarts its speakers with the
   new name.
4. `RIO_AIRPLAY=0` / `RIO_SPOTIFY=0` remove the feature entirely.
   `RIO_LIVE_BITRATE` (default 160) sets the stream bitrate.
   `RIO_AIRPLAY_PORT_BASE` (5100) and `RIO_SPOTIFY_PORT_BASE` (5300) move the
   ports.
5. For detail in the server log, `RIO_LOG_LEVEL=DEBUG` shows each poll
   (with the receiver's `cpu`, `gaps`, `under` and `lbuf`) and
   shairport-sync's and librespot's own output.
6. **Reading "ran out of music" during a session.** The warning now says
   how much of the stream the receiver held. Next to it, the server logs
   what happened upstream: `the AirPlay source stalled …` (the phone or its
   Wi-Fi; filled with silence, so it no longer starves the receiver),
   `… ran … ahead; skipped it` (the source caught up in a burst), and `the
   server held up the … stream` (the server was busy or short of CPU). If
   the receiver still runs out with none of those around it, the likely
   cause is the network to the receiver, or its audio clock running faster
   than the server's (then the runs-out come at regular intervals, and
   `lbuf` in the debug log drifts down).

## 4. Checklist on real hardware

From the plan, plus what I couldn't check here:

- [ ] **Speakers show up.** On an iPhone (Control Centre › AirPlay, or the
      AirPlay button in Music) the receiver's name is listed. In the Spotify
      app's device picker (Premium account) it is listed too.
- [ ] **They connect and play.** Picking it starts music on the receiver
      within a few seconds; Now Playing shows "AirPlay · <phone name>" (or
      "Spotify · …") with the title, artist and album, and changes with the
      next track.
- [ ] **No runs-out during a long session** (the first test on hardware
      ran out of data after about 7 minutes and then stuttered for ~15 s;
      the server now fills source stalls in and the receiver keeps 1.6 s in
      hand). Note any `stalled`, `held up` or `ran out` lines in the log.
- [ ] **CPU and dropouts at 160 kbps.** Let it play for 10+ minutes. The
      server log warns about dropouts (`audio dropped out`), running out of
      data (`ran out of music`) and CPU ≥ 95%. With `RIO_LOG_LEVEL=DEBUG`
      the polls show `cpu=`, `gaps=` and `under=`. Expect CPU similar to a
      160 kbps file. If it struggles, try `RIO_LIVE_BITRATE=128`.
- [ ] **Delay from phone to sound.** Roughly: AirPlay's own ~2 s plus ~1 s
      on the receiver. Spotify is usually lower. Tap pause on the phone and
      time it.
- [ ] **Volume from the phone** moves the receiver's volume (the card's
      slider follows). The receiver's own volume keys still work (they don't
      reach the phone).
- [ ] **Switching.** Spotify takes over from AirPlay on the same receiver
      and the other way round (the older phone is disconnected). Picking
      music on the receiver, or from the web interface, ends the session
      and disconnects the phone. Stop or Play/Pause on the receiver ends it
      too. After a session, **Resume Up Next** on the card plays the old
      queue from where it was.
- [ ] **Phone stops or picks another speaker:** the receiver stops and its
      display clears.
- [ ] **Pausing on the phone** keeps the session; the receiver plays
      silence (Now Playing keeps the title) and the card says Paused. After
      10 minutes paused the session ends and the receiver stops.
- [ ] **The host's name still resolves only to its LAN address** while the
      speakers run. From a Mac: `dns-sd -G v4v6 umbrel.local` (or the
      host's name). From Linux: `avahi-resolve -n umbrel.local`. There
      should be no `172.17.x.x` or other Docker bridge address. Also check
      `dns-sd -B _raop._tcp` and `dns-sd -B _spotify-connect._tcp` list the
      speakers, and `dns-sd -G v4 rioserver.local` gives only the LAN
      address.
- [ ] **Two receivers at once**, each with its own speakers, playing from
      two phones.
- [ ] **Another AirPlay receiver on the same host** (if you have one, say
      shairport-sync installed on Umbrel) still works; ours use 5100+ and
      5300+, not 5000/7000.
- [ ] **A browser receiver as a speaker.** On a tablet or old phone, start
      "Use this device as a receiver", switch AirPlay (or Spotify) on on its
      card, and play to it from another phone. Its display shows the song and
      cover, and its lock-screen / media controls show them too. Close the
      page: within about a minute the speaker disappears from the phone's
      list. On an iPhone or iPad used as the receiver, the sending phone's
      volume can't change the page's volume (a Safari restriction).
- [ ] Turning both switches off stops the processes (`docker top <container>`
      no longer lists shairport-sync or librespot).

## 5. What was verified here, and what wasn't

Verified in this environment:

- `make -C firmware` and the full `python -m pytest` in `server/`
  (114 tests, including the qemu firmware tests) pass locally.
- The live stream (`server/tests/test_live.py`): a generated tone through
  LAME and ffmpeg to several listeners, a stuck listener dropped without
  holding up the others, pacing of a source that writes ahead, silence while
  quiet, the encoder only running during a session, metadata callbacks.
- Sessions with fake shairport-sync and librespot processes
  (`server/tests/test_speakers.py`, `tests/fake_speaker.py`): speakers named
  after the receiver, the AirPlay advertisement (name, TXT, port,
  `rioserver.local`, LAN address only), librespot's arguments
  (`--zeroconf-interface`, fixed port), rename and switch-off, no speakers
  for firmware < 1.8, receiver switched to live with metadata and volume,
  queue saved and resumed, newest sender wins, local and web playback ending
  the session, Stop/pause from the web interface, no play-history counting.
- The firmware under qemu (`test_airplay_plays_live_on_the_receiver`):
  plays an AirPlay session for well past its buffer, shows the metadata,
  reconnects after the connection is cut, stops on Stop and when the phone
  stops. Screenshot of Now Playing checked by eye.
- **The real shairport-sync 5.6.2** (built as in the Dockerfile) run by the
  server, with pyatv as an AirPlay sender: the advertisement went through
  the `avahi-publish-service` stand-in to the server's Zeroconf, the
  session started, `/live/<id>` served a 160 kbps 44.1 kHz stereo MP3 with
  the tone at full level (`ignore_volume_control` works), and the session
  ended when the sender stopped. pyatv sends no track metadata, so titles
  were only checked with the fake.
- **The real librespot 0.8.0** run by the server: it starts with the
  server's arguments and answers Spotify's zeroconf `getInfo` on its port
  (`accountReq: PREMIUM`, `deviceType: Speaker`). No Spotify account here,
  so no real Spotify session.
- The web interface's receiver card, player bar and full-screen Now Playing
  during a session, at phone width (Playwright screenshots).
- **Browser receivers as speakers:** server tests drive a page over its
  WebSocket (the `live` and `meta` messages, confirming, pause ending it,
  Resume Up Next, a page that gives up), and a headless Chromium in receiver
  mode played an AirPlay session from the fake shairport-sync in real time
  through its `<audio>` element and showed it on its display.
- **Source stalls:** a test stalls the source for 0.8 s and checks listeners
  still get exactly real time (the previous pacer came up 0.4 s short), and
  the qemu test stalls the fake AirPlay source for 1 s without the receiver
  running out of data.
- The Dockerfile's cross-compile commands for **arm64**, run on this
  (Ubuntu) machine with the same pinned commits and flags: both binaries
  build, and run under `qemu-aarch64`.
- CI: the amd64 Docker build of the real Dockerfile (Debian trixie stages)
  passes, and a new CI step runs `shairport-sync -V`, `librespot --version`
  and `lame --version` inside the built image.

Not verified:

- Anything with a real iPhone, Mac, Spotify app or real receiver (the
  checklist above).
- The **arm64 image** itself. Debian's package mirrors are blocked in this
  sandbox, so the Debian-based build stages only ran on CI, and CI builds
  amd64 only. The arm64 commands were checked on Ubuntu (see above).
  The first Publish run is the real test.
- The Umbrel steps in section 2.
- How mDNS from python-zeroconf and librespot's libmdns behaves next to
  Umbrel's avahi on a real LAN. The plan's investigation covered it; I
  followed it as written.

## 6. Changes from the plan, unfinished work, and what I'd do differently

Decisions that differ from the plan, and why:

- **Debian trixie, not bookworm.** `python:3.12-slim` is now Debian 13
  (trixie). The final image is pinned to `python:3.12-slim-trixie`, and the
  build stages use trixie (`rust:1.91-slim-trixie`, `debian:trixie-slim`), so
  the binaries match its libraries.
- **shairport-sync with a minimal static FFmpeg.** shairport-sync 5.x
  decodes ALAC through FFmpeg by default. Debian's FFmpeg libraries would add
  well over 100 MB to the image, and the alternative built-in decoders are
  deprecated by upstream for security reasons. So the build compiles FFmpeg
  7.1.5 (pinned commit) with only the ALAC, AAC and PCM decoders and the
  resampler, and links it statically. shairport-sync needs only libconfig,
  libpopt and OpenSSL at run time.
- **librespot is patched** (`docker/librespot-ipv4-fallback.patch`, three
  lines). Its zeroconf login endpoint binds `[::]` only, which fails on
  hosts with IPv6 switched off (this sandbox is one; some NAS setups are
  too). The patch falls back to `0.0.0.0`. librespot is built with rustls
  and libmdns, so it needs no C libraries.
- **LAME** encodes the stream (Debian's `lame` package; ffmpeg is used if
  LAME is missing, which is how CI ran until I added `lame` to it).
- **Ports.** AirPlay uses TCP 5100 + 16·n with UDP 5101–5110 (+16·n), and
  Spotify uses 5300 + n, where n is a slot given to each receiver when its
  first switch goes on. Both bases are configurable.
- **Disconnecting a phone** (newest sender wins, Stop on the receiver, local
  playback) is done by restarting that receiver's shairport-sync or
  librespot. Neither offers a remote "stop" without D-Bus or MQTT. The
  speaker disappears from mDNS for a second or two and comes back.
- **Pause on the phone** keeps the session and the receiver plays silence,
  so its buffer stays steady and resuming is instant. After 10 minutes
  paused the session ends. Pause on the receiver ends the session, as the
  plan allows for v1.
- **Speakers are withdrawn** when a receiver hasn't polled for 5 minutes
  (unplugged), and come back when it polls again.

Unfinished or left for later:

- **AirPlay 2** (and multi-room from the Home app). It needs a shairport-sync
  build `--with-airplay-2`, `nqptp` on UDP 319/320 (only one per host,
  which clashes with any other AirPlay 2 receiver on the machine, and needs
  those privileged ports under host networking), and one
  `nqptp_shared_memory_interface_name` per instance. The 5.6.2 sample config
  documents that, so the build stage is the place to start.
- **Pause/next/previous from the receiver back to the phone** (DACP for
  AirPlay; librespot has no hook). The receiver's volume keys don't reach
  the phone either, and neither does the web interface's volume slider.
- **Resume Up Next** is kept in memory only: a server restart forgets it.
- **One mDNS host name** (`rioserver.local`) for the AirPlay records. Two
  RioServers on one LAN would both claim it; if that matters, add a suffix
  from the server's ID.
- **librespot events start a Python process each** (the `rio-hook` program).
  It's quick enough (tens of ms) and runs only on track changes, volume and
  play/pause, but a tiny shell or C helper would be lighter on a Pi.
- Cover art: AirPlay's comes from the phone and the web interface shows it;
  Spotify's is a URL to Spotify's CDN, which the browser loads directly.
  The receiver itself has no art.
- The receiver keeps 1.6 s of a live stream in hand (and the server 0.4 s
  of jitter buffer), raised from 0.8 s after the first test on hardware ran
  out of data. That adds about a second to the delay. If runs-out turn out to
  come from the receiver's audio clock drifting against the server's, the
  next step is for the server to follow `lbuf` and stretch or squeeze the
  stream very slightly (as shairport-sync does for its own output).
