/* Armariu skyline: the moving backdrop behind the hero of both apps.
   With a photo (the host's data-photo, e.g. static/img/hero.jpg, plus an optional hero.json beside
   it giving the waterline, focus point and aviation lights), the photo is painted on a canvas and
   brought to life: a slow Ken Burns drift, pointer and scroll parallax, ripples and glints on the
   water below the waterline, the city's own lights twinkling (found in the photo itself), blinking
   beacons and a passing plane. While the photo loads, or without one, a drawn City-at-dusk skyline
   is the fallback. Seeded, so it looks the same on every load. Honors prefers-reduced-motion by
   painting one still frame. */
(function () {
  function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
  const HORIZON = 0.69;
  const SPECS = [
    { y: HORIZON - 0.075, minH: 0.05, maxH: 0.17, wMin: 18, wMax: 60, lit: 0.10, tone: [27, 35, 58], par: 0.15, blur: 1.2 },
    { y: HORIZON - 0.04, minH: 0.08, maxH: 0.30, wMin: 22, wMax: 70, lit: 0.20, tone: [17, 23, 40], par: 0.35, blur: 0.4 },
    { y: HORIZON, minH: 0.09, maxH: 0.22, wMin: 30, wMax: 90, lit: 0.28, tone: [10, 13, 24], par: 0.6, blur: 0 },
  ];

  function shapePath(bd, base, H) {
    const top = base - bd.h * H, x = bd.x, w = bd.w, p = new Path2D();
    if (bd.kind === "spire") { p.moveTo(x, base); p.lineTo(x + w * 0.42, top); p.lineTo(x + w * 0.5, top - 16); p.lineTo(x + w, base); }
    else if (bd.kind === "dome") { p.moveTo(x, base); p.lineTo(x + w * 0.04, top + w * 0.7); p.bezierCurveTo(x + w * 0.05, top - w * 0.15, x + w * 0.95, top - w * 0.15, x + w * 0.96, top + w * 0.7); p.lineTo(x + w, base); }
    else if (bd.kind === "flare") { p.moveTo(x + w * 0.12, base); p.lineTo(x - w * 0.06, top + 6); p.quadraticCurveTo(x + w * 0.5, top - 8, x + w * 1.06, top + 6); p.lineTo(x + w * 0.88, base); }
    else if (bd.kind === "step") { const a = bd.h * H; p.moveTo(x, base); p.lineTo(x, top + a * 0.3); p.lineTo(x + w * 0.2, top + a * 0.3); p.lineTo(x + w * 0.2, top); p.lineTo(x + w * 0.8, top); p.lineTo(x + w * 0.8, top + a * 0.15); p.lineTo(x + w, top + a * 0.15); p.lineTo(x + w, base); }
    else p.rect(x, top, w, bd.h * H);
    p.closePath();
    return { p, top };
  }

  function buildLayers(W, H, seed, dpr) {
    const r = rng(seed), PAD = 80;
    return SPECS.map((sp, li) => {
      const cv = document.createElement("canvas"), cw = W + PAD * 2;
      cv.width = cw * dpr; cv.height = H * dpr;
      const c = cv.getContext("2d"); c.setTransform(dpr, 0, 0, dpr, 0, 0);
      const base = H * sp.y, flicker = [], beacons = [];
      if (sp.blur) c.filter = `blur(${sp.blur}px)`;
      let x = 0;
      while (x < cw) {
        const bw = sp.wMin + r() * (sp.wMax - sp.wMin);
        let h = sp.minH + Math.pow(r(), 1.6) * (sp.maxH - sp.minH), kind = "box";
        if (li === 1) { const k = r(); if (k < 0.06) { kind = "spire"; h = sp.maxH * 1.9; } else if (k < 0.11) { kind = "dome"; h = sp.maxH * 1.15; } else if (k < 0.16) { kind = "flare"; h = sp.maxH * 1.05; } else if (k < 0.24) { kind = "step"; h *= 1.3; } }
        const bd = { x, w: bw, h, kind };
        const { p, top } = shapePath(bd, base, H);
        const g = c.createLinearGradient(0, top, 0, base);
        g.addColorStop(0, `rgb(${sp.tone[0] + 16},${sp.tone[1] + 18},${sp.tone[2] + 26})`); g.addColorStop(1, `rgb(${sp.tone.join(",")})`);
        c.fillStyle = g; c.fill(p);
        // sunset rim light on the west faces
        c.save(); c.clip(p); const rim = c.createLinearGradient(x + bw, 0, x + bw - 14, 0);
        rim.addColorStop(0, `rgba(255,160,100,${0.16 + li * 0.05})`); rim.addColorStop(1, "rgba(255,160,100,0)"); c.fillStyle = rim; c.fillRect(x, top - 20, bw, base - top + 20);
        // glass sheen
        const sh = c.createLinearGradient(x, top, x + bw, base); sh.addColorStop(0, "rgba(140,170,255,.05)"); sh.addColorStop(0.5, "rgba(140,170,255,0)"); c.fillStyle = sh; c.fillRect(x, top, bw, base - top);
        // windows: most are static (baked in), a few flicker each frame
        const cols = Math.max(2, Math.floor((bw - 4) / 7)), rows = Math.floor((base - top - 8) / 9);
        for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) {
          if (r() >= sp.lit) continue;
          const wx = x + 3 + i * 7, wy = top + 6 + j * 9;
          if (!c.isPointInPath(p, (wx + 1.5) * dpr, (wy + 2) * dpr)) continue;
          const warm = r() < 0.12, a = 0.35 + 0.5 * r();
          if (r() < 0.06) { flicker.push([wx, wy, warm, a, r() * 40, 0.15 + r() * 0.25]); continue; }
          c.fillStyle = warm ? `rgba(255,200,130,${a * 0.18})` : `rgba(255,236,190,${a * 0.12})`; c.fillRect(wx - 1.5, wy - 1.5, 6, 7);   // glow
          c.fillStyle = warm ? `rgba(255,214,150,${a})` : `rgba(255,236,190,${a * 0.75})`; c.fillRect(wx, wy, 3, 4);
        }
        c.restore();
        if (h > sp.maxH && r() < 0.85) beacons.push([x + bw * 0.5, top - (kind === "spire" ? 16 : 2), r() * 6.28]);
        x += bw + (li === 2 ? r() * 6 : r() * 3);
      }
      return { ...sp, cv, cw, base, flicker, beacons, PAD };
    });
  }


  // ---------------------------------------------------------------- photo mode
  const TINTS = { warm: "255,190,115", white: "255,238,210", cool: "190,214,255", red: "255,60,50" };
  function sprite(rgb, star) {
    const n = 64, h = n / 2, c = document.createElement("canvas"); c.width = c.height = n;
    const g = c.getContext("2d"), rg = g.createRadialGradient(h, h, 0, h, h, h);
    rg.addColorStop(0, "rgba(255,255,255,1)"); rg.addColorStop(0.1, `rgba(${rgb},.95)`); rg.addColorStop(0.35, `rgba(${rgb},.28)`); rg.addColorStop(1, `rgba(${rgb},0)`);
    g.fillStyle = rg; g.fillRect(0, 0, n, n);
    if (star) {
      const lh = g.createLinearGradient(0, 0, n, 0); lh.addColorStop(0, `rgba(${rgb},0)`); lh.addColorStop(0.5, "rgba(255,255,255,.9)"); lh.addColorStop(1, `rgba(${rgb},0)`);
      g.fillStyle = lh; g.fillRect(0, h - 0.8, n, 1.6);
      const lv = g.createLinearGradient(0, 0, 0, n); lv.addColorStop(0.15, `rgba(${rgb},0)`); lv.addColorStop(0.5, "rgba(255,255,255,.7)"); lv.addColorStop(0.85, `rgba(${rgb},0)`);
      g.fillStyle = lv; g.fillRect(h - 0.6, 0, 1.2, n);
    }
    return c;
  }
  let SPRITES = null;
  function sprites() {
    if (!SPRITES) { SPRITES = {}; for (const [k, rgb] of Object.entries(TINTS)) SPRITES[k] = { glow: sprite(rgb, false), star: sprite(rgb, true) }; }
    return SPRITES;
  }
  const tintOf = (r, g, b) => (b > r + 12 ? "cool" : r > b + 60 ? "warm" : "white");

  // Reads a small copy of the photo once: where its lights are, and which parts of the water shine.
  function preparePhoto(img, meta) {
    const iw = img.naturalWidth, ih = img.naturalHeight, k = iw / 1170;   // effects are tuned on a 1170 px wide photo
    const wl = typeof meta.waterline === "number" ? meta.waterline : null;
    const focus = Array.isArray(meta.focus) ? meta.focus : [0.5, 0.5];
    const aw = 320, ah = Math.max(1, Math.round((aw * ih) / iw));
    const ac = document.createElement("canvas"); ac.width = aw; ac.height = ah;
    const a = ac.getContext("2d", { willReadFrequently: true }); a.drawImage(img, 0, 0, aw, ah);
    let px = null; try { px = a.getImageData(0, 0, aw, ah).data; } catch (e) { /* a photo from another origin can't be read */ }
    const L = (x, y) => { const i = (y * aw + x) * 4; return 0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]; };
    const r = rng(29), lights = [], glints = [];
    if (px) {
      const cand = [], yEnd = Math.floor(ah * (wl || 0.92)) - 2;
      for (let y = Math.max(1, Math.floor(ah * 0.05)); y < yEnd; y++) for (let x = 1; x < aw - 1; x++) {
        const v = L(x, y);
        if (v >= 150 && v >= L(x - 1, y) && v >= L(x + 1, y) && v >= L(x, y - 1) && v >= L(x, y + 1)) cand.push([v, x, y]);
      }
      cand.sort((p, q) => q[0] - p[0]);
      const taken = new Set();
      for (const [v, x, y] of cand) {
        const cell = ((x / 7) | 0) + "," + ((y / 7) | 0);   // one light per cell, so they spread across the city
        if (taken.has(cell)) continue; taken.add(cell);
        const i = (y * aw + x) * 4;
        lights.push({ x: ((x + 0.5) / aw) * iw, y: ((y + 0.5) / ah) * ih, tint: tintOf(px[i], px[i + 1], px[i + 2]), ph: r() * 6.28, sp: 0.25 + r() * 0.7, big: v > 236 && r() < 0.28 });
        if (lights.length >= 120) break;
      }
      if (wl) for (let y = Math.ceil(ah * wl) + 1; y < ah; y++) for (let x = 0; x < aw; x += 2) {
        const v = L(x, y); if (v < 75) continue;
        const i = (y * aw + x) * 4;
        glints.push([((x + r()) / aw) * iw, ((y + r() - 0.5) / ah) * ih, Math.min(1, v / 210), `rgb(${Math.min(255, px[i] + 50)},${Math.min(255, px[i + 1] + 40)},${Math.min(255, px[i + 2] + 30)})`]);
      }
    }
    // The river is redrawn in thin bands. Ripples are tighter near the far shore and longer close
    // up, like perspective on real water; each band's phases are fixed here, only time moves.
    const bands = [];
    if (wl) {
      const wy = wl * ih, hw = (ih - wy) / k, step = Math.max(1, Math.round(1.5 * k));
      for (let y = Math.ceil(wy) + step; y < ih; y += step) {
        const d = (y - wy) / (ih - wy);
        bands.push({ y, d, h: step, p1: hw * (0.6 * d - 0.24 * d * d), p2: hw * (0.15 * d - 0.05 * d * d), a1: k * (0.8 + 6 * d), a2: k * (0.5 + 3 * d), wob: k * (0.3 + 1.6 * d) });
      }
    }
    return { img, iw, ih, k, wl, wy: wl ? wl * ih : 0, focus, beacons: Array.isArray(meta.beacons) ? meta.beacons : [], lights, glints, bands };
  }

  function mount(host) {
    if (host.__skyline) return;
    host.__skyline = true;
    const canvas = document.createElement("canvas");
    canvas.className = "skyline-canvas"; canvas.setAttribute("aria-hidden", "true");
    host.prepend(canvas);
    const ctx = canvas.getContext("2d");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const seed = Number(host.dataset.seed || 7);
    const src = host.dataset.photo;
    let W = 0, H = 0, dpr = 1, layers = [], stars = [], mx = 0, my = 0, tx = 0, ty = 0, raf = 0, visible = true;
    // photo state: "loading" until it arrives, then "ready"; "none" without one or if it fails
    let photo = null, photoState = src ? "loading" : "none", photoAt = 0, photoBase = "night";
    const waitUntil = performance.now() + 900;   // give the photo a moment before showing the drawn city

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = host.clientWidth; H = host.clientHeight;
      if (!W || !H) return;
      canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + "px"; canvas.style.height = H + "px";
      layers = [];   // the drawn city is rebuilt lazily, only if it is shown
      const r = rng(seed + 11); stars = Array.from({ length: Math.floor(W / 8) }, () => [r() * W, r() * H * 0.5, r() * 1.1 + 0.2, r() * 6.28]);
    }

    function drawNight() {
      const g = ctx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, "#05070d"); g.addColorStop(0.6, "#0b1020"); g.addColorStop(1, "#06080e");
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }

    function drawScene(t) {
      if (!layers.length) layers = buildLayers(W, H, seed, dpr);
      const hz = H * HORIZON;
      const sky = ctx.createLinearGradient(0, 0, 0, hz);
      sky.addColorStop(0, "#04060c"); sky.addColorStop(0.45, "#0b1328"); sky.addColorStop(0.72, "#2b2442"); sky.addColorStop(0.9, "#8e4c3a"); sky.addColorStop(1, "#e39552");
      ctx.fillStyle = sky; ctx.fillRect(0, 0, W, hz + 2);
      const sun = ctx.createRadialGradient(W * 0.7 + tx * 12, hz, 4, W * 0.7, hz, W * 0.5);
      sun.addColorStop(0, "rgba(255,196,120,.6)"); sun.addColorStop(0.25, "rgba(236,124,82,.22)"); sun.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = sun; ctx.fillRect(0, 0, W, hz + 2);
      for (const [x, y, s, p] of stars) { ctx.globalAlpha = 0.2 + 0.35 * (0.5 + 0.5 * Math.sin(t * 0.8 + p)); ctx.fillStyle = "#cfd8ff"; ctx.fillRect(x, y, s, s); }
      ctx.globalAlpha = 1;
      if (!still) { const px = ((t * 24) % (W + 240)) - 120, py = H * 0.15 + Math.sin(t * 0.2) * 6;
        ctx.fillStyle = Math.sin(t * 6) > 0.6 ? "rgba(255,90,90,.95)" : "rgba(255,255,255,.55)"; ctx.beginPath(); ctx.arc(px, py, 1.4, 0, 6.28); ctx.fill(); }
      const drawLayer = (L, li) => {
        const ox = tx * 26 * L.par - L.PAD, oy = ty * 6 * L.par;
        ctx.drawImage(L.cv, 0, 0, L.cv.width, L.cv.height, ox, oy, L.cw, H);
        for (const [wx, wy, warm, a, ph, sp] of L.flicker) {
          if (!still && Math.sin(t * sp + ph) < -0.2) continue;
          ctx.fillStyle = warm ? `rgba(255,214,150,${a})` : `rgba(255,236,190,${a * 0.75})`; ctx.fillRect(wx + ox, wy + oy, 3, 4);
        }
        for (const [bx, by, ph] of L.beacons) {
          if (!still && Math.sin(t * 2.2 + ph) < 0.3) continue;
          ctx.fillStyle = "rgba(255,70,70,.95)"; ctx.shadowColor = "rgba(255,60,60,.9)"; ctx.shadowBlur = 10;
          ctx.beginPath(); ctx.arc(bx + ox, by + oy, 1.8, 0, 6.28); ctx.fill(); ctx.shadowBlur = 0;
        }
        const haze = ctx.createLinearGradient(0, L.base - H * 0.12, 0, L.base);
        haze.addColorStop(0, "rgba(30,26,44,0)"); haze.addColorStop(1, `rgba(70,46,58,${0.32 - li * 0.09})`);
        ctx.fillStyle = haze; ctx.fillRect(0, L.base - H * 0.12 + oy, W, H * 0.12);
      };
      layers.forEach(drawLayer);
      // river: the city's reflection, broken up by moving ripples
      const front = layers[2], rt = front.base + ty * 6 * front.par;
      const water = ctx.createLinearGradient(0, rt, 0, H); water.addColorStop(0, "#1b1a2a"); water.addColorStop(1, "#05070c");
      ctx.fillStyle = water; ctx.fillRect(0, rt, W, H - rt);
      ctx.save(); ctx.beginPath(); ctx.rect(0, rt, W, H - rt); ctx.clip();
      ctx.globalAlpha = 0.22; ctx.translate(0, rt * 2); ctx.scale(1, -1);
      ctx.drawImage(front.cv, 0, 0, front.cv.width, front.cv.height, tx * 26 * front.par - front.PAD, 0, front.cw, H);
      ctx.restore();
      const fade = ctx.createLinearGradient(0, rt, 0, H); fade.addColorStop(0, "rgba(8,9,16,.15)"); fade.addColorStop(0.5, "rgba(6,8,13,.75)"); fade.addColorStop(1, "rgba(5,7,12,.97)");
      ctx.fillStyle = fade; ctx.fillRect(0, rt, W, H - rt);
      for (let y = rt + 2; y < H; y += 3) {
        const k = (y - rt) / (H - rt);
        for (let x = 0; x < W; x += 16) {
          const s = Math.sin(x * 0.05 + y * 0.31 + t * 1.6) + Math.sin(x * 0.013 - t * 0.7 + y * 0.02);
          if (s > 1.05) { ctx.fillStyle = `rgba(255,${176 + (x % 60)},120,${0.26 * (1 - k)})`; ctx.fillRect(x + s * 4, y, 7 + s * 6, 1); }
        }
      }
      ctx.fillStyle = "rgba(255,190,130,.35)"; ctx.fillRect(0, rt, W, 1);   // the bright waterline
      const v = ctx.createRadialGradient(W / 2, H * 0.55, H * 0.3, W / 2, H * 0.55, Math.max(W, H) * 0.85);
      v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, "rgba(0,0,0,.6)");
      ctx.fillStyle = v; ctx.fillRect(0, 0, W, H);
    }

    function drawPhoto(t, alpha) {
      const P = photo, { img, iw, ih, k } = P, moving = !still;
      const focus = host.dataset.focus ? host.dataset.focus.split(",").map(Number) : P.focus;
      const kb = moving ? 1.06 + 0.05 * (0.5 - 0.5 * Math.cos((t * Math.PI * 2) / 64)) : 1.06;   // Ken Burns zoom
      const s = Math.max(W / iw, H / ih) * kb, dw = iw * s, dh = ih * s;
      const panX = moving ? Math.sin((t * Math.PI * 2) / 97) * 0.012 : 0, panY = moving ? Math.sin((t * Math.PI * 2) / 81) * 0.008 : 0;
      const scroll = moving ? Math.max(0, -host.getBoundingClientRect().top) * 0.22 : 0;   // the photo lags the page a little
      let ox = W / 2 - (focus[0] + panX) * dw - tx * 22, oy = H / 2 - (focus[1] + panY) * dh - ty * 12 + scroll;
      ox = Math.min(0, Math.max(W - dw, ox)); oy = Math.min(0, Math.max(H - dh, oy));
      const px = (cssPx) => cssPx / s;   // a size on screen, in photo pixels
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.setTransform(dpr * s, 0, 0, dpr * s, dpr * ox, dpr * oy);
      ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, 0, 0);
      const off = [];
      if (moving && P.bands.length) {
        // the river: each thin band of the reflection is pushed sideways by the ripples
        for (const b of P.bands) {
          const o = b.a1 * Math.sin(b.p1 - t * 1.8) + b.a2 * Math.sin(b.p2 + t * 0.9);
          const sy = Math.min(ih - b.h, Math.max(P.wy, b.y + b.wob * Math.sin(b.p1 * 0.5 + t * 1.3)));
          ctx.drawImage(img, 0, sy, iw, b.h, o, b.y, iw, b.h);
          off.push(o);
        }
      }
      ctx.globalAlpha = 1;
      const S = sprites();
      ctx.globalCompositeOperation = "lighter";
      if (moving && alpha >= 1) {
        // glints: sparkles running along the bright parts of the reflection
        const span = ih - P.wy, step = P.bands.length ? P.bands[0].h : 1;
        for (const [gx, gy, b, col] of P.glints) {
          const d = (gy - P.wy) / span, hw = span / k;
          const q = Math.sin(gx / k * 0.11 + hw * 0.5 * d - t * 1.3) + Math.sin(gx / k * 0.021 + t * 0.55 + hw * 0.2 * d);
          if (q < 1.25) continue;
          const a = ((q - 1.25) / 0.75) * b, bi = Math.max(0, Math.min(off.length - 1, Math.floor((gy - P.wy) / step) - 1));
          ctx.globalAlpha = Math.min(0.75, a * 0.7); ctx.fillStyle = col;
          ctx.fillRect(gx + (off[bi] || 0) - k * (2 + 5 * a) * (0.6 + d), gy, k * (4 + 10 * a) * (0.6 + d), k * (0.6 + 0.9 * d));
        }
        // the city's own lights, twinkling
        for (const l of P.lights) {
          const q = Math.sin(t * l.sp + l.ph);
          if (q < 0.8) continue;
          const a = Math.pow((q - 0.8) / 0.2, 2), size = px(l.big ? 30 : 13);
          ctx.globalAlpha = a * (l.big ? 0.9 : 0.75);
          ctx.drawImage(S[l.tint][l.big ? "star" : "glow"], l.x - size / 2, l.y - size / 2, size, size);
        }
        // a plane crossing high over the city, every minute and a half
        const pp = (t / 90) % 1, plx = iw * (1.08 - pp * 1.16), ply = ih * (0.085 + 0.01 * Math.sin(t * 0.07));
        if (plx > 0 && plx < iw) {
          const strobe = (t % 1.3) < 0.09, sz = px(strobe ? 12 : 6);
          ctx.globalAlpha = strobe ? 0.95 : 0.6;
          ctx.drawImage(S[strobe ? "white" : "red"].glow, plx - sz / 2, ply - sz / 2, sz, sz);
        }
      }
      // aviation beacons on the tallest towers
      P.beacons.forEach(([u, v], i) => {
        const p = (t * 0.62 + i * 0.37) % 1, a = moving ? (p < 0.24 ? Math.sin((p / 0.24) * Math.PI) : 0) : 0.8;
        if (a <= 0) return;
        const sz = px(20);
        ctx.globalAlpha = a * alpha; ctx.drawImage(S.red.glow, u * iw - sz / 2, v * ih - sz / 2, sz, sz);
      });
      ctx.restore();
      const vg = ctx.createRadialGradient(W / 2, H * 0.5, Math.min(W, H) * 0.35, W / 2, H * 0.5, Math.max(W, H) * 0.8);
      vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, `rgba(0,0,0,${0.38 * alpha})`);
      ctx.fillStyle = vg; ctx.fillRect(0, 0, W, H);
    }

    function frame(ms) {
      if (!host.isConnected) { visible = false; return; }   // re-observed when the page puts it back
      if (!W || !H) { if (!still) raf = requestAnimationFrame(frame); return; }
      const t = ms / 1000;
      tx += (mx - tx) * 0.04; ty += (my - ty) * 0.04;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const fade = photoState === "ready" ? (still ? 1 : Math.min(1, (ms - photoAt) / 1400)) : 0;
      if (fade < 1) {
        const showScene = photoState === "none" || (photoState === "loading" ? ms >= waitUntil : photoBase === "scene");
        if (showScene) drawScene(t); else drawNight();
      }
      if (fade > 0) drawPhoto(t, Math.max(0, fade));
      if (!still && visible) raf = requestAnimationFrame(frame);
    }

    resize();
    // Resize with the host (also covers a host that is mounted before it is on the page).
    let rz; new ResizeObserver(() => { clearTimeout(rz); rz = setTimeout(() => { if (host.clientWidth && (host.clientWidth !== W || host.clientHeight !== H)) { resize(); if (still) frame(performance.now()); } }, 80); }).observe(host);
    host.addEventListener("pointermove", (e) => { const b = host.getBoundingClientRect(); mx = (e.clientX - b.left) / b.width - 0.5; my = (e.clientY - b.top) / b.height - 0.5; });
    new IntersectionObserver(([en]) => { visible = en.isIntersecting; cancelAnimationFrame(raf); if (visible) raf = requestAnimationFrame(frame); }).observe(host);
    if (still) frame(performance.now()); else raf = requestAnimationFrame(frame);
    if (src) {
      const img = new Image(); img.decoding = "async";
      const meta = fetch(src.replace(/\.[a-z0-9]+$/i, ".json"), { cache: "no-cache" }).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
      const ready = () => meta.then((m) => {
        photo = preparePhoto(img, m || {});
        photoBase = performance.now() < waitUntil ? "night" : "scene";
        photoState = "ready"; photoAt = performance.now(); layers = [];
        if (still) frame(performance.now());
      });
      const fail = () => { photoState = "none"; if (still) frame(performance.now()); };
      img.onload = () => (img.decode ? img.decode().catch(() => {}) : Promise.resolve()).then(ready, fail);
      img.onerror = fail;
      img.src = src;
    }
  }

  window.ArmariuSkyline = { mount };
})();
