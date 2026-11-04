# Ideas for later

Things discussed and designed but not built yet, with enough detail to pick
them up again.

## Let a tablet receiver's screen turn off while it waits

**The problem.** A phone or tablet used as a receiver ("Use this device as a
receiver") has to keep its screen on. If the screen turns off, the browser
soon freezes the page, so the receiver drops offline and can't be woken by
music sent to it.

**The idea: Fully Kiosk Browser on Fire and Android tablets.** Fully is an
Android app for wall-mounted tablets. It keeps a web page running with the
screen off, and its JavaScript interface lets the page switch the screen with
`fully.turnScreenOn()` and `fully.turnScreenOff()`.

How the receiver page would use it (in `server/rioserver/web/app.js`, the
`RX` object):

1. **Music starts.** The server already sends a `load` (or `resume`) message
   over the receiver's WebSocket. When `window.fully` exists, the page also
   calls `fully.turnScreenOn()`, so the screen lights up with Now Playing.
2. **Nothing plays for a while** (paused, stopped or the end of the queue;
   about 5 minutes): the page calls `fully.turnScreenOff()`. The page keeps
   running and stays connected.
3. **Inside Fully, skip the page's own screen wake lock**
   (`RX.keepAwake()`), so it doesn't fight Fully over the screen.

Setup in Fully, and things to check first:

- Turn on the JavaScript interface (Advanced Web Settings). It may need
  Fully's paid Plus licence.
- Keep the page running with the screen off; Fully's own screen-off timer off
  or longer than the receiver's delay.
- Exclude Fully from battery optimisation in Android / Fire OS settings.
  Otherwise the system may pause Wi-Fi after a long time dark; the page then
  reconnects only when the screen next wakes.

**If the JavaScript interface needs the Plus licence:** Fully also has a
remote-control web address on the tablet (its REST interface). The server
could call it to turn the screen on when it starts music there, at the cost
of storing the tablet's address and Fully's password on the server.

**Not possible on iPhone and iPad:** iOS freezes web pages when the screen
locks, and nothing can wake them to start audio.

**Considered and set aside: a black idle screen.** After a few minutes with
nothing playing, the receiver display fades to black (or a dim clock) while
the page stays open. It works on every device, but on LCD screens (iPhone 8,
Fire tablets) the backlight stays on, so it's dark rather than off. Not
judged worth building.

## Send music to DLNA / UPnP renderers

Many devices can already play music sent over DLNA: Kodi, Android receiver
apps (BubbleUPnP and others), many smart TVs and network AV receivers. The
server could send to them the same way it sends to Chromecasts: find them
over SSDP, then drive them with AVTransport (SetAVTransportURI, Play, Pause,
Seek, SetNextAVTransportURI for gapless where supported) and
RenderingControl (volume), and poll their position. That would be a new
QueuePlayer subclass, like `cast.py`; a Python library such as
async_upnp_client could do most of the protocol work.

It would reach devices a browser receiver can't (no screen needed, playback
survives the device sleeping, AV gear and TVs), but not iPhones or iPads.
Renderers differ in the details, gapless especially, so it needs testing
device by device.

## Download albums for offline play on phones (an opt-in setting)

**The idea.** On a phone, download albums and playlists from the web
interface so they play with no connection: in the car, at the gym, on a
flight, or when a remote connection (for example over Tailscale) drops.
Only worth it for people who play this library away from home; at home,
streaming already works.

**How it would work** (in `server/rioserver/web/app.js` and
`server/rioserver/web/sw.js`, no server changes needed for the core):

1. **A per-device toggle in Settings**, kept in the browser like the theme.
   Off by default; when off there are no download buttons and `sw.js`
   behaves exactly as today. Turning it off offers to delete the downloads.
2. **"Download album / playlist"** fetches each `/api/stream/{id}` (plus the
   album JSON and cover art) into the Cache API, with progress. Call
   `navigator.storage.persist()` so the browser doesn't clear it when space
   runs low.
3. **Cache-first streaming in `sw.js`** for `/api/stream/*`. `<audio>` asks for
   byte ranges (`Range:`) to seek, and Safari does even for normal playback,
   so the service worker must slice the cached file and answer with a
   `206 Partial Content`. Without that, seeking breaks and Safari may refuse
   to play.
4. **A "Downloaded" view** that opens without the server, with the
   server-only features (receivers, Chromecast, search, handoff) hidden
   when it can't be reached.

**Rough size.** Steps 1–3 are about 250–350 lines of JS. Step 4 is the
expensive part, because every view currently assumes the server answers
`api()`; a simple Downloaded view gets most of the value without making the
whole app work offline.

**Later, if it matters:** keep plays made offline on the phone and send them
when it reconnects, so Top 25 and Recently Played count them; re-download
files whose tags changed; show storage used.

**Things to check first:**

- **HTTPS is required.** Browsers only run service workers over HTTPS or on
  `localhost`, so over plain `http://<server>:8080/` the option has to be
  greyed out with a note saying why.
- **iPhone and iPad:** the app must be added to the Home Screen; in a Safari
  tab, stored data can be cleared after about 7 days without a visit, and
  the storage allowance is smaller than on Android. Needs testing on a real
  device.
- An album is typically 50–150 MB, so dozens of albums fit, not a whole
  library.

## Back up and restore the data volume

Everything made in the app lives in the `/data` volume, apart from the
music: playlists, play counts and history, podcast subscriptions and
listening positions, receiver names, alarms and settings. Nothing backs it up,
so losing the volume (reinstalling Docker or Umbrel, moving to a new host)
loses all of it, even though the music is untouched.

**The idea.** In Settings:

- **Download backup:** a zip of the database and settings, made with
  SQLite's online backup so it's consistent while the server runs.
  Downloaded podcast episodes and firmware images are left out (they can be
  fetched or copied again), which keeps the file small.
- **Restore:** upload a backup, check that it's one of ours and of a schema
  version we can read, swap it in and restart the affected services.
- **Export as .m3u** on each playlist, so playlists can also live in the music
  folder or go to another player.

Small, mostly server-side. The cheapest protection for the things that took
effort to build up.

## Even out loudness between songs (ReplayGain)

A 1980s CD rip and a 2010s release can differ a lot in loudness, which shows
most in shuffle and Top 25. Nothing adjusts volume per track today.

**The idea:**

1. **Server:** read `REPLAYGAIN_TRACK_GAIN` / `REPLAYGAIN_ALBUM_GAIN` tags
   (ID3v2 TXXX frames) during the scan. For files without them, optionally
   measure loudness in the background (ffmpeg's `ebur128` filter) and store
   the result in the database. Return the gain with each track. Album gain
   when a whole album plays in order, track gain in shuffle.
2. **Browser player:** apply it with a Web Audio gain node (`audio.volume`
   can't go above 1, and iOS ignores it).
3. **Chromecast:** scale the volume sent with each track, or leave it out.
4. **Receiver firmware:** scale the decoder's output per track. Needs care
   to avoid clipping when a gain is positive; test on a real receiver.

Worth it for libraries that mix eras or sources. Steps 1–2 can ship without
the firmware change.

## Play FLAC, AAC and other formats

Only `.mp3` files are indexed, because that's all the receiver hardware can
decode, so FLAC, M4A, Ogg and other files in the music folder don't show up.

**The idea.** Index them too (mutagen reads their tags), and convert each one
to MP3 in the background with ffmpeg into a cache in `/data`. Every player
then gets an ordinary MP3 with a known size, so seeking and the Rio
protocol's byte ranges work unchanged. Browsers and Chromecasts could get
the original file instead, as they can play it.

Costs: ffmpeg in the Docker image, disk space for the cache (roughly 1 MB a
minute at 128 kbit/s), and a first conversion that takes a while on a big
library. A track can't play until its MP3 is ready, so convert newly added
files first. Only worth it if the library isn't all MP3.

## Shuffle Albums

The classic iPod mode: whole albums, each played in track order, with the
albums in random order. It fits a library that's browsed by album.

The server builds the queue (shuffle the album list, then append each album's
tracks); the receiver firmware gets a "Shuffle Albums" entry next to Shuffle
Songs, and the web interface a button on the Albums page. Small.

## An optional password for the web interface

The web interface has no authentication, which is fine on a trusted home
network. If it's ever reached through a reverse proxy or a port forward
(rather than Tailscale, which already controls who can connect), it needs a
password.

**The idea.** An optional `RIO_WEB_PASSWORD`. When set, the web interface and
its API ask for it once and keep a long-lived session cookie, and the stream
and art URLs (which `<audio>` and `<img>` load without custom headers) are
covered by the same cookie. Chromecasts fetch streams themselves, so they'd
need signed, expiring stream URLs. The receivers' protocol stays open, as
the hardware can't authenticate. Small to medium. Not needed while the
server stays on the home network.

## Receivers as AirPlay and Spotify Connect speakers

Planned in detail, as a prompt ready to start an implementation session:
[`docs/plans/airplay-spotify-connect.md`](plans/airplay-spotify-connect.md).
The server runs shairport-sync and librespot for each receiver, encodes what
they receive as a live MP3 stream, and the firmware gets a live-stream mode.

## Internet radio

Add stations by URL, or search a public directory such as
radio-browser.info. The server relays each stream as plain HTTP MP3, since
the receivers can't fetch HTTPS (the same reason podcasts are downloaded),
and converts streams that aren't MP3 (AAC, Ogg) with ffmpeg. A Radio menu on
the receiver sits next to Podcasts, and the web interface lists stations
with their logos.

Builds on the AirPlay plan's groundwork: the firmware's live-stream mode
(no size or duration, reconnect from scratch) and the server's live MP3
source. With those in place this is mostly a station list, the relay, and
the station's current song (ICY `StreamTitle`) shown on the display. Medium
on its own, small after the AirPlay work.

## Home Assistant integration and announcements

Show each receiver (and speaker) in Home Assistant as a media player, via
MQTT discovery, so automations can drive them: pause when the doorbell
rings, start a playlist on arriving home, play quietly at bedtime.

**Announcements** are the most useful part: an API call (and Home Assistant
service) that lowers or pauses the music on chosen receivers, plays a chime
or a spoken message (Home Assistant's text-to-speech gives an MP3 URL),
then carries on where the music was. That needs the server to interrupt a
queue and resume it, much like moving playback between rooms already does.
Medium. MQTT means a broker setting (`RIO_MQTT_URL`) and the paho-mqtt
library.

## A clock and information screen when idle

The receiver's 128×64 display sits idle when nothing plays. It could show a
large clock, the next alarm, and optionally the weather or today's first
calendar event, sent by the server. A receiver on a nightstand becomes a
clock radio, which suits the alarms already there.

Mostly firmware drawing, with the time and data coming from the server in
the poll reply (the receiver has no clock it can trust). Keep it dim or
switch the backlight off at night (display ioctl 11). Weather needs a
location and a free source such as Open-Meteo, so make it optional. Small to
medium.

## Party mode: guests add songs with a QR code

Show a QR code (on the web interface, or printed) that opens a cut-down page
for one receiver: guests search the library and add songs to its Up Next,
but can't skip, clear the queue, change the volume or reach other
receivers. Optionally limit how many songs each guest can have queued, and
show who added what.

Needs a separate, limited guest link (a random token in the URL) so the
full web interface stays private. Small: Up Next and its API already exist.

## Smart playlists from listening history

Plays are already recorded on every device, so add playlists built from
them, next to Top 25 and Recently Played on the receivers and in the web
interface:

- **Forgotten favourites:** played often, but not in the last year.
- **Never played:** songs with no plays, shuffled.
- **On this day:** what was played on this date in earlier years.
- **Recently added, not yet played.**

Small: SQL queries in the server, plus menu entries.

## A year in review

A stats page for each year: top artists, albums and songs, hours listened,
the busiest day and hour, which room plays most, new albums that got the
most plays. A fun use of history the server already keeps, and a natural
place to start a "Top songs of 2026" playlist from. Small.

## Audiobooks

Treat an `Audiobooks` folder in the music library like podcasts: each book
resumes where it was left on any device, a book's files play in order as
one item, chapter markers (from files or ID3 CHAP frames) give previous and
next chapter, and the sleep timer can stop at the end of a chapter. An
Audiobooks menu on the receiver next to Podcasts. Much of the
resume-anywhere machinery exists for podcast episodes already. Small to
medium.

## Per-receiver limits

Settings for each receiver: a maximum volume, quiet hours (a lower cap, or
no alarms and remote starts), and optionally restricting it to chosen
playlists or genres, for a child's room or a bedroom. Enforced by the server
for commands from the web interface, and by the firmware for its own
volume keys and menus (sent with the poll reply). Small.

## Considered and set aside

- **Synchronised multi-room playback across Rio receivers.** The receivers
  have no shared clock, so the same song started on several of them drifts
  apart audibly within minutes. Google speaker groups already cover
  in-sync playback.
- **Scrobbling to Last.fm or ListenBrainz.** Easy, since plays are already
  counted on every device, but only useful to someone who already uses one of
  those services.
