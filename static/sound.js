"use strict";
// Table sounds, synthesised with the Web Audio API so there are no audio files to ship.

const Sound = (() => {
  const MUTE_KEY = "aitable.muted";
  let muted = localStorage.getItem(MUTE_KEY) === "1";
  let ctx = null, out = null, noise = null;
  const rand = (lo, hi) => lo + Math.random() * (hi - lo);

  // The audio context, or null while muted or before the browser allows sound
  // (it needs a click or a key press first). Sounds asked for meanwhile are dropped
  // instead of queued, so they can't all go off at once later.
  function ready() {
    if (muted) return null;
    if (!ctx) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return null;
      ctx = new AudioCtx();
      out = ctx.createDynamicsCompressor();  // keeps a pile of overlapping chips from clipping
      const master = ctx.createGain();
      master.gain.value = 0.8;
      out.connect(master).connect(ctx.destination);
      noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
      const samples = noise.getChannelData(0);
      for (let i = 0; i < samples.length; i++) samples[i] = Math.random() * 2 - 1;
    }
    if (ctx.state !== "running") {
      ctx.resume().catch(() => {});
      return null;
    }
    return ctx;
  }
  for (const ev of ["pointerdown", "keydown"]) window.addEventListener(ev, ready, true);

  // ---- building blocks ----------------------------------------------------
  // A burst of filtered noise: paper, felt, and the click at the front of a chip.
  function hiss(t, { dur, from, to = from, type = "bandpass", q = 1, gain, attack = 0.004 }) {
    const src = ctx.createBufferSource();
    src.buffer = noise;
    src.loop = true;
    const filter = ctx.createBiquadFilter();
    filter.type = type;
    filter.Q.value = q;
    filter.frequency.setValueAtTime(from, t);
    filter.frequency.exponentialRampToValueAtTime(to, t + dur);
    const amp = ctx.createGain();
    amp.gain.setValueAtTime(0.0001, t);
    amp.gain.exponentialRampToValueAtTime(gain, t + attack);
    amp.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(filter).connect(amp).connect(out);
    src.start(t, Math.random() * 0.9);
    src.stop(t + dur + 0.02);
  }

  // A tone that starts hard and dies away: the ring of a chip, a knuckle on the table.
  function tone(t, { freq, to = freq, dur, gain, type = "sine" }) {
    const osc = ctx.createOscillator();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    osc.frequency.exponentialRampToValueAtTime(to, t + dur);
    const amp = ctx.createGain();
    amp.gain.setValueAtTime(gain, t);
    amp.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(amp).connect(out);
    osc.start(t);
    osc.stop(t + dur + 0.02);
  }

  // ---- single sounds --------------------------------------------------------
  // One card: it slides off the deck, then snaps flat on the felt.
  function card(t, gain) {
    const pitch = rand(0.85, 1.2);
    hiss(t, { dur: 0.11, from: 1800 * pitch, to: 5200 * pitch, q: 0.8, gain: 0.5 * gain, attack: 0.025 });
    hiss(t + 0.075, { dur: 0.05, type: "lowpass", from: 2400, to: 500, gain: 0.55 * gain, attack: 0.002 });
    tone(t + 0.075, { freq: 170, to: 70, dur: 0.06, gain: 0.22 * gain });
  }

  // One chip landing on another: a sharp click with a short, slightly out-of-tune ring.
  function clack(t, gain, pitch = 1) {
    const base = rand(2300, 3100) * pitch;
    hiss(t, { dur: 0.012, type: "highpass", from: 4500, gain: 0.5 * gain, attack: 0.001 });
    tone(t, { freq: base, dur: 0.05, gain: 0.3 * gain, type: "triangle" });
    tone(t, { freq: base * 1.52, dur: 0.035, gain: 0.2 * gain, type: "triangle" });
    tone(t, { freq: base * 2.31, dur: 0.025, gain: 0.12 * gain, type: "triangle" });
  }

  // Chips or cards dragged across the felt.
  function slide(t, gain, from, to, dur) {
    hiss(t, { dur, from, to, q: 0.7, gain: 0.16 * gain, attack: dur * 0.3 });
  }

  // Schedule a sound `delay` seconds from now, if sound is available.
  function play(delay, build) {
    if (ready()) build(ctx.currentTime + 0.01 + delay);
  }

  // ---- what the table calls ---------------------------------------------------
  // `size` is 0-4, how big the pile of chips is. `gain` is 0-1. `delay` is in seconds.
  return {
    get muted() { return muted; },
    setMuted(value) {
      muted = value;
      localStorage.setItem(MUTE_KEY, value ? "1" : "0");
      if (!muted) ready();
    },

    // `count` cards dealt one after another, `gap` seconds apart.
    cards(count, gap = 0.09) {
      play(0, (t) => { for (let i = 0; i < count; i++) card(t + i * gap, i ? 0.85 : 1); });
    },

    // A hand thrown away face down.
    fold(gain = 1) {
      play(0, (t) => slide(t, gain * 1.4, 1500, 600, 0.22));
    },

    // A check: two knuckle taps on the table.
    knock(gain = 1) {
      play(0, (t) => {
        for (const at of [t, t + 0.12]) {
          tone(at, { freq: 190, to: 90, dur: 0.07, gain: 0.5 * gain });
          hiss(at, { dur: 0.03, type: "lowpass", from: 900, gain: 0.3 * gain, attack: 0.001 });
        }
      });
    },

    // A single chip, for the bet buttons.
    chip(gain = 0.6) {
      play(0, (t) => clack(t, gain));
    },

    // Chips leaving a stack: tossed onto the felt as a bet.
    chipsBet(size = 1, gain = 1, delay = 0) {
      play(delay, (t) => {
        hiss(t, { dur: 0.04, type: "lowpass", from: 700, gain: 0.3 * gain, attack: 0.002 });
        for (let i = 0; i < 2 + size; i++) {
          clack(t, gain * rand(0.7, 1));
          t += rand(0.035, 0.075);
        }
      });
    },

    // Chips coming in: a pile raked over and stacked up, faster and brighter as it goes.
    chipsWin(size = 1, gain = 1, delay = 0) {
      play(delay, (t) => {
        const count = 6 + 2 * size;
        slide(t, gain, 700, 1600, 0.45);
        t += 0.12;
        for (let i = 0; i < count; i++) {
          const along = i / (count - 1);
          clack(t, gain * rand(0.6, 0.9), 0.9 + 0.3 * along);
          t += 0.085 - 0.05 * along;
        }
      });
    },

    // Chips lost: the dealer sweeps the bet away, with a few dull knocks as it goes.
    chipsLose(gain = 1, delay = 0) {
      play(delay, (t) => {
        slide(t, gain * 1.3, 1500, 450, 0.4);
        [0.75, 0.66, 0.58].forEach((pitch, i) => clack(t + 0.05 + i * 0.09, gain * 0.5, pitch));
      });
    },

    // Bets pushed into the pot at the end of a betting round.
    chipsSlide(gain = 1) {
      play(0, (t) => {
        slide(t, gain, 900, 1300, 0.3);
        clack(t + 0.16, gain * 0.5);
        clack(t + 0.22, gain * 0.4);
      });
    },
  };
})();
