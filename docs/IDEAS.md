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

**Free alternatives to Fully** (checked November 2026, from each app's own
documentation; none tried on a device yet). What we need: the receiver page
keeps running and connected with the screen off; the page, or our server, can
turn the screen on and off; it runs on Fire tablets; no licence fee.

- **[Kiosk Satellite](https://github.com/jxlarrea/kiosk-satellite):
  closest to Fully.** It gives every page a `window.kioskSatellite` object
  (detect it with `window.kioskSatellite.platform === "kiosksatellite"`) with
  `screenOn()`, `screenOff()` and `isScreenOn()`, and fires
  `kiosksatellite:screenon` / `kiosksatellite:screenoff` events
  ([JavaScript API](https://github.com/jxlarrea/kiosk-satellite/blob/main/docs/js-api.md)).
  The receiver page would use it as described for Fully above. Free for
  personal, non-commercial use (CC BY-NC-ND 4.0, so not open source).
  Android 7+, and it has a setup guide for Amazon Fire tablets.
  - `screenOff()` needs the "device admin" permission.
  - It's built around Home Assistant (it's also a voice satellite); check that
    it's happy showing our page on its own.
  - Its docs say voice and the Home Assistant connection keep working with the
    screen off, but not whether the web page keeps running. Test that first:
    start music, let the screen go off, then send music again and see that it
    wakes.
- **[FreeKiosk](https://github.com/RushB-fr/freekiosk): the open-source
  fallback** (MIT, free; an optional paid cloud service). Screen control is a
  REST API on the tablet, `GET`/`POST` `/api/screen/on` and `/api/screen/off`,
  with an optional API key
  ([REST API](https://github.com/RushB-fr/freekiosk/blob/main/docs/rest-api.md)).
  Its web server stays reachable with the screen off.
  - It truly turns the screen off only with Device Owner, Device Admin or
    Accessibility permission; without them, "off" dims to black.
  - No documented way for the page itself to switch the screen, so our server
    would call the tablet (storing its address, and the API key if one is
    set), as in the Fully REST fallback above.
  - Android 8+; Fire tablets aren't mentioned.
- **Also looked at, less suitable:**
  - [Webview Kiosk](https://github.com/nktnet1/webview-kiosk) (AGPL, free):
    can wake the screen from MQTT commands, which we don't use; no confirmed
    screen-off command.
  - [Open Kiosk](https://github.com/shoshins/open-kiosk) (Apache, free): very
    new; its screensaver darkens rather than turns the screen off, and no
    screen-off command is documented.
  - [WallPanel](https://github.com/thanksmister/wallpanel-android) (Apache,
    free): older, with a remote "wake" command; showing its age, screen-off not
    documented.
  - [KioskZen](https://github.com/exraaaa/KioskZen): no longer developed.

**Plan when picking this up:** try Kiosk Satellite on one Fire tablet and
check the receiver stays connected with the screen off. If it does, support
it like Fully (detect `window.kioskSatellite`, screen on when music arrives,
off after idle, skip `RX.keepAwake()`). If it doesn't, use FreeKiosk with the
server switching the screen.

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
