export const WEIGHT_PROJECTED_OPERATIONS = Object.freeze(["upsert_weight", "delete_weight"]);

export function projectWeightOperation(rows, entry) {
  if (entry.op === "delete_weight") return rows.filter((r) => r.uid !== entry.params.weight_uid);
  if (entry.op !== "upsert_weight") return rows;
  const p = entry.params;
  let match = rows.find((r) => r.uid === (p.weight_uid || p.uid));
  if (!match && !p.weight_uid) match = rows.findLast((r) => r.source === "manual" && r.measured_at.slice(0, 10) === p.measured_at.slice(0, 10));
  if (!match && p.weight_uid) return rows;
  const measuredAt = p.measured_at.length === 10 ? `${p.measured_at}T00:00:00` : p.measured_at;
  const updated = { ...match, uid: match?.uid || p.uid, measured_at: measuredAt, weight_kg: p.weight_kg, source: match?.source || "manual", pending: true };
  return [...rows.filter((r) => r !== match), updated].sort((a, b) => a.measured_at.localeCompare(b.measured_at));
}
