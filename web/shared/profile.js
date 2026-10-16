// Explicit URL profiles. The JSON literal is also read by the shell generator;
// edit this and web/index.html, then run scripts/generate_phone_apps.py --write.
export const PROFILES = {
  "full": {
    "name": "JobTracker", "path": "/", "defaultView": "weight",
    "views": ["goals", "today", "sessions", "graphs", "weight"],
    "storagePrefix": "jobtracker", "manifest": "/manifest.webmanifest",
    "identity": "/", "scope": "/", "icon": "/icons/cutout"
  },
  "weight": {
    "name": "Weight", "path": "/phone/weight/", "defaultView": "weight",
    "views": ["weight"], "storagePrefix": "jobtracker",
    "manifest": "/phone/weight/manifest.webmanifest",
    "identity": "/", "scope": "/", "icon": "/icons/weight"
  },
  "jobtracker": {
    "name": "JobTracker", "path": "/phone/jobtracker/", "defaultView": "today",
    "views": ["goals", "today", "sessions", "graphs"],
    "storagePrefix": "jobtracker.phone.jobtracker",
    "manifest": "/phone/jobtracker/manifest.webmanifest",
    "identity": "/phone/jobtracker/", "scope": "/phone/jobtracker/",
    "icon": "/icons/cutout"
  }
};

export function profileForPath(path = "/") {
  return Object.entries(PROFILES).find(([key, value]) => key !== "full" &&
    (path === value.path || path === `${value.path}index.html`))?.[0] || "full";
}
export const PROFILE_ID = profileForPath(globalThis.location?.pathname);
export const PROFILE = PROFILES[PROFILE_ID];
