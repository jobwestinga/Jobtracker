export const pad = (n) => String(n).padStart(2, "0");

export function addDays(iso, delta) {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + delta);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function localIso(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T` +
         `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

export const localDate = (date = new Date()) => localIso(date).slice(0, 10);
