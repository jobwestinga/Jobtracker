// Coordinate durable writes and their display without changing retry identity.
export function createActionRunner({ send, project, render, refresh, banner, onMutation }) {
  return async function act(op, params, options = {}) {
    onMutation();
    if (options.optimistic) {
      try { options.optimistic(); render(); } catch { /* guesses never block a write */ }
    }
    let outcome;
    try {
      outcome = await send(op, params, options.uid ? { uid: options.uid } : {});
      if (outcome.queued) {
        project();
        render();
        banner(outcome.offline ? "Saved on this phone — waiting for connection" :
          `Saved on this phone — ${outcome.error || "waiting to sync"}`, outcome.offline ? "ok" : "error", 0);
        return outcome;
      }
      if (options.afterAcknowledged) await options.afterAcknowledged();
      await refresh({ quiet: true });
      if (outcome.result?.reason) banner(outcome.result.reason, "error", 0);
      return outcome;
    } catch (error) {
      try { project(); render(); } catch { /* preserve the visible last state */ }
      try { banner(error.message || "That did not work", "error", 0); } catch { /* display may be unavailable */ }
      // send rejects only before durability. Once it returns, a display/cache
      // failure must never invite retrying a saved operation with a fresh UID.
      return outcome ?? { ok: false };
    }
  };
}
