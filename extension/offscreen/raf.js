// Offscreen documents never paint, so requestAnimationFrame callbacks never run.
// pdf.js yields to rAF while rendering long pages, which would hang the scan forever.
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
