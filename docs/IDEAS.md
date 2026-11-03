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
