/* Armariu skyline: a living City-of-London-at-dusk scene drawn on a canvas.
   Seeded, so it looks the same on every load. Building layers are pre-rendered once per resize;
   each frame only adds the moving parts: flickering windows, blinking aviation lights, a plane,
   the city's reflection shimmering on the river, and pointer parallax. Honors
   prefers-reduced-motion by painting one still frame. If the host has data-photo and that image
   exists (e.g. static/img/hero.jpg), a real photograph fades in over the scene with a slow
   Ken Burns move, and the scene stays underneath as the fallback. */
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

  function mount(host) {
    if (host.__skyline) return;
    host.__skyline = true;
    const canvas = document.createElement("canvas");
    canvas.className = "skyline-canvas"; canvas.setAttribute("aria-hidden", "true");
    host.prepend(canvas);
    const ctx = canvas.getContext("2d");
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const seed = Number(host.dataset.seed || 7);
    let W = 0, H = 0, dpr = 1, layers = [], stars = [], mx = 0, my = 0, tx = 0, ty = 0, raf = 0, visible = true;

    function resize() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = host.clientWidth; H = host.clientHeight;
      if (!W || !H) return;
      canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + "px"; canvas.style.height = H + "px";
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      layers = buildLayers(W, H, seed, dpr);
      const r = rng(seed + 11); stars = Array.from({ length: Math.floor(W / 8) }, () => [r() * W, r() * H * 0.5, r() * 1.1 + 0.2, r() * 6.28]);
    }

    function frame(ms) {
      if (!host.isConnected) { visible = false; return; }   // re-observed when the page puts it back
      if (!W || !layers.length || !layers[0].cv.width) { if (!still) raf = requestAnimationFrame(frame); return; }
      const t = ms / 1000;
      tx += (mx - tx) * 0.04; ty += (my - ty) * 0.04;
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
      if (!still && visible) raf = requestAnimationFrame(frame);
    }

    resize();
    // Resize with the host (also covers a host that is mounted before it is on the page).
    let rz; new ResizeObserver(() => { clearTimeout(rz); rz = setTimeout(() => { if (host.clientWidth && (host.clientWidth !== W || host.clientHeight !== H)) { resize(); if (still) frame(0); } }, 80); }).observe(host);
    host.addEventListener("pointermove", (e) => { const b = host.getBoundingClientRect(); mx = (e.clientX - b.left) / b.width - 0.5; my = (e.clientY - b.top) / b.height - 0.5; });
    new IntersectionObserver(([en]) => { visible = en.isIntersecting; cancelAnimationFrame(raf); if (visible) raf = requestAnimationFrame(frame); }).observe(host);
    raf = requestAnimationFrame(frame);
    const src = host.dataset.photo;
    if (src) {
      const img = new Image();
      img.onload = () => { const d = document.createElement("div"); d.className = "hero-photo"; d.style.backgroundImage = `url("${src}")`; canvas.after(d); host.classList.add("has-photo"); requestAnimationFrame(() => d.classList.add("in")); };
      img.src = src;
    }
  }

  window.ArmariuSkyline = { mount };
})();
