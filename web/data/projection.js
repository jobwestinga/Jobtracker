// Queued edits are display-only. Server acknowledgements still determine
// durations, logical days, completion gates and timer ownership.
const TABLES = ["subjects", "goals", "milestones", "sessions", "templates"];
const timerFor = (p) => ({ uid: p.uid, session_uid: p.uid, subject_uid: p.subject_uid,
  start_time: p.start_time, elapsed_seconds: 0, timer_origin: new Date(p.start_time).getTime(), pending: true });

// A named registry also lets operation-contract checks distinguish deliberate
// server-only behavior from an accidentally missing phone projection.
const PROJECTORS = {
  start_subject: (view, p) => {
    if (!view.timer || view.timer.uid === p.uid) view.timer = timerFor(p);
  },
  switch_subject: (view, p) => {
    if (!p.source_session_uid || view.timer?.uid === p.source_session_uid || view.timer?.session_uid === p.source_session_uid) view.timer = timerFor(p);
  },
  stop_active_subject: (view, p) => {
    if (!p.session_uid || view.timer?.uid === p.session_uid || view.timer?.session_uid === p.session_uid) view.timer = null;
  },
  add_subject: (view, p) => view.add("subjects", { is_archived: 0, sort_order: view.result.subjects.length, ...p }),
  update_subject: (view, p) => view.patch("subjects", p.subject_uid, { name: p.name, color: p.color, notes: p.notes }),
  archive_subject: (view, p) => view.patch("subjects", p.subject_uid, { is_archived: 1 }),
  unarchive_subject: (view, p) => view.patch("subjects", p.subject_uid, { is_archived: 0 }),
  add_goal: (view, p) => view.add("goals", { is_completed: 0, sort_order: -1, ...p }),
  update_goal: (view, p) => view.patch("goals", p.goal_uid, { name: p.name, notes: p.notes, deadline: p.deadline }),
  complete_goal: (view, p) => view.patch("goals", p.goal_uid, { is_completed: 1 }),
  uncomplete_goal: (view, p) => view.patch("goals", p.goal_uid, { is_completed: 0 }),
  toggle_goal_focused: (view, p) => {
    const row = view.result.goals.find((item) => item.uid === p.goal_uid);
    if (row) view.patch("goals", p.goal_uid, { is_focused: row.is_focused ? 0 : 1 });
  },
  delete_goal: (view, p) => {
    view.remove("goals", p.goal_uid);
    view.result.milestones = view.result.milestones.filter((row) => row.goal_uid !== p.goal_uid);
  },
  add_milestone: (view, p) => view.add("milestones", { is_done: 0, ...p }),
  update_milestone: (view, p) => view.patch("milestones", p.milestone_uid, { title: p.title, note: p.note }),
  set_milestone_done: (view, p) => view.patch("milestones", p.milestone_uid, { is_done: p.done ? 1 : 0 }),
  delete_milestone: (view, p) => view.remove("milestones", p.milestone_uid),
  add_template: (view, p) => view.add("templates", { is_active: 1, ...p, milestones_json: JSON.stringify(p.milestone_titles || []) }),
  update_template: (view, p) => view.patch("templates", p.template_uid, { title: p.title, notes: p.notes,
    recurrence: p.recurrence, recurrence_day: p.recurrence_day, milestones_json: JSON.stringify(p.milestone_titles || []) }),
  set_template_active: (view, p) => view.patch("templates", p.template_uid, { is_active: p.active ? 1 : 0 }),
  delete_template: (view, p) => view.remove("templates", p.template_uid),
  set_subject_order: (view, p) => (p.subject_uids || []).forEach((uid, index) => view.patch("subjects", uid, { sort_order: index })),
  set_goal_order: (view, p) => (p.goal_uids || []).forEach((uid, index) => view.patch("goals", uid, { sort_order: index })),
};
export const PROJECTED_OPERATIONS = Object.freeze(Object.keys(PROJECTORS));
export const UNPROJECTED_OPERATIONS = Object.freeze({
  delete_subject: "Server owns cascading session deletion; the phone offers archive instead.",
  add_session: "Persisted sessions and day totals wait for server validation and logical-day attribution.",
  update_session: "Session duration and day attribution stay authoritative.",
  delete_session: "Day lists reload after the server acknowledges deletion.",
  duplicate_session: "Server chooses the destination logical day and validates the copy.",
  shift_session: "Server validates shifted timestamps and recalculates duration.",
  set_milestone_order: "The phone has no milestone ordering controls.",
  heartbeat: "Heartbeat metadata does not change the displayed timer.",
  resolve_recovery: "Recovery is a desktop/server interaction; queued stops use exact timer identity.",
  generate_due_goals: "Server owns recurrence scheduling and occurrence identity.",
  set_setting: "Logical-day changes wait for the shared server context.",
  import_weights: "Bulk import is an external adapter; the phone fetches the resulting history.",
  import_backup: "Server owns restore, UID merging and authoritative replacement.",
});

export function applyChanges(snapshot, changes, settings = {}) {
  const result = { ...snapshot, settings: { ...snapshot.settings, ...settings } };
  for (const table of TABLES) {
    const edits = changes.filter((change) => change.table === table);
    if (!edits.length) continue;
    const rows = new Map((snapshot[table] || []).map((row) => [row.uid, row]));
    for (const change of edits) {
      if (change.op === "delete") rows.delete(change.uid);
      else if (change.row) rows.set(change.uid, change.row);
    }
    result[table] = [...rows.values()];
  }
  return result;
}

export function projectPending(snapshot, active, operations) {
  const result = { ...snapshot };
  for (const table of TABLES) result[table] = (snapshot?.[table] || []).map((row) => ({ ...row }));
  const view = {
    result, timer: active ? { ...active } : null,
    patch(table, uid, values) {
      const row = result[table].find((item) => item.uid === uid);
      if (row) Object.assign(row, values, { pending: true });
    },
    add(table, values) {
      if (values.uid && !result[table].some((item) => item.uid === values.uid)) result[table].push({ ...values, pending: true });
    },
    remove(table, uid) { result[table] = result[table].filter((row) => row.uid !== uid); },
  };
  for (const { op, params } of operations) {
    if (Object.hasOwn(PROJECTORS, op)) PROJECTORS[op](view, params);
  }
  return { snapshot: result, active: view.timer };
}
