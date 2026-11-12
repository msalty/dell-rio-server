// RioAmp: RioServer as a 1990s desktop player. It really kicks the alpaca's
// butt.
//
// While it is on it is the whole interface. On top is the classic 275x116
// player window, drawn from a skin's sprite sheets exactly where the classic
// layout puts them, so the .wsz skins made for the player that inspired it
// work here. Under it, in the classic playlist window's frame, is everything
// else: where the music goes, the library, and Up Next. On a phone the two
// fill the screen; on a computer they are life-size or twice that, and an
// installed app's window shrinks to fit around them.
//
// A skin is a zip of bitmaps; one is read in this file (the browser decodes
// the bitmaps), kept in this browser, and anything it leaves out comes from
// the built-in skin, which is drawn by code below in RioServer's colours.
//
// Everything works wherever the music is playing (this browser, a receiver
// or a speaker): it only uses what the standard player uses, and app.js's
// Eq for the equaliser and balance, which this browser applies itself and
// the server applies for the others. That is also why the spectrum is an
// animation and not a measurement.
//
// Loaded by app.js only when RioAmp is switched on in Settings.
"use strict";

window.RioAmp = (() => {
  const W = 275, H = 116;
  const SHEETS = ["main", "titlebar", "cbuttons", "numbers", "nums_ex", "text", "posbar", "volume", "balance",
    "shufrep", "playpaus", "monoster", "pledit", "eqmain"];

  // ------------------------------------------------------------ reading a skin (.wsz is a zip)

  const LBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258];
  const LEXT = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0];
  const DBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073,
    4097, 6145, 8193, 12289, 16385, 24577];
  const DEXT = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13];
  const CLEN_ORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15];

  function huffman(lens) {
    const count = new Uint16Array(16), offs = new Uint16Array(16), sym = new Uint16Array(lens.length);
    for (const l of lens) count[l]++;
    count[0] = 0;
    for (let i = 1; i < 16; i++) offs[i] = offs[i - 1] + count[i - 1];
    for (let i = 0; i < lens.length; i++) if (lens[i]) sym[offs[lens[i]]++] = i;
    return { count, sym };
  }

  // Raw DEFLATE (RFC 1951), as zip files hold it.
  function inflate(src, size) {
    const out = new Uint8Array(size);
    let ip = 0, op = 0, buf = 0, cnt = 0;
    const bits = (n) => {
      while (cnt < n) {
        if (ip >= src.length) throw new Error("cut short");
        buf |= src[ip++] << cnt;
        cnt += 8;
      }
      const v = buf & ((1 << n) - 1);
      buf >>>= n;
      cnt -= n;
      return v;
    };
    const decode = (hf) => {
      let code = 0, first = 0, index = 0;
      for (let len = 1; len < 16; len++) {
        code |= bits(1);
        const c = hf.count[len];
        if (code - c < first) return hf.sym[index + (code - first)];
        index += c;
        first = (first + c) << 1;
        code <<= 1;
      }
      throw new Error("bad code");
    };
    let fixed = null;
    for (let last = 0; !last;) {
      last = bits(1);
      const type = bits(2);
      if (type === 0) {
        buf = 0; cnt = 0;
        const len = src[ip] | (src[ip + 1] << 8);
        ip += 4;
        if (ip + len > src.length || op + len > size) throw new Error("bad block");
        out.set(src.subarray(ip, ip + len), op);
        ip += len; op += len;
        continue;
      }
      if (type === 3) throw new Error("bad block");
      let lit, dist;
      if (type === 1) {
        if (!fixed) {
          const l = new Uint8Array(288);
          l.fill(8, 0, 144); l.fill(9, 144, 256); l.fill(7, 256, 280); l.fill(8, 280, 288);
          fixed = [huffman(l), huffman(new Uint8Array(30).fill(5))];
        }
        [lit, dist] = fixed;
      } else {
        const nlit = bits(5) + 257, ndist = bits(5) + 1, nclen = bits(4) + 4;
        const cl = new Uint8Array(19);
        for (let i = 0; i < nclen; i++) cl[CLEN_ORDER[i]] = bits(3);
        const ch = huffman(cl);
        const lens = new Uint8Array(nlit + ndist);
        for (let i = 0; i < lens.length;) {
          const s = decode(ch);
          if (s < 16) { lens[i++] = s; continue; }
          let prev = 0, rep;
          if (s === 16) {
            if (!i) throw new Error("bad lengths");
            prev = lens[i - 1]; rep = 3 + bits(2);
          } else if (s === 17) rep = 3 + bits(3);
          else rep = 11 + bits(7);
          if (i + rep > lens.length) throw new Error("bad lengths");
          while (rep--) lens[i++] = prev;
        }
        lit = huffman(lens.subarray(0, nlit));
        dist = huffman(lens.subarray(nlit));
      }
      for (;;) {
        const s = decode(lit);
        if (s === 256) break;
        if (s < 256) {
          if (op >= size) throw new Error("too long");
          out[op++] = s;
          continue;
        }
        if (s > 285) throw new Error("bad length");
        const len = LBASE[s - 257] + bits(LEXT[s - 257]);
        const ds = decode(dist);
        if (ds > 29) throw new Error("bad distance");
        const d = DBASE[ds] + bits(DEXT[ds]);
        if (d > op || op + len > size) throw new Error("bad distance");
        for (let i = 0; i < len; i++, op++) out[op] = out[op - d];
      }
    }
    return out;
  }

  // The files in a zip, by lower-case name without folders: name -> () => bytes.
  function unzip(bytes) {
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    let end = -1;
    for (let i = bytes.length - 22; i >= 0 && i >= bytes.length - 22 - 65535; i--) {
      if (dv.getUint32(i, true) === 0x06054b50) { end = i; break; }
    }
    if (end < 0) throw new Error("That isn't a skin file");
    const files = new Map();
    let p = dv.getUint32(end + 16, true);
    for (let n = dv.getUint16(end + 10, true); n > 0 && p + 46 <= bytes.length; n--) {
      if (dv.getUint32(p, true) !== 0x02014b50) break;
      const method = dv.getUint16(p + 10, true), csize = dv.getUint32(p + 20, true), usize = dv.getUint32(p + 24, true);
      const nameLen = dv.getUint16(p + 28, true), local = dv.getUint32(p + 42, true);
      let name = "";
      for (let i = 0; i < nameLen; i++) name += String.fromCharCode(bytes[p + 46 + i]);
      p += 46 + nameLen + dv.getUint16(p + 30, true) + dv.getUint16(p + 32, true);
      name = name.toLowerCase().split(/[\\/]/).pop();
      if (!name || files.has(name) || usize > 16e6) continue;
      files.set(name, () => {
        const at = local + 30 + dv.getUint16(local + 26, true) + dv.getUint16(local + 28, true);
        const data = bytes.subarray(at, at + csize);
        if (method === 0) return data;
        if (method === 8) return inflate(data, usize);
        throw new Error("unsupported compression");
      });
    }
    return files;
  }

  function decodeImage(bytes, type) {
    return new Promise((ok, bad) => {
      const url = URL.createObjectURL(new Blob([bytes], { type }));
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        const c = canvas(img.naturalWidth, img.naturalHeight);
        c.getContext("2d").drawImage(img, 0, 0);
        ok(c);
      };
      img.onerror = () => { URL.revokeObjectURL(url); bad(new Error("unreadable picture")); };
      img.src = url;
    });
  }

  // A skin from the bytes of a .wsz: its sheets, with the built-in skin's
  // wherever it has none (or one that can't be read).
  async function readSkin(buffer, name) {
    const files = unzip(new Uint8Array(buffer));
    if (!["main.bmp", "main.png"].some((f) => files.has(f))) {
      throw new Error(files.has("skin.xml") ? "That is a “modern” skin; RioAmp wears the classic kind (.wsz)"
        : "That isn't a classic skin (no main.bmp in it)");
    }
    const base = builtinSkin();
    const skin = { name, sheets: {}, vis: base.vis, list: { ...base.list } };
    for (const s of SHEETS) {
      let img = null;
      for (const [ext, type] of [["bmp", "image/bmp"], ["png", "image/png"]]) {
        const get = files.get(`${s}.${ext}`);
        if (!get || img) continue;
        try { img = await decodeImage(get(), type); } catch { img = null; }
      }
      skin.sheets[s] = img;
    }
    if (!skin.sheets.main) throw new Error("That skin's main picture can't be read");
    // What a skin leaves out: its own nearest sheet first, as the classic did.
    const sh = skin.sheets;
    if (!sh.balance && sh.volume) sh.balance = { volumeAsBalance: true, img: sh.volume };
    if (sh.nums_ex) sh.numbers = null;
    else if (!sh.numbers) sh.nums_ex = base.sheets.nums_ex;
    for (const s of SHEETS) if (!sh[s] && s !== "numbers" && s !== "nums_ex") sh[s] = base.sheets[s];
    const vis = files.get("viscolor.txt");
    if (vis) {
      try {
        const rows = new TextDecoder("latin1").decode(vis()).split(/\r?\n/)
          .map((l) => l.match(/(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/)).filter(Boolean)
          .map((m) => `rgb(${m[1]},${m[2]},${m[3]})`);
        if (rows.length >= 24) skin.vis = rows.slice(0, 24);
      } catch { /* the built-in colours */ }
    }
    // the list window's colours
    const pl = files.get("pledit.txt");
    if (pl) {
      try {
        const txt = new TextDecoder("latin1").decode(pl());
        for (const [key, name] of [["normal", "Normal"], ["current", "Current"], ["bg", "NormalBG"], ["sel", "SelectedBG"]]) {
          const m = txt.match(new RegExp(`^\\s*${name}\\s*=\\s*#?([0-9a-f]{6})`, "im"));
          if (m) skin.list[key] = `#${m[1]}`;
        }
      } catch { /* the built-in colours */ }
    }
    return finishSkin(skin);
  }

  // The colours for words the skin's own lettering can't spell (see drawTitle).
  function finishSkin(skin) {
    const t = skin.sheets.text;
    let ink = "#00e000", paper = "#000";
    try {
      const d = t.getContext("2d").getImageData(0, 0, Math.min(155, t.width), Math.min(6, t.height)).data;
      const px = (i) => `rgb(${d[i]},${d[i + 1]},${d[i + 2]})`;
      const counts = new Map();
      for (let i = 0; i < d.length; i += 4) counts.set(px(i), (counts.get(px(i)) || 0) + 1);
      const ranked = [...counts].sort((a, b) => b[1] - a[1]);
      paper = ranked[0][0];
      if (ranked[1]) ink = ranked[1][0];
    } catch { /* the defaults */ }
    skin.ink = ink;
    skin.paper = paper;
    // the equaliser's curve: a colour for each height
    skin.eqLine = Array.from({ length: 19 }, () => ink);
    try {
      const d = skin.sheets.eqmain.getContext("2d").getImageData(115, 294, 1, 19).data;
      skin.eqLine = skin.eqLine.map((_, i) => `rgb(${d[i * 4]},${d[i * 4 + 1]},${d[i * 4 + 2]})`);
    } catch { /* the lettering's colour */ }
    return skin;
  }

  // ------------------------------------------------------------ the built-in skin

  function canvas(w, h) {
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    return c;
  }

  // Lettering, 4x5 in a 5x6 cell (M and W take all five columns). Rows are
  // bit patterns, left bit first.
  const GLYPHS = {
    a: [6, 9, 15, 9, 9], b: [14, 9, 14, 9, 14], c: [7, 8, 8, 8, 7], d: [14, 9, 9, 9, 14],
    e: [15, 8, 14, 8, 15], f: [15, 8, 14, 8, 8], g: [7, 8, 11, 9, 7], h: [9, 9, 15, 9, 9], i: [14, 4, 4, 4, 14],
    j: [3, 1, 1, 9, 6], k: [9, 10, 12, 10, 9], l: [8, 8, 8, 8, 15], m: [17, 27, 21, 17, 17], n: [9, 13, 11, 9, 9],
    o: [6, 9, 9, 9, 6], p: [14, 9, 14, 8, 8], q: [6, 9, 9, 10, 5], r: [14, 9, 14, 10, 9], s: [7, 8, 6, 1, 14],
    t: [14, 4, 4, 4, 4], u: [9, 9, 9, 9, 6], v: [9, 9, 10, 10, 4], w: [17, 17, 21, 27, 17], x: [9, 9, 6, 9, 9],
    y: [10, 10, 4, 4, 4], z: [15, 1, 6, 8, 15],
    0: [6, 11, 13, 9, 6], 1: [4, 12, 4, 4, 14], 2: [14, 1, 6, 8, 15], 3: [14, 1, 6, 1, 14], 4: [9, 9, 15, 1, 1],
    5: [15, 8, 14, 1, 14], 6: [6, 8, 14, 9, 6], 7: [15, 1, 2, 4, 4], 8: [6, 9, 6, 9, 6], 9: [6, 9, 7, 1, 6],
    '"': [10, 10, 0, 0, 0], "@": [6, 9, 11, 8, 7], "…": [0, 0, 0, 0, 10, 0], ".": [0, 0, 0, 0, 4], ":": [0, 4, 0, 4, 0],
    "(": [2, 4, 4, 4, 2], ")": [4, 2, 2, 2, 4], "-": [0, 0, 14, 0, 0], "'": [4, 4, 0, 0, 0], "!": [4, 4, 4, 0, 4],
    _: [0, 0, 0, 0, 0, 15], "+": [0, 4, 14, 4, 0], "\\": [8, 8, 4, 2, 2], "/": [2, 2, 4, 8, 8], "[": [6, 4, 4, 4, 6],
    "]": [6, 2, 2, 2, 6], "^": [4, 10, 0, 0, 0], "&": [4, 10, 4, 10, 5], "%": [9, 2, 4, 9, 0], ",": [0, 0, 0, 4, 4, 8],
    "=": [0, 14, 0, 14, 0], $: [7, 10, 6, 5, 14], "#": [10, 15, 10, 15, 10], "?": [14, 1, 6, 0, 4], "*": [0, 10, 4, 10, 0],
  };
  // Where each character sits in a skin's text sheet: [column, row].
  const FONT = (() => {
    const map = {};
    [..."abcdefghijklmnopqrstuvwxyz\"@"].forEach((c, i) => { map[c] = [i, 0]; });
    map[" "] = [30, 0];
    [..."0123456789….:()-'!_+\\/[]^&%,=$#"].forEach((c, i) => { map[c] = [i, 1]; });
    map["?"] = [3, 2];
    map["*"] = [4, 2];
    return map;
  })();

  function glyph(g, ch, x, y, colour) {
    const rows = GLYPHS[ch];
    if (!rows) return;
    g.fillStyle = colour;
    const top = rows.some((bits) => bits > 15) ? 16 : 8;
    rows.forEach((bits, r) => {
      for (let c = 0; c < 5; c++) if (bits & (top >> c)) g.fillRect(x + c, y + r, 1, 1);
    });
  }
  function word(g, text, x, y, colour) {
    [...text.toLowerCase()].forEach((ch, i) => glyph(g, ch, x + i * 5, y, colour));
  }

  let builtin = null;
  function builtinSkin() {
    if (builtin) return builtin;
    const C = {
      face: "#1b252b", hi: "#37474f", lo: "#0a0e11", edge: "#05080a", lcd: "#060a0c", ink: "#5ef2d0",
      dim: "#2c6f63", off: "#2b3940", label: "#8ea2aa", white: "#e8f0f2",
    };
    const make = (w, h, fill) => {
      const c = canvas(w, h), g = c.getContext("2d");
      g.fillStyle = fill || C.face;
      g.fillRect(0, 0, w, h);
      return [c, g];
    };
    const rect = (g, x, y, w, h, c) => { g.fillStyle = c; g.fillRect(x, y, w, h); };
    // a raised block: lit along the top and left
    const raised = (g, x, y, w, h, face = C.face, hi = C.hi, lo = C.lo) => {
      rect(g, x, y, w, h, lo);
      rect(g, x, y, w - 1, h - 1, hi);
      rect(g, x + 1, y + 1, w - 2, h - 2, face);
    };
    const sunk = (g, x, y, w, h, fill = C.lcd) => raised(g, x, y, w, h, fill, C.lo, C.hi);
    const sheets = {};

    // The window itself.
    {
      const [c, g] = make(W, H);
      rect(g, 0, 0, W, H, C.edge);
      raised(g, 1, 1, W - 2, H - 2);
      sunk(g, 8, 21, 95, 45);    // clutter bar, time and spectrum
      sunk(g, 109, 23, 159, 14); // the song title
      sunk(g, 109, 40, 19, 11);
      word(g, "kbps", 130, 43, C.label);
      sunk(g, 154, 40, 14, 11);
      word(g, "khz", 170, 43, C.label);
      // the mark: RioServer's ring
      const ring = ["..#####..", ".#######.", "###...###", "##.....##", "##..#..##", "##.###.##", "##..#..##", "##.....##",
        "###...###", ".#######.", "..#####.."];
      ring.forEach((row, y) => [...row].forEach((p, x) => { if (p === "#") rect(g, 255 + x, 93 + y, 1, 1, C.ink); }));
      sheets.main = c;
    }

    // Title bar (focused, then not), its four buttons, and the clutter bar.
    {
      const [c, g] = make(344, 87);
      const bar = (y, ink, line) => {
        rect(g, 27, y, W, 14, C.edge);
        raised(g, 28, y + 1, W - 2, 13, "#222e35");
        const tx = 27 + Math.round((W - 29) / 2);
        word(g, "rioamp", tx, y + 4, ink);
        for (const ly of [5, 7, 9]) {
          rect(g, 27 + 20, y + ly, tx - 27 - 26, 1, line);
          rect(g, tx + 35, y + ly, 27 + 238 - tx - 35, 1, line);
        }
      };
      bar(0, C.ink, C.dim);
      bar(15, C.label, C.off);
      const shapes = {
        menu: [[2, 2, 5, 1], [2, 4, 5, 1], [2, 6, 5, 1]],
        min: [[2, 6, 5, 1]],
        shade: [[2, 2, 5, 2]],
        close: [[2, 2, 1, 1], [6, 2, 1, 1], [3, 3, 1, 1], [5, 3, 1, 1], [4, 4, 1, 1], [3, 5, 1, 1], [5, 5, 1, 1], [2, 6, 1, 1], [6, 6, 1, 1]],
      };
      const button = (x, y, shape, down) => {
        raised(g, x, y, 9, 9, down ? "#141b1f" : "#26333a", down ? C.lo : C.hi, down ? C.hi : C.lo);
        for (const [sx, sy, sw, sh] of shapes[shape]) rect(g, x + sx, y + sy, sw, sh, down ? C.ink : C.label);
      };
      button(0, 0, "menu"); button(0, 9, "menu", true);
      button(9, 0, "min"); button(9, 9, "min", true);
      button(0, 18, "shade"); button(9, 18, "shade", true);
      button(18, 0, "close"); button(18, 9, "close", true);
      rect(g, 304, 0, 8, 43, C.lcd);
      [..."oaidv"].forEach((ch, i) => glyph(g, ch, 306, 3 + i * 8, C.dim));
      sheets.titlebar = c;
    }

    // Transport buttons: up along the top, pressed below.
    {
      const [c, g] = make(136, 36);
      const tri = (x, y, dir, col) => { // a 5-wide, 9-tall arrowhead
        for (let i = 0; i < 5; i++) rect(g, dir > 0 ? x + i : x + 4 - i, y + i, 1, 9 - 2 * i, col);
      };
      const icons = {
        prev: (x, y, col) => { rect(g, x + 5, y + 5, 2, 9, col); tri(x + 8, y + 5, -1, col); tri(x + 13, y + 5, -1, col); },
        play: (x, y, col) => { tri(x + 9, y + 5, 1, col); rect(g, x + 9, y + 5, 1, 9, col); },
        pause: (x, y, col) => { rect(g, x + 8, y + 5, 3, 9, col); rect(g, x + 13, y + 5, 3, 9, col); },
        stop: (x, y, col) => rect(g, x + 8, y + 5, 8, 8, col),
        next: (x, y, col) => { tri(x + 5, y + 5, 1, col); tri(x + 10, y + 5, 1, col); rect(g, x + 16, y + 5, 2, 9, col); },
        eject: (x, y, col) => {
          for (let i = 0; i < 5; i++) rect(g, x + 10 - i, y + 3 + i, 1 + 2 * i, 1, col);
          rect(g, x + 6, y + 10, 9, 2, col);
        },
      };
      [["prev", 0, 23, 18], ["play", 23, 23, 18], ["pause", 46, 23, 18], ["stop", 69, 23, 18], ["next", 92, 22, 18],
        ["eject", 114, 22, 16]].forEach(([name, x, w, h]) => {
        raised(g, x, 0, w, h, "#26333a");
        icons[name](x, 0, C.white);
        raised(g, x, h, w, h, "#141b1f", C.lo, C.hi);
        icons[name](x + 1, h + 1, C.ink);
      });
      sheets.cbuttons = c;
    }

    // Shuffle, repeat and the two window buttons: off, off pressed, on, on pressed.
    {
      const [c, g] = make(92, 85);
      const key = (x, y, w, h, label, on, down) => {
        raised(g, x, y, w, h, down ? "#141b1f" : "#26333a", down ? C.lo : C.hi, down ? C.hi : C.lo);
        const o = down ? 1 : 0;
        rect(g, x + 3 + o, y + Math.floor(h / 2) - 1 + o, 3, 3, on ? C.ink : C.lo);
        word(g, label, x + 8 + o, y + Math.floor((h - 5) / 2) + o, on ? C.ink : C.label);
      };
      [[false, false], [false, true], [true, false], [true, true]].forEach(([on, down], i) => {
        key(0, i * 15, 28, 15, "rep", on, down);
        key(28, i * 15, 47, 15, "shuffle", on, down);
        key(down ? 46 : 0, on ? 73 : 61, 23, 12, "eq", on, down);
        key(down ? 69 : 23, on ? 73 : 61, 23, 12, "pl", on, down);
      });
      sheets.shufrep = c;
    }

    // Seek bar and its handle (up, pressed).
    {
      const [c, g] = make(307, 10);
      sunk(g, 0, 2, 248, 6);
      for (const [x, down] of [[248, false], [278, true]]) {
        raised(g, x, 0, 29, 10, down ? "#141b1f" : "#2c3b43", down ? C.lo : C.hi, down ? C.hi : C.lo);
        rect(g, x + 13, 2, 1, 6, C.ink);
        rect(g, x + 15, 2, 1, 6, C.ink);
      }
      sheets.posbar = c;
    }

    // Volume: 28 pictures of the slot filling up, and the handle. Balance has
    // the same handle and a slot that fills from the middle.
    const slider = (w, x0, sw, fill) => {
      const [c, g] = make(w, 433);
      for (let i = 0; i < 28; i++) {
        sunk(g, x0, i * 15 + 3, sw, 7);
        fill(g, x0 + 1, i * 15 + 4, sw - 2, i);
      }
      for (const [x, down] of [[15, false], [0, true]]) {
        raised(g, x, 422, 14, 11, down ? "#141b1f" : "#2c3b43", down ? C.lo : C.hi, down ? C.hi : C.lo);
        rect(g, x + 6, 424, 2, 7, C.ink);
      }
      return c;
    };
    sheets.volume = slider(68, 0, 68, (g, x, y, w, i) => {
      const lit = Math.round((w * i) / 27);
      rect(g, x, y, lit, 5, C.dim);
      rect(g, x, y + 1, lit, 3, C.ink);
    });
    sheets.balance = slider(47, 9, 38, (g, x, y, w, i) => {
      const half = Math.round(((w / 2) * i) / 27);
      rect(g, x + w / 2 - Math.max(1, half), y + 1, Math.max(2, half * 2), 3, C.dim);
    });

    // Mono / stereo lamps: lit above, dark below.
    {
      const [c, g] = make(56, 24);
      for (const [y, col] of [[0, C.ink], [12, C.off]]) {
        word(g, "stereo", 0, y + 3, col);
        word(g, "mono", 32, y + 3, col);
      }
      sheets.monoster = c;
    }

    // Playing / paused / stopped, and the little "working" light beside them.
    {
      const [c, g] = make(42, 9, C.lcd);
      for (let i = 0; i < 4; i++) rect(g, 2 + i, 1 + i, 1, 7 - 2 * i, C.ink);
      rect(g, 11, 1, 2, 7, C.ink); rect(g, 15, 1, 2, 7, C.ink);
      rect(g, 20, 2, 5, 5, C.ink);
      rect(g, 40, 3, 2, 3, C.ink);
      sheets.playpaus = c;
    }

    // The time's digits: seven segments each, then a blank and a minus.
    {
      const [c, g] = make(108, 13, C.lcd);
      const seg = {
        a: [[1, 0, 6, 2]], d: [[1, 11, 6, 2]], g: [[2, 5, 4, 1], [1, 6, 6, 1], [2, 7, 4, 1]],
        f: [[0, 1, 2, 5]], b: [[6, 1, 2, 5]], e: [[0, 7, 2, 5]], c: [[6, 7, 2, 5]],
      };
      ["abcdef", "bc", "abged", "abgcd", "fgbc", "afgcd", "afgedc", "abc", "abcdefg", "abcdfg"].forEach((on, n) => {
        for (const s of "abcdefg") {
          for (const [x, y, w, h] of seg[s]) rect(g, n * 9 + x, y, w, h, on.includes(s) ? C.ink : "#0b1215");
        }
      });
      rect(g, 100, 6, 6, 1, C.ink);
      sheets.nums_ex = c;
      sheets.numbers = null;
    }

    // The lettering sheet.
    {
      const [c, g] = make(155, 18, C.lcd);
      for (const [ch, [col, row]] of Object.entries(FONT)) glyph(g, ch, col * 5, row * 6, C.ink);
      sheets.text = c;
    }

    // The list window's frame, as the classic playlist window has it: title
    // bar (focused, then not), sides, scroll handle and bottom bar.
    {
      const [c, g] = make(280, 186, "#000");
      const cross = [[2, 2], [6, 2], [3, 3], [5, 3], [4, 4], [3, 5], [5, 5], [2, 6], [6, 6]];
      for (const [y, ink, line] of [[0, C.ink, C.dim], [21, C.label, C.off]]) {
        for (const [x, w] of [[0, 25], [26, 100], [127, 25], [153, 25]]) {
          rect(g, x, y, w, 20, "#222e35");
          rect(g, x, y, w, 1, C.edge);
          rect(g, x, y + 1, w, 1, C.hi);
          rect(g, x, y + 19, w, 1, C.lo);
        }
        rect(g, 0, y, 1, 20, C.edge); rect(g, 1, y + 1, 1, 19, C.hi);
        rect(g, 177, y, 1, 20, C.edge); rect(g, 176, y + 1, 1, 19, C.lo);
        word(g, "playlist", 56, y + 8, ink);
        for (const ly of [8, 10, 12]) {
          rect(g, 8, y + ly, 17, 1, line);
          rect(g, 26, y + ly, 25, 1, line); rect(g, 101, y + ly, 25, 1, line);
          rect(g, 127, y + ly, 25, 1, line);
          rect(g, 153, y + ly, 10, 1, line);
        }
        raised(g, 167, y + 5, 9, 9, "#26333a");
        for (const [cx, cy] of cross) rect(g, 167 + cx, y + 5 + cy, 1, 1, C.label);
      }
      rect(g, 0, 42, 12, 29, C.face); rect(g, 0, 42, 1, 29, C.edge); rect(g, 1, 42, 1, 29, C.hi); rect(g, 11, 42, 1, 29, C.lo);
      rect(g, 31, 42, 20, 29, C.face); rect(g, 31, 42, 1, 29, C.hi); rect(g, 49, 42, 1, 29, C.lo); rect(g, 50, 42, 1, 29, C.edge);
      rect(g, 37, 42, 10, 29, C.lcd); rect(g, 37, 42, 1, 29, C.lo); rect(g, 46, 42, 1, 29, C.hi);
      for (const [x, down] of [[52, false], [61, true]]) {
        raised(g, x, 53, 8, 18, down ? "#141b1f" : "#2c3b43", down ? C.lo : C.hi, down ? C.hi : C.lo);
        rect(g, x + 2, 60, 4, 1, C.ink); rect(g, x + 2, 62, 4, 1, C.ink);
      }
      const floor = (x, y, w) => {
        rect(g, x, y, w, 38, C.face); rect(g, x, y, w, 1, C.hi); rect(g, x, y + 36, w, 1, C.lo); rect(g, x, y + 37, w, 1, C.edge);
      };
      floor(179, 0, 25); floor(0, 72, 125); floor(126, 72, 150);
      rect(g, 0, 72, 1, 38, C.edge); rect(g, 1, 73, 1, 36, C.hi);
      rect(g, 275, 72, 1, 38, C.edge); rect(g, 274, 73, 1, 36, C.lo);
      const key = (x, label) => {
        raised(g, x, 80, 22, 18, "#26333a");
        word(g, label, x + (label.length > 3 ? 2 : 4), 86, C.label);
      };
      key(14, "add"); key(43, "rem"); key(72, "sel"); key(101, "misc"); key(232, "list");
      sunk(g, 130, 80, 96, 10);
      sunk(g, 189, 93, 29, 10);
      // the little transport: previous, play, pause, stop, next, open
      const tip = (x, y, dir) => { for (let i = 0; i < 4; i++) rect(g, dir > 0 ? x + i : x + 3 - i, y + i, 1, 7 - 2 * i, C.label); };
      rect(g, 130, 95, 1, 7, C.label); tip(131, 95, -1);
      tip(139, 95, 1);
      rect(g, 148, 95, 2, 7, C.label); rect(g, 151, 95, 2, 7, C.label);
      rect(g, 157, 95, 6, 6, C.label);
      tip(166, 95, 1); rect(g, 170, 95, 1, 7, C.label);
      for (let i = 0; i < 4; i++) rect(g, 177 - i, 95 + i, 1 + 2 * i, 1, C.label);
      rect(g, 174, 100, 7, 2, C.label);
      sheets.pledit = c;
    }

    // The equaliser window: its face, the 28 pictures of a slider's slot,
    // the handle, the title bar (focused, then not) and the buttons.
    {
      const [c, g] = make(W, 315);
      rect(g, 0, 0, W, 116, C.edge);
      raised(g, 1, 1, W - 2, 114);
      sunk(g, 85, 16, 115, 21);
      word(g, "+12db", 44, 39, C.label); word(g, "+0db", 49, 67, C.label); word(g, "-12db", 44, 95, C.label);
      word(g, "preamp", 13, 106, C.label);
      ["60", "170", "310", "600", "1k", "3k", "6k", "12k", "14k", "16k"].forEach((n, i) =>
        word(g, n, 78 + 18 * i + 7 - Math.floor((n.length * 5) / 2), 106, C.label));
      for (const [y, ink, line] of [[134, C.ink, C.dim], [149, C.label, C.off]]) {
        rect(g, 0, y, W, 14, C.edge);
        raised(g, 1, y + 1, W - 2, 13, "#222e35");
        word(g, "equalizer", 115, y + 4, ink);
        for (const ly of [5, 7, 9]) { rect(g, 20, y + ly, 89, 1, line); rect(g, 166, y + ly, 92, 1, line); }
      }
      const cross = [[2, 2], [6, 2], [3, 3], [5, 3], [4, 4], [3, 5], [5, 5], [2, 6], [6, 6]];
      for (const [y, down] of [[116, false], [125, true]]) {
        raised(g, 0, y, 9, 9, down ? "#141b1f" : "#26333a", down ? C.lo : C.hi, down ? C.hi : C.lo);
        for (const [cx, cy] of cross) rect(g, cx, y + cy, 1, 1, down ? C.ink : C.label);
      }
      const key = (x, y, w, label, on, down, lamp) => {
        raised(g, x, y, w, 12, down ? "#141b1f" : "#26333a", down ? C.lo : C.hi, down ? C.hi : C.lo);
        const o = down ? 1 : 0;
        if (lamp) rect(g, x + 4 + o, y + 5 + o, 3, 3, on ? C.ink : C.lo);
        word(g, label, x + (lamp ? 11 : 5) + o, y + 3 + o, on ? C.ink : C.label);
      };
      key(10, 119, 26, "on", false, false, true); key(128, 119, 26, "on", false, true, true);
      key(69, 119, 26, "on", true, false, true); key(187, 119, 26, "on", true, true, true);
      key(224, 164, 44, "presets", false, false); key(224, 176, 44, "presets", false, true);
      for (let n = 0; n < 28; n++) {
        const x = 13 + 15 * (n % 14), y = 164 + 65 * Math.floor(n / 14);
        sunk(g, x + 4, y, 6, 63);
        const level = 6 + Math.round((1 - n / 27) * 50);
        rect(g, x + 5, y + Math.min(level, 31), 4, Math.abs(31 - level) + 1, n > 13 ? C.ink : C.dim);
      }
      for (const [y, down] of [[164, false], [176, true]]) {
        raised(g, 0, y, 11, 11, down ? "#141b1f" : "#2c3b43", down ? C.lo : C.hi, down ? C.hi : C.lo);
        rect(g, 2, y + 5, 7, 1, C.ink);
      }
      rect(g, 0, 294, 113, 19, C.lcd);
      rect(g, 0, 303, 113, 1, "#12202a");
      for (let y = 0; y < 19; y++) rect(g, 115, 294 + y, 1, 1, Math.abs(y - 9) > 5 ? C.white : C.ink);
      rect(g, 0, 314, 113, 1, C.dim);
      sheets.eqmain = c;
    }

    const blend = (a, b, t) => `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * t)).join(",")})`;
    const vis = [C.lcd, "#0d1619"];
    for (let i = 0; i < 16; i++) {
      vis.push(i < 5 ? blend([232, 255, 249], [94, 242, 208], i / 5) : blend([94, 242, 208], [18, 110, 92], (i - 5) / 10));
    }
    for (let i = 0; i < 5; i++) vis.push(C.ink);
    vis.push(C.white);
    const list = { normal: "#4fd1b5", current: "#e8fff9", bg: C.lcd, sel: "#12443b" };
    builtin = finishSkin({ name: "", sheets, vis, list });
    return builtin;
  }

  // ------------------------------------------------------------ keeping the chosen skin in this browser

  function db() {
    return new Promise((ok, bad) => {
      const open = indexedDB.open("rioamp", 1);
      open.onupgradeneeded = () => open.result.createObjectStore("skin");
      open.onsuccess = () => ok(open.result);
      open.onerror = () => bad(open.error);
    });
  }
  async function saved(value) {
    const d = await db();
    try {
      return await new Promise((ok, bad) => {
        const tx = d.transaction("skin", value === undefined ? "readonly" : "readwrite");
        const st = tx.objectStore("skin");
        const req = value === undefined ? st.get("current") : value === null ? st.delete("current") : st.put(value, "current");
        tx.oncomplete = () => ok(req.result);
        tx.onerror = tx.onabort = () => bad(tx.error);
      });
    } finally {
      d.close();
    }
  }

  // ------------------------------------------------------------ the windows

  const LIST_H = 290; // the list window's height on a desk, in skin pixels
  const CATS = [["queue", "Up Next"], ["genres", "Genres"], ["artists", "Artists"], ["playlists", "Playlists"],
    ["albums", "Albums"], ["podcasts", "Podcasts"], ["songs", "Songs"], ["recent", "Recent"]];
  const state = {
    on: false, skin: null, desk: null, room: null, stack: null,
    el: null, base: null, // the main window on screen, and its 275x116 picture
    eq: null,             // the equaliser window: {el, base}
    list: null,           // the list window's parts
    scale: 1,
    press: null,          // {id, inside}: the button under a held pointer
    drag: null,           // {id: "vol" | "pos", v}: a handle being moved, v from 0 to 1
    hold: null,           // {v, until}: where a seek went, until the player reports it
    remaining: store("rio.rioamp.remaining") === "1",
    double: store("rio.rioamp.double") !== "0",
    listShown: store("rio.rioamp.list") !== "0",
    eqShown: store("rio.rioamp.eq") !== "0",
    nav: [], rows: [], loadSeq: 0, queueKey: "", queueMs: 0, targetKey: "", menuRow: null,
    title: "", scroll: 0, scrollAt: 0, overlay: null,
    bars: new Float32Array(19), peaks: new Float32Array(19), visAt: 0,
    timer: null, was: null,
  };

  // Where things are in the main window: [x, y, w, h].
  const AT = {
    menu: [6, 3, 9, 9], close: [264, 3, 9, 9],
    prev: [16, 88, 23, 18], play: [39, 88, 23, 18], pause: [62, 88, 23, 18], stop: [85, 88, 23, 18],
    next: [108, 88, 22, 18], eject: [136, 89, 22, 16],
    shuffle: [164, 89, 47, 15], repeat: [210, 89, 28, 15], eq: [219, 58, 23, 12], pl: [242, 58, 23, 12],
    vol: [107, 57, 68, 13], bal: [177, 57, 38, 13], pos: [16, 72, 248, 10],
    time: [36, 26, 63, 13], vis: [24, 43, 76, 16],
    clutO: [10, 24, 8, 8], clutD: [10, 48, 8, 8], logo: [253, 91, 13, 15],
  };
  const ROOMY = { menu: 3, close: 3 }; // small targets get more room for fingers
  const ORDER = ["menu", "close", "prev", "play", "pause", "stop", "next", "eject", "shuffle", "repeat", "eq", "pl",
    "vol", "bal", "pos", "time", "clutO", "clutD", "logo"];

  function hit(x, y) {
    for (const id of ORDER) {
      const [ax, ay, w, h] = AT[id], r = ROOMY[id] || 0;
      if (x >= ax - r && x < ax + w + r && y >= ay - r && y < ay + h + r) return id;
    }
    return null;
  }

  const clamp = (v) => Math.max(0, Math.min(1, v));
  const volume = () => Number($("#volume").value) / 100;
  const coarse = () => window.matchMedia("(pointer: coarse)").matches;

  // What the windows show right now, from the same state the standard player uses.
  function snapshot() {
    const ns = nowState();
    const { pos, dur } = progressNow();
    const t = ns.t, lv = ns.lv;
    let status = "stop";
    if (ns.playing) status = "play";
    else if (ns.remote ? ns.st && ns.st.st === "pause" && (t || lv) : t && audio.currentTime > 0) status = "pause";
    let title;
    if (t) {
      const n = ns.remote ? (ns.st.qn ? Number(ns.st.qi) + 1 : 0) : Player.index + 1;
      title = `${n ? `${n}. ` : ""}${t.artist ? `${t.artist} - ` : ""}${t.title} (${fmtTime(t.duration_ms)})`;
      if (ns.remote && ns.r) title += ` [${ns.r.name}]`;
    } else if (lv) {
      title = [liveTitle(lv), liveSub(lv), ns.r && ns.r.name].filter(Boolean).join(" - ");
    } else {
      title = ns.remote && ns.sub ? `RioAmp - ${ns.sub}` : "RioAmp - it really kicks the alpaca's butt";
    }
    return { ns, t, lv, pos, dur, status, title, has: !!(t || lv) };
  }

  // Characters the skins' lettering has; the rest of the world's are drawn
  // with the device's own font, in the skin's colours.
  function plain(text) {
    return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
      .replace(/[‘’`´]/g, "'").replace(/[“”«»]/g, '"').replace(/[–—]/g, "-").replace(/[·•]/g, "*")
      .replace(/ß/g, "ss").replace(/æ/g, "ae").replace(/ø/g, "o").replace(/ł/g, "l").replace(/[{<]/g, "(").replace(/[}>]/g, ")")
      .replace(/[;|]/g, ":").replace(/~/g, "-");
  }
  const spellable = (text) => [...text].every((ch) => FONT[ch]);

  // One sprite, if the sheet has it.
  function put(g, img, sx, sy, w, h, dx, dy) {
    if (img && sx + w <= img.width && sy + h <= img.height) g.drawImage(img, sx, sy, w, h, dx, dy, w, h);
  }
  // Words in the skin's own lettering.
  function letters(g, sheet, str, x, y, w) {
    [...plain(str)].slice(0, Math.floor(w / 5)).forEach((ch, i) => {
      const at = FONT[ch] || FONT[" "];
      put(g, sheet, at[0] * 5, at[1] * 6, 5, 6, x + i * 5, y);
    });
  }
  // A window's picture onto the screen, every pixel a clean square.
  function present(el, base) {
    const g = el.getContext("2d");
    g.imageSmoothingEnabled = false;
    g.clearRect(0, 0, el.width, el.height);
    g.drawImage(base, 0, 0, el.width, el.height);
    return g;
  }

  function draw() {
    const { skin, base } = state;
    if (!skin || !base) return null;
    const g = base.getContext("2d");
    const sh = skin.sheets;
    const s = snapshot();
    const now = performance.now();
    const spr = (...a) => put(g, ...a);
    const down = (id) => !!state.press && state.press.id === id && state.press.inside;

    g.clearRect(0, 0, W, H);
    spr(sh.main, 0, 0, W, H, 0, 0);
    spr(sh.titlebar, 27, document.hasFocus() ? 0 : 15, W, 14, 0, 0);
    spr(sh.titlebar, 0, down("menu") ? 9 : 0, 9, 9, 6, 3);
    spr(sh.titlebar, 9, 0, 9, 9, 244, 3);
    spr(sh.titlebar, 0, 18, 9, 9, 254, 3);
    spr(sh.titlebar, 18, down("close") ? 9 : 0, 9, 9, 264, 3);
    spr(sh.titlebar, 304, 0, 8, 43, 10, 22);

    // playing / paused / stopped
    spr(sh.playpaus, s.status === "play" ? 0 : s.status === "pause" ? 9 : 18, 0, 9, 9, 26, 28);
    spr(sh.playpaus, s.status === "play" ? 39 : 36, 0, 3, 9, 24, 28);

    // the time: elapsed, or what is left; it blinks while paused
    const dragPos = state.drag && state.drag.id === "pos" ? state.drag.v : null;
    const held = state.hold && now < state.hold.until ? state.hold.v : null;
    const shownPos = dragPos !== null ? dragPos * s.dur : held !== null ? held * s.dur : s.pos;
    const digits = sh.nums_ex || sh.numbers;
    const blink = s.status === "pause" && Math.floor(now / 500) % 2 === 1;
    if (s.has && !blink) {
      const left = state.remaining && s.dur > 0;
      const secs = Math.max(0, Math.floor((left ? s.dur - shownPos : shownPos) / 1000));
      const m = Math.floor(secs / 60) % 100, sec = secs % 60;
      if (left) {
        if (sh.nums_ex) spr(sh.nums_ex, 99, 0, 9, 13, 39, 26);
        else spr(sh.numbers, 20, 6, 5, 1, 40, 32);
      }
      [[Math.floor(m / 10), 48], [m % 10, 60], [Math.floor(sec / 10), 78], [sec % 10, 90]].forEach(([d, x]) =>
        spr(digits, d * 9, 0, 9, 13, x, 26));
    } else {
      for (const x of [48, 60, 78, 90]) spr(digits, 90, 0, 9, 13, x, 26);
    }

    drawVis(g, s, now);

    // the song title, scrolling when it doesn't fit
    let title = s.title;
    if (state.drag && state.drag.id === "pos") {
      title = `Seek to: ${fmtTime(shownPos)}/${fmtTime(s.dur)} (${Math.round(state.drag.v * 100)}%)`;
    } else if (state.drag && state.drag.id === "vol") {
      title = `Volume: ${Math.round(state.drag.v * 100)}%`;
    } else if (state.drag && state.drag.id === "bal") {
      const b = balanceOf(state.drag.v);
      title = `Balance: ${b ? `${Math.round(Math.abs(b) * 100)}% ${b < 0 ? "left" : "right"}` : "center"}`;
    } else if (state.drag && state.drag.id === "eq") {
      const i = state.drag.i, db = i ? state.drag.s.bands[i - 1] : state.drag.s.preamp;
      title = `EQ: ${i ? EQ_NAMES[i - 1] : "preamp"}: ${db > 0 ? "+" : ""}${db.toFixed(1)} db`;
    }
    if (title !== state.title) { state.title = title; state.scroll = 0; state.scrollAt = now + 1200; }
    state.overlay = drawTitle(g, sh, plain(title), now);

    if (s.t && s.t.bitrate) letters(g, sh.text, String(s.t.bitrate).padStart(3), 111, 43, 15);
    if (s.t && s.t.sample_rate) letters(g, sh.text, String(Math.round(s.t.sample_rate / 1000)).padStart(2), 156, 43, 10);
    spr(sh.monoster, 29, 12, 27, 12, 212, 41);
    spr(sh.monoster, 0, s.has && s.status !== "stop" ? 0 : 12, 29, 12, 239, 41);

    // volume and balance
    const vol = state.drag && state.drag.id === "vol" ? state.drag.v : volume();
    spr(sh.volume, 0, Math.round(vol * 27) * 15, 68, 13, 107, 57);
    spr(sh.volume, state.drag && state.drag.id === "vol" ? 0 : 15, 422, 14, 11, 107 + Math.round(vol * 51), 58);
    const settings = Eq.get();
    const balDrag = state.drag && state.drag.id === "bal";
    const bal = balDrag ? balanceOf(state.drag.v) : settings.bal;
    const lean = Math.round(Math.abs(bal) * 27) * 15;
    if (sh.balance.volumeAsBalance) spr(sh.balance.img, 0, lean, 38, 13, 177, 57);
    else spr(sh.balance, 9, lean, 38, 13, 177, 57);
    spr(sh.balance.volumeAsBalance ? sh.balance.img : sh.balance, balDrag ? 0 : 15, 422, 14, 11,
      177 + Math.round(((bal + 1) / 2) * 24), 58);

    spr(sh.shufrep, down("eq") ? 46 : 0, state.eqShown ? 73 : 61, 23, 12, 219, 58);
    spr(sh.shufrep, down("pl") ? 69 : 23, state.listShown ? 73 : 61, 23, 12, 242, 58);

    // the seek bar; no handle when there is nothing to seek in
    spr(sh.posbar, 0, 0, 248, 10, 16, 72);
    if (s.dur > 0 && s.t) {
      const frac = clamp(shownPos / s.dur);
      spr(sh.posbar, dragPos !== null ? 278 : 248, 0, 29, 10, 16 + Math.round(frac * 219), 72);
    }

    [["prev", 0, 23], ["play", 23, 23], ["pause", 46, 23], ["stop", 69, 23], ["next", 92, 22]].forEach(([id, sx, w]) =>
      spr(sh.cbuttons, sx, down(id) ? 18 : 0, w, 18, AT[id][0], 88));
    spr(sh.cbuttons, 114, down("eject") ? 16 : 0, 22, 16, 136, 89);
    spr(sh.shufrep, 28, (s.ns.shuffle ? 30 : 0) + (down("shuffle") ? 15 : 0), 47, 15, 164, 89);
    spr(sh.shufrep, 0, (s.ns.repeat !== "off" ? 30 : 0) + (down("repeat") ? 15 : 0), 28, 15, 210, 89);

    drawOverlay(present(state.el, base));
    drawEq(settings);
    drawBars(s, shownPos);
    return s;
  }

  // Returns what must be drawn on top at full sharpness: a title in letters
  // the skin's font doesn't have.
  function drawTitle(g, sh, title, now) {
    const X = 111, Y = 27, WIDTH = 154;
    const fits = title.length * 5 <= WIDTH;
    const loop = fits ? title : `${title}  ***  `;
    if (!fits && now >= state.scrollAt) {
      state.scroll += 5;
      state.scrollAt = now + 200;
    }
    if (!spellable(title)) return { loop, fits };
    const chars = [...loop];
    const start = fits ? 0 : Math.floor(state.scroll / 5) % chars.length;
    for (let i = 0; i < Math.floor(WIDTH / 5) && (i < chars.length || !fits); i++) {
      const at = FONT[chars[(start + i) % chars.length]] || FONT[" "];
      put(g, sh.text, at[0] * 5, at[1] * 6, 5, 6, X + i * 5, Y);
    }
    return null;
  }
  function drawOverlay(g) {
    const o = state.overlay;
    if (!o) return;
    const k = state.el.width / W;
    g.save();
    g.scale(k, k);
    g.beginPath();
    g.rect(111, 25, 154, 10);
    g.clip();
    g.fillStyle = state.skin.ink;
    g.font = "600 8px system-ui, -apple-system, 'Segoe UI', sans-serif";
    g.textBaseline = "middle";
    const w = g.measureText(o.loop).width;
    if (o.fits || w <= 154) {
      g.fillText(state.title, 111, 30.5);
    } else {
      const off = state.scroll % w;
      g.fillText(o.loop, 111 - off, 30.5);
      g.fillText(o.loop, 111 - off + w, 30.5);
    }
    g.restore();
  }

  // The spectrum. There is no measuring what a receiver across the house is
  // playing, so this is a lively guess: the same wherever the music is.
  function drawVis(g, s, now) {
    const c = state.skin.vis;
    const [X, Y] = AT.vis;
    g.fillStyle = c[0];
    g.fillRect(X, Y, 76, 16);
    g.fillStyle = c[1];
    for (let y = 0; y < 16; y += 2) for (let x = 0; x < 76; x += 2) g.fillRect(X + x, Y + y, 1, 1);
    const dt = Math.min(0.25, (now - state.visAt) / 1000);
    state.visAt = now;
    const t = now / 1000;
    for (let i = 0; i < 19; i++) {
      let want = 0;
      if (s.status === "play") {
        const beat = Math.pow(Math.max(0, Math.sin(t * 6.3 + i * 0.12)), 6) * (i < 5 ? 1 : 0.35);
        const wobble = 0.5 + 0.5 * Math.sin(t * (2.1 + i * 0.37) + i * 1.7) * Math.sin(t * (0.9 + i * 0.11) + i);
        const tilt = 1 - i / 30;
        want = 16 * clamp(tilt * (0.25 + 0.5 * wobble + 0.45 * beat));
      }
      state.bars[i] = want > state.bars[i] ? want : Math.max(want, state.bars[i] - 40 * dt);
      state.peaks[i] = state.bars[i] >= state.peaks[i] ? state.bars[i] : Math.max(0, state.peaks[i] - 9 * dt);
      const hgt = Math.round(state.bars[i]);
      for (let r = 16 - hgt; r < 16; r++) {
        g.fillStyle = c[2 + r];
        g.fillRect(X + i * 4, Y + r, 3, 1);
      }
      const pk = Math.round(state.peaks[i]);
      if (pk > 0) {
        g.fillStyle = c[23];
        g.fillRect(X + i * 4, Y + 16 - pk, 3, 1);
      }
    }
  }

  // The equaliser window. Value 0..1 of a slider: -12 dB at the bottom, +12 at the top.
  const EQ_NAMES = ["60 hz", "170 hz", "310 hz", "600 hz", "1 khz", "3 khz", "6 khz", "12 khz", "14 khz", "16 khz"];
  const EQ_X = [21, 78, 96, 114, 132, 150, 168, 186, 204, 222, 240]; // the preamp, then the ten bands
  const PRESETS = [
    ["Flat", [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]], ["Rock", [5, 3, -3, -5, -2, 2, 5, 7, 7, 7]], ["Pop", [-1, 3, 4, 5, 3, 0, -1, -1, -1, -1]],
    ["Dance", [6, 4, 1, 0, 0, -3, -4, -4, 0, 0]], ["Classical", [0, 0, 0, 0, 0, 0, -4, -4, -4, -6]],
    ["Live", [-3, 0, 2, 3, 3, 3, 2, 1, 1, 1]], ["Full bass", [6, 6, 6, 4, 1, -2, -5, -6, -6, -6]],
    ["Full treble", [-6, -6, -6, -2, 2, 6, 10, 10, 10, 10]], ["Bass and treble", [5, 4, 0, -4, -3, 1, 5, 7, 8, 8]],
    ["Spoken word", [-4, -1, 2, 4, 4, 3, 1, -1, -3, -5]],
  ];
  function drawEq(e) {
    const Q = state.eq;
    if (!Q || !state.eqShown) return;
    const q = state.skin.sheets.eqmain;
    const g = Q.base.getContext("2d");
    const down = (id) => !!state.press && state.press.id === `eq:${id}` && state.press.inside;
    if (state.drag && state.drag.id === "eq") e = state.drag.s;
    g.clearRect(0, 0, W, H);
    put(g, q, 0, 0, W, H, 0, 0);
    put(g, q, 0, document.hasFocus() ? 134 : 149, W, 14, 0, 0);
    put(g, q, 0, down("close") ? 125 : 116, 9, 9, 264, 3);
    put(g, q, e.on ? (down("on") ? 187 : 69) : (down("on") ? 128 : 10), 119, 26, 12, 14, 18);
    put(g, q, 36, 119, 32, 12, 40, 18);
    put(g, q, 224, down("presets") ? 176 : 164, 44, 12, 217, 18);
    // the curve
    put(g, q, 0, 294, 113, 19, 86, 17);
    put(g, q, 0, 314, 113, 1, 86, 17 + Math.round((1 - (e.preamp + 12) / 24) * 18));
    let last = null;
    for (let x = 0; x < 113; x++) {
      const at = (x / 112) * 9, i = Math.min(8, Math.floor(at)), f = (1 - Math.cos((at - i) * Math.PI)) / 2;
      const db = e.bands[i] + (e.bands[i + 1] - e.bands[i]) * f;
      const y = Math.round((1 - (db + 12) / 24) * 18);
      const from = last === null ? y : Math.min(last, y), to = last === null ? y : Math.max(last, y);
      for (let yy = from; yy <= to; yy++) {
        g.fillStyle = state.skin.eqLine[yy];
        g.fillRect(86 + x, 17 + yy, 1, 1);
      }
      last = y;
    }
    // the sliders
    [e.preamp, ...e.bands].forEach((db, i) => {
      const v = (db + 12) / 24, n = Math.round(v * 27);
      put(g, q, 13 + 15 * (n % 14), 164 + 65 * Math.floor(n / 14), 14, 63, EQ_X[i], 38);
      const held = state.drag && state.drag.id === "eq" && state.drag.i === i;
      put(g, q, 0, held ? 176 : 164, 11, 11, EQ_X[i] + 1, 39 + Math.round((1 - v) * 50));
    });
    present(Q.el, Q.base);
  }
  function eqHit(x, y) {
    if (y >= 36 && y < 104) {
      const i = EQ_X.findIndex((sx) => x >= sx - 2 && x < sx + 16);
      if (i >= 0) return `s${i}`;
    }
    if (x >= 14 && x < 40 && y >= 16 && y < 32) return "on";
    if (x >= 217 && x < 261 && y >= 16 && y < 32) return "presets";
    if (x >= 259 && y < 15) return "close";
    return null;
  }
  // The decibels a slider's handle is at; it settles on 0 when close to it.
  function eqDb(y) {
    const db = (1 - clamp((y - 44) / 50)) * 24 - 12;
    return Math.abs(db) < 0.7 ? 0 : Math.round(db * 10) / 10;
  }
  function eqWith(i, db) {
    const e = Eq.get();
    const s = { ...e, bands: e.bands.slice() };
    if (i) s.bands[i - 1] = db; else s.preamp = db;
    return s;
  }
  function onEqDown(e) {
    if (e.button) return;
    const [x, y] = point(e, state.eq.el);
    const id = eqHit(x, y);
    if (!id) return;
    state.eq.el.setPointerCapture(e.pointerId);
    if (id[0] === "s") {
      const i = Number(id.slice(1));
      state.drag = { id: "eq", i, s: eqWith(i, eqDb(y)) };
      Eq.set(state.drag.s, { live: true });
    } else {
      state.press = { id: `eq:${id}`, inside: true };
    }
    tick();
  }
  function onEqMove(e) {
    const [x, y] = point(e, state.eq.el);
    if (state.drag && state.drag.id === "eq") {
      state.drag.s = eqWith(state.drag.i, eqDb(y));
      Eq.set(state.drag.s, { live: true });
      draw();
    } else if (state.press && state.press.id.startsWith("eq:")) {
      const inside = `eq:${eqHit(x, y)}` === state.press.id;
      if (inside !== state.press.inside) { state.press.inside = inside; draw(); }
    }
  }
  function onEqUp(e) {
    const drag = state.drag, press = state.press;
    state.drag = state.press = null;
    if (drag && drag.id === "eq") Eq.set(drag.s); // now a receiver hears it too
    if (press && press.inside && e.type === "pointerup") {
      const id = press.id.slice(3), at = { x: e.clientX, y: e.clientY };
      if (id === "on") Eq.set({ ...Eq.get(), on: !Eq.get().on });
      else if (id === "close") toggleEq();
      else if (id === "presets") {
        showMenu(at, PRESETS.map(([label, bands]) => ({ label, run: () => Eq.set({ ...Eq.get(), on: true, bands }) })), "Presets");
      }
    }
    tick();
  }

  // The list window's title bar and bottom bar (the classic playlist frame).
  function drawBars(s, shownPos) {
    const L = state.list;
    if (!L || !state.listShown) return;
    const sh = state.skin.sheets, p = sh.pledit;
    const y = document.hasFocus() ? 0 : 21;
    let g = L.topBase.getContext("2d");
    g.clearRect(0, 0, W, 20);
    for (let x = 25; x < W - 25; x += 25) put(g, p, 127, y, 25, 20, x, 0);
    put(g, p, 26, y, 100, 20, 87, 0);
    put(g, p, 0, y, 25, 20, 0, 0);
    put(g, p, 153, y, 25, 20, W - 25, 0);
    present(L.top, L.topBase);
    g = L.botBase.getContext("2d");
    g.clearRect(0, 0, W, 38);
    put(g, p, 0, 72, 125, 38, 0, 0);
    put(g, p, 126, 72, 150, 38, 125, 0);
    if (s.has) {
      letters(g, sh.text, state.queueMs ? `${fmtTime(shownPos)}/${fmtTime(state.queueMs)}` : fmtTime(shownPos), 132, 10, 90);
      letters(g, sh.text, fmtTime(shownPos).padStart(5), 191, 23, 25);
    }
    present(L.bot, L.botBase);
  }

  // ------------------------------------------------------------ the list window: where to play, what to play

  function buildList() {
    const L = { topBase: canvas(W, 20), botBase: canvas(W, 38) };
    L.top = h("canvas", { class: "rioamp-bar" });
    L.bot = h("canvas", { class: "rioamp-bar" });
    L.targets = h("div", { class: "rioamp-col" });
    L.cats = h("div", { class: "rioamp-cats" });
    L.crumb = h("button", { class: "rioamp-crumb", type: "button", onclick: () => { if (state.nav.length > 1) { state.nav.pop(); show(); } } });
    L.filter = h("input", { class: "rioamp-filter", type: "search", placeholder: "find…", "aria-label": "Find in this list",
      autocomplete: "off", oninput: renderRows });
    L.rows = h("div", { class: "rioamp-rows", onscroll: placeHandle });
    L.handle = h("div", { class: "rioamp-handle" });
    L.left = h("div", { class: "rioamp-edge" });
    L.right = h("div", { class: "rioamp-edge r" }, L.handle);
    L.el = h("div", { class: "rioamp-list" }, L.top,
      h("div", { class: "rioamp-mid" }, L.left,
        h("div", { class: "rioamp-body" },
          h("div", { class: "rioamp-picks" },
            h("div", { class: "rioamp-pick" }, h("div", { class: "rioamp-head" }, "Play on"), L.targets),
            h("div", { class: "rioamp-pick wide" }, h("div", { class: "rioamp-head" }, "Library"), L.cats)),
          h("div", { class: "rioamp-nav" }, L.crumb, L.filter),
          L.rows),
        L.right),
      L.bot);

    const where = (e, el, hgt) => {
      const b = el.getBoundingClientRect();
      return [((e.clientX - b.left) / b.width) * W, ((e.clientY - b.top) / b.height) * hgt];
    };
    L.top.addEventListener("click", (e) => {
      const [x, y] = where(e, L.top, 20);
      if (x >= 260 && y < 15) toggleList();
    });
    L.bot.addEventListener("click", (e) => {
      const [x, y] = where(e, L.bot, 38);
      const at = { x: e.clientX, y: e.clientY };
      if (y >= 6 && y < 28) {
        if (x >= 12 && x < 38) open("artists");
        else if (x >= 41 && x < 67) showMenu(at, [{ label: "Clear Up Next", icon: "trash", run: () => Queue.edit("clear") }], "Up Next");
        else if ((x >= 99 && x < 125) || (x >= 229 && x < 255)) showMenu(at, menuItems(), "RioAmp");
      }
      if (y >= 19 && y < 33) {
        const i = [128, 136, 145, 155, 164, 172, 182].findIndex((edge) => x < edge);
        if (i > 0) { act(["prev", "play", "pause", "stop", "next", "eject"][i - 1], at); tick(); }
      }
    });
    // the scroll handle
    const scrub = (e) => {
      const b = L.right.getBoundingClientRect();
      const track = L.right.clientHeight - 18;
      const y = ((e.clientY - b.top) / b.height) * L.right.clientHeight;
      L.rows.scrollTop = clamp((y - 9) / Math.max(1, track)) * (L.rows.scrollHeight - L.rows.clientHeight);
    };
    L.right.addEventListener("pointerdown", (e) => { L.right.setPointerCapture(e.pointerId); L.scrubbing = true; scrub(e); });
    L.right.addEventListener("pointermove", (e) => { if (L.scrubbing) scrub(e); });
    for (const ev of ["pointerup", "pointercancel"]) L.right.addEventListener(ev, () => { L.scrubbing = false; });
    // right-click or long press on a row: what can be done with it
    L.rows.addEventListener("pointerdown", (e) => {
      const row = e.target.closest && e.target.closest(".rioamp-row");
      state.menuRow = row ? row._row : null;
    }, true);
    L.rows.addEventListener("contextmenu", (e) => {
      const row = e.target.closest && e.target.closest(".rioamp-row");
      state.menuRow = row ? row._row : null;
    }, true);
    bindContext(L.rows, () => {
      const r = state.menuRow;
      const items = r && r.menu ? r.menu() : [];
      return items.length ? items : r && r.run ? [{ label: r.more ? "Open" : "Play", icon: "play", run: r.run }] : menuItems();
    }, () => (state.menuRow ? state.menuRow.label : "RioAmp"));

    L.cats.append(...CATS.map(([id, label]) => h("button", { type: "button", "data-cat": id, onclick: () => open(id) }, label)));
    return L;
  }

  function placeHandle() {
    const L = state.list;
    if (!L) return;
    const max = L.rows.scrollHeight - L.rows.clientHeight;
    L.handle.style.top = `${max > 0 ? (L.rows.scrollTop / max) * (L.right.clientHeight - 18) : 0}px`;
  }

  // Where the music goes: this browser, a receiver or a speaker.
  function renderTargets() {
    const L = state.list;
    if (!L) return;
    const options = (RX.on ? [] : [{ id: "", name: "This browser", online: true }]).concat(
      Player.receivers.filter(canTarget).map((r) => ({ id: r.id, name: r.id === RX.id ? `This device (${r.name})` : r.name, online: r.online })));
    const key = JSON.stringify([Player.target, options]);
    if (key === state.targetKey) return;
    state.targetKey = key;
    L.targets.replaceChildren(...options.map((o) => h("button", {
      type: "button", class: `${o.id === Player.target ? "sel cur" : ""}${o.online ? "" : " dim"}`, title: o.name,
      onclick: () => chooseTarget(o.id),
    }, h("span", { class: "l" }, o.name))));
  }

  // The lists. A row is {label, right, run, menu, more, current, dim}.
  const sumMs = (tracks) => tracks.reduce((n, t) => n + (t.duration_ms || 0), 0);
  const trackMenuItems = (tracks) => [
    { label: "Play next", icon: "play", run: () => playNext(tracks) },
    { label: "Add to Up Next", icon: "list", run: () => addToQueue(tracks) },
  ];
  function trackRows(tracks, { numbers = true, artist = true } = {}) {
    return tracks.map((t, i) => ({
      label: `${numbers ? `${i + 1}. ` : ""}${artist && t.artist ? `${t.artist} - ` : ""}${t.title}`,
      right: fmtTime(t.duration_ms),
      run: () => Player.playTracks(tracks, i),
      menu: () => trackMenuItems([t]),
    }));
  }
  function albumRow(al, withArtist) {
    const tracks = async () => (await api(`/api/albums/${al.id}`)).tracks;
    return {
      label: withArtist && al.artist ? `${al.artist} - ${al.title}` : al.title, right: al.year || "", more: true,
      run: () => push(al.title, async () => {
        const list = await tracks();
        const mixed = list.some((t) => t.artist !== list[0].artist);
        return trackRows(list, { artist: mixed });
      }),
      menu: () => [
        { label: "Play", icon: "play", run: async () => Player.playTracks(await tracks(), 0) },
        { label: "Play next", icon: "play", run: async () => playNext(await tracks()) },
        { label: "Add to Up Next", icon: "list", run: async () => addToQueue(await tracks()) },
      ],
    };
  }
  const LOAD = {
    async queue() {
      const q = await Queue.get();
      if (!q.known) { state.queueMs = 0; return [{ label: "This player keeps its own list", dim: true }]; }
      state.queueMs = sumMs(q.tracks);
      if (!q.tracks.length) return [{ label: "Nothing lined up. Pick something from the Library.", dim: true }];
      return q.tracks.map((t, i) => ({
        label: `${i + 1}. ${t.artist ? `${t.artist} - ` : ""}${t.title}`, right: fmtTime(t.duration_ms), current: i === q.index,
        run: () => Queue.edit("jump", { pos: i }).catch(fail),
        menu: () => (q.editable && i !== q.index
          ? [{ label: "Play", icon: "play", run: () => Queue.edit("jump", { pos: i }) },
            { label: "Remove", icon: "trash", run: () => Queue.edit("remove", { pos: i }) }] : []),
      }));
    },
    artists: async () => (await api("/api/artists")).map((a) => ({
      label: a.name, right: a.track_count, more: true,
      run: () => push(a.name, async () => (await api(`/api/artists/${a.id}`)).albums.map((al) => albumRow(al))),
    })),
    albums: async () => (await api("/api/albums")).map((al) => albumRow(al, true)),
    songs: async () => trackRows((await api("/api/songs?offset=0&limit=100000")).tracks, { numbers: false }),
    genres: async () => (await api("/api/genres")).map((gn) => ({
      label: gn.name, right: gn.track_count, more: true,
      run: () => push(gn.name, async () => (await api(`/api/genres/${gn.id}`)).albums.map((al) => albumRow(al, true))),
    })),
    playlists: async () => (await api("/api/playlists")).map((p) => ({
      label: p.name, right: p.track_count, more: true,
      run: () => push(p.name, async () => trackRows((await api(`/api/playlists/${p.id}`)).tracks)),
    })),
    podcasts: async () => (await api("/api/podcasts")).map((p) => ({
      label: p.title, more: true,
      run: () => push(p.title, async () => (await api(`/api/podcasts/${p.id}`)).episodes.filter((e) => e.track_id).map((e) => ({
        label: e.title, right: e.duration_ms ? fmtTime(e.duration_ms) : "", dim: !!e.played,
        run: () => playEpisode(e, p).catch(fail),
      }))),
    })),
    recent: async () => (await api("/api/recent")).map((al) => albumRow(al, true)),
  };

  function open(cat) {
    if (!state.listShown) toggleList();
    state.nav = [{ cat, title: (CATS.find((c) => c[0] === cat) || [])[1] || cat, load: LOAD[cat] }];
    show();
  }
  function push(title, load) {
    state.nav.push({ cat: state.nav[0].cat, title, load });
    show();
  }
  // Loads and shows the list on top; with keep, where it was scrolled to stays.
  async function show(keep) {
    const L = state.list, top = state.nav[state.nav.length - 1];
    if (!L || !top) return;
    const seq = ++state.loadSeq;
    for (const b of L.cats.children) b.classList.toggle("sel", b.dataset.cat === top.cat);
    L.crumb.textContent = state.nav.length > 1 ? `‹ ${state.nav.map((n) => n.title).join(" › ")}` : top.title;
    if (!keep) L.filter.value = "";
    let rows;
    try { rows = await top.load(); } catch (e) { rows = [{ label: e.message || String(e), dim: true }]; }
    if (seq !== state.loadSeq || !state.list) return;
    state.rows = rows;
    const at = L.rows.scrollTop;
    renderRows();
    if (keep) L.rows.scrollTop = at;
    else {
      const cur = L.rows.querySelector(".cur");
      L.rows.scrollTop = cur ? Math.max(0, cur.offsetTop - L.rows.clientHeight / 3) : 0;
    }
    placeHandle();
  }
  function renderRows() {
    const L = state.list;
    if (!L) return;
    const find = L.filter.value.trim().toLowerCase();
    const rows = find ? state.rows.filter((r) => r.label.toLowerCase().includes(find)) : state.rows;
    const MAX = 3000;
    L.rows.replaceChildren(...rows.slice(0, MAX).map((r) => {
      const el = h("div", { class: `rioamp-row${r.current ? " cur" : ""}${r.dim ? " dim" : ""}`, onclick: r.run || null },
        h("span", { class: "l" }, r.label), h("span", { class: "r" }, r.more ? `${r.right || ""} ›` : r.right || ""));
      el._row = r;
      return el;
    }), rows.length > MAX ? h("div", { class: "rioamp-row dim" }, `…and ${rows.length - MAX} more: type in “find” to narrow it`) : "",
    find && !rows.length ? h("div", { class: "rioamp-row dim" }, "Nothing here by that name") : "");
    placeHandle();
  }
  // Up Next follows the player.
  function queueChanged() {
    const top = state.nav[state.nav.length - 1];
    if (state.on && top && top.cat === "queue") show(true);
  }

  // ------------------------------------------------------------ size and place

  // On a phone the windows fill the screen. On a desk they are life-size or
  // twice that, and an installed app's window shrinks to fit around them.
  function layout(fit) {
    if (!state.on) return;
    const touch = coarse();
    const rw = state.room.clientWidth, rh = state.room.clientHeight;
    const fixed = state.eqShown ? H * 2 : H; // the player, and the equaliser under it
    const full = state.listShown ? fixed + LIST_H : fixed;
    let k, hn;
    if (touch) {
      k = Math.min(rw / W, 2.5, rh / (state.listShown ? fixed + 140 : fixed));
      hn = state.listShown ? rh / k : fixed;
    } else {
      k = Math.max(0.5, Math.min(state.double ? 2 : 1, rw / W));
      hn = state.listShown ? Math.max(fixed + 100, Math.min(full, rh / k)) : fixed;
      if (fit === true) fitWindow(W * k, full * k);
    }
    state.scale = k;
    state.desk.classList.toggle("coarse", touch);
    const st = state.stack.style;
    st.height = `${hn}px`;
    st.left = `${Math.max(0, (rw - W * k) / 2)}px`;
    st.top = `${touch ? 0 : Math.max(0, (rh - hn * k) / 2)}px`;
    st.transform = `scale(${k})`;
    const dpr = window.devicePixelRatio || 1;
    const size = (el, hgt) => {
      el.style.width = `${W}px`;
      el.style.height = `${hgt}px`;
      const bw = Math.round(W * k * dpr), bh = Math.round(hgt * k * dpr);
      if (el.width !== bw || el.height !== bh) { el.width = bw; el.height = bh; }
    };
    size(state.el, H);
    size(state.eq.el, H);
    state.eq.el.hidden = !state.eqShown;
    size(state.list.top, 20);
    size(state.list.bot, 38);
    state.list.el.hidden = !state.listShown;
    draw();
    placeHandle();
  }
  function fitWindow(w, hgt) {
    const app = ["standalone", "minimal-ui", "window-controls-overlay"].some((m) => window.matchMedia(`(display-mode: ${m})`).matches);
    if (!app) return;
    try {
      if (!sessionStorage.getItem("rio.rioamp.win")) sessionStorage.setItem("rio.rioamp.win", JSON.stringify([outerWidth, outerHeight]));
      window.resizeTo(Math.ceil(w + outerWidth - innerWidth), Math.ceil(hgt + outerHeight - innerHeight));
    } catch { /* the browser keeps its window as it is */ }
  }
  function unfitWindow() {
    try {
      const was = JSON.parse(sessionStorage.getItem("rio.rioamp.win") || "null");
      sessionStorage.removeItem("rio.rioamp.win");
      if (was) window.resizeTo(was[0], was[1]);
    } catch { /* as above */ }
  }

  function tick() {
    clearTimeout(state.timer);
    if (!state.on || document.hidden) return;
    const s = draw();
    const lively = !!state.drag || !!state.press || (s && (s.status !== "stop" || state.title.length * 5 > 154))
      || state.bars.some((b) => b > 0.5);
    state.timer = setTimeout(tick, lively ? 50 : 250);
  }
  // The player's state changed (app.js says so).
  function poke() {
    if (!state.on) return;
    renderTargets();
    const key = `${Queue.remoteKey()}|${Player.index}|${Player.queue.length}`;
    if (key !== state.queueKey) { state.queueKey = key; queueChanged(); }
    tick();
  }

  // ------------------------------------------------------------ what the buttons do

  function menuItems() {
    return [
      { label: "Load a skin…", icon: "download", run: chooseSkin },
      state.skin && state.skin.name ? { label: "Back to the RioAmp skin", icon: "refresh", run: useSkin } : null,
      coarse() ? null : { label: state.double ? "Normal size" : "Double size", icon: "monitor", run: toggleDouble },
      { label: state.eqShown ? "Hide the equaliser" : "Show the equaliser", icon: "list", run: toggleEq },
      { label: state.listShown ? "Hide the list" : "Show the list", icon: "list", run: toggleList },
      "-",
      { label: "Back to RioServer NG", icon: "x", run: () => Skinned.set(false) },
    ];
  }
  function toggleDouble() {
    state.double = !state.double;
    store("rio.rioamp.double", state.double ? null : "0");
    layout(true);
  }
  function toggleEq() {
    state.eqShown = !state.eqShown;
    store("rio.rioamp.eq", state.eqShown ? null : "0");
    layout(true);
  }
  function toggleList() {
    state.listShown = !state.listShown;
    store("rio.rioamp.list", state.listShown ? null : "0");
    layout(true);
  }

  function act(id, at) {
    const s = snapshot();
    const live = !!s.lv; // an AirPlay or Spotify session: only play/pause and volume apply
    const actions = {
      prev: () => { if (!live) Player.prev(); },
      next: () => { if (!live) Player.next(); },
      play: () => { if (s.status !== "play") Player.toggle(); },
      pause: () => { if (s.has) Player.toggle(); },
      stop: () => {
        if (!s.has) return;
        if (Player.target) Player.command(live ? "toggle" : "stop");
        else { audio.pause(); audio.currentTime = 0; renderNowPlaying(); }
      },
      eject: () => open("artists"),
      shuffle: () => { if (!live) toggleShuffle(); },
      repeat: () => { if (!live) cycleRepeat(); },
      eq: toggleEq,
      pl: toggleList,
      time: () => {
        state.remaining = !state.remaining;
        store("rio.rioamp.remaining", state.remaining ? "1" : null);
      },
      clutO: () => showMenu(at, menuItems(), "RioAmp"),
      menu: () => showMenu(at, menuItems(), "RioAmp"),
      clutD: () => { if (!coarse()) toggleDouble(); },
      close: () => { Skinned.set(false); toast("RioAmp is off. Settings turns it back on."); },
      logo: () => toast("RioAmp. It really kicks the alpaca's butt."),
    };
    if (actions[id]) actions[id]();
  }

  function point(e, el = state.el) {
    const b = el.getBoundingClientRect();
    return [((e.clientX - b.left) / b.width) * W, ((e.clientY - b.top) / b.height) * H];
  }
  const slide = { vol: (x) => clamp((x - 107 - 7) / 51), bal: (x) => clamp((x - 177 - 7) / 24), pos: (x) => clamp((x - 16 - 14) / 219) };
  // The balance a handle position stands for; it settles in the middle when close to it.
  function balanceOf(v) {
    const b = v * 2 - 1;
    return Math.abs(b) < 0.15 ? 0 : Math.round(b * 100) / 100;
  }
  const applyBalance = (live) => Eq.set({ ...Eq.get(), bal: balanceOf(state.drag.v) }, { live });

  function onDown(e) {
    if (e.button) return;
    const [x, y] = point(e);
    const id = hit(x, y);
    if (!id) return;
    state.el.setPointerCapture(e.pointerId);
    if (id === "vol" || id === "pos" || id === "bal") {
      const s = snapshot();
      if (id === "pos" && !(s.t && s.dur > 0)) return;
      state.drag = { id, v: slide[id](x) };
      if (id === "vol") { $("#volume")._held = true; applyVolume(); }
      if (id === "bal") applyBalance(true);
    } else {
      state.press = { id, inside: true };
    }
    tick();
  }
  function onMove(e) {
    const [x, y] = point(e);
    if (state.drag) {
      state.drag.v = slide[state.drag.id](x);
      if (state.drag.id === "vol") applyVolume();
      if (state.drag.id === "bal") applyBalance(true);
      draw();
    } else if (state.press) {
      const inside = hit(x, y) === state.press.id;
      if (inside !== state.press.inside) { state.press.inside = inside; draw(); }
    }
  }
  function onUp(e) {
    const drag = state.drag, press = state.press;
    if (drag && drag.id === "bal") applyBalance(false); // now a receiver hears it too
    state.drag = state.press = null;
    if (drag && drag.id === "pos" && e.type === "pointerup") {
      state.hold = { v: drag.v, until: performance.now() + (Player.target ? 1500 : 300) };
      seekTo(drag.v);
    }
    if (drag && drag.id === "vol") {
      const slider = $("#volume");
      slider._held = false;
      slider._busyUntil = Date.now() + 1500;
    }
    // a long press already opened the menu: it isn't also a press of the button
    if (press && press.inside && e.type === "pointerup" && !state.el._longPressed) {
      act(press.id, { x: e.clientX, y: e.clientY });
    }
    state.el._longPressed = false;
    tick();
  }
  function applyVolume() {
    const v = Math.round(state.drag.v * 100);
    $("#volume").value = v;
    setVolume(v);
  }

  // The classic's own keys, along with the app's: Z X C V B along the bottom row.
  function onKey(e) {
    if (!state.on || e.ctrlKey || e.metaKey || e.altKey || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
    if (!$("#menu-layer").hidden || document.querySelector(".dialog-back")) return;
    const id = { z: "prev", x: "play", c: "pause", v: "stop", b: "next" }[e.key.toLowerCase()];
    if (!id) return;
    e.preventDefault();
    act(id);
    tick();
  }

  // ------------------------------------------------------------ skins

  let picker = null;
  function chooseSkin() {
    if (!picker) {
      picker = h("input", { type: "file", hidden: true, onchange: () => {
        const f = picker.files[0];
        picker.value = "";
        if (f) loadSkinFile(f).catch(fail);
      } });
      document.body.append(picker);
    }
    picker.click();
  }
  async function loadSkinFile(file) {
    const name = file.name.replace(/\.(wsz|zip)$/i, "");
    const buffer = await file.arrayBuffer();
    const skin = await readSkin(buffer, name); // before it is kept: a file that isn't a skin changes nothing
    try { await saved({ name, buffer }); } catch { toast("This browser won't keep the skin after a reload"); }
    setSkin(skin);
    toast(`Wearing “${name}”`);
  }
  async function useSkin() {
    try { await saved(null); } catch { /* nothing was kept */ }
    setSkin(builtinSkin());
  }
  // A piece of a sheet as a picture a stylesheet can tile.
  function cut(img, sx, sy, w, hgt) {
    const c = canvas(w, hgt);
    put(c.getContext("2d"), img, sx, sy, w, hgt, 0, 0);
    return `url(${c.toDataURL()})`;
  }
  function setSkin(skin) {
    state.skin = skin;
    if (!state.on) return;
    // around the windows, the screen takes the colour of the skin's own edge
    let edge = "#1b252b";
    try {
      const d = skin.sheets.main.getContext("2d").getImageData(2, 60, 1, 1).data;
      edge = `rgb(${d[0]},${d[1]},${d[2]})`;
    } catch { /* the usual */ }
    const root = state.desk.style, L = state.list, p = skin.sheets.pledit;
    root.setProperty("--rioamp-edge", edge);
    root.setProperty("--pl-normal", skin.list.normal);
    root.setProperty("--pl-current", skin.list.current);
    root.setProperty("--pl-bg", skin.list.bg);
    root.setProperty("--pl-sel", skin.list.sel);
    L.left.style.backgroundImage = cut(p, 0, 42, 12, 29);
    L.right.style.backgroundImage = cut(p, 31, 42, 20, 29);
    L.handle.style.backgroundImage = cut(p, 52, 53, 8, 18);
    for (const meta of document.querySelectorAll('meta[name="theme-color"]')) meta.setAttribute("content", edge);
    draw();
    Skinned.changed();
  }

  // ------------------------------------------------------------ on and off

  async function start() {
    if (state.on) return;
    state.on = true;
    state.base = canvas(W, H);
    state.el = h("canvas", { class: "rioamp-win", role: "img", tabindex: "0",
      "aria-label": "RioAmp player. Space plays and pauses, N and P skip, plus and minus change the volume." });
    const el = state.el;
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onUp);
    el.addEventListener("wheel", (e) => { e.preventDefault(); nudgeVolume(e.deltaY < 0 ? 5 : -5); draw(); }, { passive: false });
    bindContext(el, menuItems, () => "RioAmp");
    state.eq = { base: canvas(W, H), el: h("canvas", { class: "rioamp-eq", role: "img", "aria-label": "Equaliser" }) };
    state.eq.el.addEventListener("pointerdown", onEqDown);
    state.eq.el.addEventListener("pointermove", onEqMove);
    state.eq.el.addEventListener("pointerup", onEqUp);
    state.eq.el.addEventListener("pointercancel", onEqUp);
    state.list = buildList();
    state.stack = h("div", { class: "rioamp-stack" }, el, state.eq.el, state.list.el);
    state.room = h("div", { class: "rioamp-room" }, state.stack);
    state.desk = h("div", { class: "rioamp-desk" }, state.room);
    state.desk.addEventListener("dragover", (e) => e.preventDefault());
    state.desk.addEventListener("drop", (e) => {
      e.preventDefault();
      const f = e.dataTransfer.files[0];
      if (f) loadSkinFile(f).catch(fail);
    });
    document.body.append(state.desk);
    document.body.classList.add("rioamp");
    state.was = document.title;
    document.title = "RioAmp";
    Queue.listeners.add(queueChanged);
    state.targetKey = state.queueKey = "";
    setSkin(builtinSkin());
    layout(true);
    open("queue");
    poke();
    try {
      const kept = await saved();
      if (kept && state.on) setSkin(await readSkin(kept.buffer, kept.name));
    } catch { /* the built-in skin */ }
  }
  function stop() {
    if (!state.on) return;
    state.on = false;
    clearTimeout(state.timer);
    Queue.listeners.delete(queueChanged);
    state.desk.remove();
    state.desk = state.room = state.stack = state.el = state.base = state.eq = state.list = null;
    state.press = state.drag = null;
    document.body.classList.remove("rioamp");
    document.title = state.was || document.title;
    applyTheme(store("rio.theme") || "system"); // the browser's own bar gets its colour back
    unfitWindow();
  }

  window.addEventListener("resize", () => layout());
  document.addEventListener("visibilitychange", tick);
  window.addEventListener("focus", () => state.on && draw());
  window.addEventListener("blur", () => state.on && draw());
  document.addEventListener("keydown", onKey);

  return {
    start, stop, chooseSkin, loadSkinFile, poke,
    defaultSkin: useSkin,
    get skinName() { return state.skin && state.skin.name; },
  };
})();
