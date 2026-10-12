export const WEIGHT_PROJECTED_OPERATIONS = Object.freeze(["upsert_weight", "delete_weight"]);

/** Pending create IDs can become aliases of an existing daily server row. */
export function findWeightMeasurement(rows, uid) {
  if (!uid) return undefined;
  return rows.find((row) => row.uid === uid || (Array.isArray(row.pending_uids) && row.pending_uids.includes(uid)));
}

const identities = (row) => row ? [row.uid, ...(Array.isArray(row.pending_uids) ? row.pending_uids : [])] : [];

/** Alias rows are optional on older servers and are never separate weighings. */
export function withWeightAliases(rows, aliases = []) {
  const byUid = new Map();
  for (const alias of Array.isArray(aliases) ? aliases : []) {
    if (typeof alias?.alias_uid !== "string" || typeof alias.weight_uid !== "string") continue;
    if (!byUid.has(alias.weight_uid)) byUid.set(alias.weight_uid, []);
    byUid.get(alias.weight_uid).push(alias.alias_uid);
  }
  return rows.map((row) => byUid.has(row.uid) ?
    { ...row, pending_uids: [...new Set([...identities(row).slice(1), ...byUid.get(row.uid)])] } : row);
}

export function projectWeightOperation(rows, entry) {
  if (entry.op === "delete_weight") {
    const match = findWeightMeasurement(rows, entry.params.weight_uid);
    return rows.filter((row) => row !== match);
  }
  if (entry.op !== "upsert_weight") return rows;
  const p = entry.params;
  let match = findWeightMeasurement(rows, p.weight_uid || p.uid);
  if (!match && !p.weight_uid) match = rows.findLast((r) => r.source === "manual" && r.measured_at.slice(0, 10) === p.measured_at.slice(0, 10));
  if (!match && p.weight_uid) return rows;
  const source = match;
  if (match?.source === "manual" && match.measured_at.slice(0, 10) !== p.measured_at.slice(0, 10)) {
    // Match server date-correction coalescing without discarding either side's
    // pending aliases. Later queued edits/deletions retain their captured IDs.
    match = rows.findLast((row) => row !== source && row.source === "manual" &&
      row.measured_at.slice(0, 10) === p.measured_at.slice(0, 10)) || match;
  }
  const measuredAt = p.measured_at.length === 10 ? `${p.measured_at}T00:00:00` : p.measured_at;
  const updated = { ...match, uid: match?.uid || p.uid, measured_at: measuredAt, weight_kg: p.weight_kg, source: match?.source || "manual", pending: true };
  const aliases = [...new Set([...identities(source), ...identities(match), p.uid].filter((uid) => uid && uid !== updated.uid))];
  if (aliases.length) updated.pending_uids = aliases;
  return [...rows.filter((row) => row !== source && row !== match), updated].sort((a, b) => a.measured_at.localeCompare(b.measured_at));
}
