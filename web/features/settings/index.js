import { $, el } from "../../shared/dom.js";
import { openSheet, closeSheet, createSheetAction } from "../../shared/sheet.js";
import { STORAGE_KEYS } from "../../shared/storage.js";

export function createSettingsFeature({ store, act, banner, flush, pendingCount, refresh, clearToken, getSyncStatus = () => null }) {


  function settingsSheet() {
    openSheet("Settings", (host) => {
      const body = el("div"); host.append(body);
      const submit = createSheetAction(body, { act, onError: (error) => banner(error.message || "That did not work", "error", 0) });
      // Day start is a SHARED setting: changing it here changes it on the Mac too,
      // because it describes the data rather than this device. Theme and graph
      // preferences stay per-machine and deliberately are not offered here.
      const dayRow = el("div", "set-row");
      const dayLabel = el("label", "k", "Day starts at");
      dayLabel.htmlFor = "settings-day-start";
      const dayInput = Object.assign(document.createElement("input"), {
        id: "settings-day-start", type: "time", step: "60", required: true,
        value: store.context?.day_start || store.snapshot?.settings?.day_start_time || "03:00",
      });
      const saveDay = el("button", "primary wide", "Save day start");
      saveDay.id = "settings-day-save";
      saveDay.onclick = async () => {
        if (!submit.canSubmit() || !dayInput.reportValidity()) return;
        const result = await submit("set_setting", { key: "day_start_time", value: dayInput.value });
        if (result?.ok && body.isConnected) banner("Day start updated — shared with your Mac", "ok");
      };
      dayRow.append(dayLabel, dayInput);
      body.append(dayRow, saveDay);
      body.append(el("p", "set-note",
        "Late-night work counts towards the day it started. Shared with your Mac."));

      let waiting;
      try { waiting = String(pendingCount()); } catch { waiting = "Cannot read saved changes"; }
      const sync = getSyncStatus();
      const info = [
        ["Today (logical)", store.context?.today || "—"],
        ["Server time", (store.context?.server_time || "—").replace("T", " ")],
        ["Subjects", String((store.snapshot?.subjects || []).filter((x) => !x.is_archived).length)],
        ["Sessions stored", String((store.snapshot?.sessions || []).length)],
        ["Goals", String((store.snapshot?.goals || []).length)],
        ["Waiting to send", waiting],
      ];
      if (sync) {
        info.unshift(["Connection", sync.label], ["Last successful sync", sync.lastSuccessAt ? new Date(sync.lastSuccessAt).toLocaleString() : "No confirmed sync yet"]);
        if (sync.error) info.splice(2, 0, ["Sync attention", sync.error]);
      }
      for (const [key, value] of info) {
        const row = el("div", "set-row");
        row.append(el("div", "k", key), el("div", "v", value));
        body.append(row);
      }

      const retry = el("button", "secondary wide", "Send anything waiting");
      let retrying = false;
      retry.onclick = async () => {
        if (retrying || !submit.canSubmit()) return;
        retrying = true; retry.disabled = true;
        try {
          const outcome = await flush();
          if (!body.isConnected) return;
          banner(outcome.ok
            ? (outcome.sent ? `Sent ${outcome.sent} change(s)` : "Nothing waiting")
            : `Changes kept: ${outcome.error || "waiting for connection"}`, outcome.ok ? "ok" : "error");
          closeSheet();
          await refresh({ quiet: true });
        } catch (error) {
          if (body.isConnected) banner(error.message || "Could not send saved changes. They are kept.", "error", 0);
        } finally { retrying = false; retry.disabled = false; }
      };
      body.append(retry);

      const icons = el("a", "secondary wide settings-icons", "Choose Home Screen icon");
      icons.href = "/icons/index.html";
      icons.target = "_blank";
      icons.rel = "noopener";
      body.append(icons);
      body.append(el("p", "set-note", "Open the gallery in Safari to add a Home Screen icon. Let waiting changes sync before replacing an existing Home Screen app."));

      const reload = el("button", "secondary wide", "Reload app");
      reload.onclick = () => location.reload();
      body.append(reload);

      const out = el("button", "danger wide", "Disconnect this phone");
      let disconnecting = false;
      out.onclick = () => {
        if (disconnecting || !submit.canSubmit()) return;
        try {
          if (pendingCount()) return banner("Send the waiting changes before disconnecting this phone.", "error", 0);
        } catch (error) { return banner(error.message || "Cannot read saved changes. Keep this phone connected.", "error", 0); }
        if (!confirm("Remove the token from this phone? Your data stays on the server.")) return;
        disconnecting = true;
        try {
          if (pendingCount()) {
            disconnecting = false;
            return banner("Send the waiting changes before disconnecting this phone.", "error", 0);
          }
          clearToken();
        } catch (error) {
          disconnecting = false;
          return banner(error.message || "Could not disconnect this phone.", "error", 0);
        }
        // Authentication is already removed. Reload even if an optional cache
        // cleanup fails, so private authenticated DOM is no longer displayed.
        try {
          localStorage.removeItem(STORAGE_KEYS.mirror);
          localStorage.removeItem(STORAGE_KEYS.weightCache);
        } catch { /* credentials are removed; account-scoped caches stay private */ }
        location.reload();
      };
      body.append(out);
    });
  }

  return { open: settingsSheet };
}
