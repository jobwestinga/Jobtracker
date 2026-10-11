import { $, el } from "../../shared/dom.js";
import { pad } from "../../shared/time.js";
import { openSheet, closeSheet } from "../../shared/sheet.js";
import { STORAGE_KEYS } from "../../shared/storage.js";

export function createSettingsFeature({ store, act, banner, flush, pendingCount, refresh, clearToken }) {


  function settingsSheet() {
    openSheet("Settings", (body) => {
      // Day start is a SHARED setting: changing it here changes it on the Mac too,
      // because it describes the data rather than this device. Theme and graph
      // preferences stay per-machine and deliberately are not offered here.
      const dayRow = el("div", "set-row");
      dayRow.append(el("div", "k", "Day starts at"));
      const daySelect = document.createElement("select");
      for (let hour = 0; hour < 24; hour += 1) {
        const option = el("option", null, `${pad(hour)}:00`);
        option.value = `${pad(hour)}:00`;
        if (option.value === store.context?.day_start) option.selected = true;
        daySelect.append(option);
      }
      daySelect.onchange = async () => {
        closeSheet();
        const result = await act("set_setting", { key: "day_start_time", value: daySelect.value });
        if (result.ok) banner("Day start updated — shared with your Mac", "ok");
      };
      dayRow.append(daySelect);
      body.append(dayRow);
      body.append(el("p", "set-note",
        "Late-night work counts towards the day it started. Shared with your Mac."));

      const info = [
        ["Today (logical)", store.context?.today || "—"],
        ["Server time", (store.context?.server_time || "—").replace("T", " ")],
        ["Subjects", String((store.snapshot?.subjects || []).filter((x) => !x.is_archived).length)],
        ["Sessions stored", String((store.snapshot?.sessions || []).length)],
        ["Goals", String((store.snapshot?.goals || []).length)],
        ["Waiting to send", String(pendingCount())],
      ];
      for (const [key, value] of info) {
        const row = el("div", "set-row");
        row.append(el("div", "k", key), el("div", "v", value));
        body.append(row);
      }

      const retry = el("button", "secondary wide", "Send anything waiting");
      retry.onclick = async () => {
        const outcome = await flush();
        banner(outcome.ok
          ? (outcome.sent ? `Sent ${outcome.sent} change(s)` : "Nothing waiting")
          : `Changes kept: ${outcome.error || "waiting for connection"}`, outcome.ok ? "ok" : "error");
        closeSheet();
        refresh({ quiet: true });
      };
      body.append(retry);

      const reload = el("button", "secondary wide", "Reload app");
      reload.onclick = () => location.reload();
      body.append(reload);

      const out = el("button", "danger wide", "Disconnect this phone");
      out.onclick = () => {
        if (pendingCount()) return banner("Send the waiting changes before disconnecting this phone.", "error", 0);
        if (!confirm("Remove the token from this phone? Your data stays on the server.")) return;
        localStorage.removeItem(STORAGE_KEYS.mirror);
        localStorage.removeItem(STORAGE_KEYS.weightCache);
        clearToken();
        location.reload();
      };
      body.append(out);
    });
  }

  return { open: settingsSheet };
}
