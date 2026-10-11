// Projection of queued edits for display only. The server still decides every
// completion, duration, logical day, and timer rule when these operations sync.
const TABLES = ["subjects", "goals", "milestones", "sessions", "templates"];

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
  let timer = active ? { ...active } : null;
  const patch = (table, uid, values) => {
    const row = result[table].find((item) => item.uid === uid);
    if (row) Object.assign(row, values, { pending: true });
  };
  const add = (table, values) => {
    if (values.uid && !result[table].some((item) => item.uid === values.uid)) {
      result[table].push({ ...values, pending: true });
    }
  };
  const remove = (table, uid) => { result[table] = result[table].filter((row) => row.uid !== uid); };
  const openTimer = (p) => ({ uid: p.uid, session_uid: p.uid, subject_uid: p.subject_uid,
    start_time: p.start_time, elapsed_seconds: 0, timer_origin: new Date(p.start_time).getTime(), pending: true });
  for (const { op, params: p } of operations) {
    if (op === "start_subject") {
      if (!timer || timer.uid === p.uid) timer = openTimer(p);
    } else if (op === "switch_subject") {
      if (!p.source_session_uid || timer?.uid === p.source_session_uid || timer?.session_uid === p.source_session_uid) {
        timer = openTimer(p);
      }
    } else if (op === "stop_active_subject") {
      if (!p.session_uid || timer?.uid === p.session_uid || timer?.session_uid === p.session_uid) timer = null;
    } else if (op === "add_subject") add("subjects", { is_archived: 0, sort_order: result.subjects.length, ...p });
    else if (op === "update_subject") patch("subjects", p.subject_uid, { name: p.name, color: p.color, notes: p.notes });
    else if (op === "archive_subject" || op === "unarchive_subject") patch("subjects", p.subject_uid, { is_archived: op === "archive_subject" ? 1 : 0 });
    else if (op === "add_goal") add("goals", { is_completed: 0, sort_order: -1, ...p });
    else if (op === "update_goal") patch("goals", p.goal_uid, { name: p.name, notes: p.notes, deadline: p.deadline });
    else if (op === "complete_goal" || op === "uncomplete_goal") patch("goals", p.goal_uid, { is_completed: op === "complete_goal" ? 1 : 0 });
    else if (op === "toggle_goal_focused") {
      const row = result.goals.find((item) => item.uid === p.goal_uid);
      if (row) patch("goals", p.goal_uid, { is_focused: row.is_focused ? 0 : 1 });
    } else if (op === "delete_goal") {
      remove("goals", p.goal_uid);
      result.milestones = result.milestones.filter((row) => row.goal_uid !== p.goal_uid);
    } else if (op === "add_milestone") add("milestones", { is_done: 0, ...p });
    else if (op === "update_milestone") patch("milestones", p.milestone_uid, { title: p.title, note: p.note });
    else if (op === "set_milestone_done") patch("milestones", p.milestone_uid, { is_done: p.done ? 1 : 0 });
    else if (op === "delete_milestone") remove("milestones", p.milestone_uid);
    else if (op === "add_template") add("templates", { is_active: 1, ...p, milestones_json: JSON.stringify(p.milestone_titles || []) });
    else if (op === "update_template") patch("templates", p.template_uid, { title: p.title, notes: p.notes,
      recurrence: p.recurrence, recurrence_day: p.recurrence_day, milestones_json: JSON.stringify(p.milestone_titles || []) });
    else if (op === "set_template_active") patch("templates", p.template_uid, { is_active: p.active ? 1 : 0 });
    else if (op === "delete_template") remove("templates", p.template_uid);
    else if (op === "set_subject_order" || op === "set_goal_order") {
      const table = op === "set_subject_order" ? "subjects" : "goals";
      (p.subject_uids || p.goal_uids || []).forEach((uid, index) => patch(table, uid, { sort_order: index }));
    }
  }
  return { snapshot: result, active: timer };
}
