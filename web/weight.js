// Default composition of the weight feature with the real data layer (used by app.js and tests).
import { api } from "./data/api.js";
import { send, pendingForProjection as pendingOperations } from "./data/outbox.js";
import { getToken } from "./data/credentials.js";
import { uuid } from "./shared/ids.js";
import { createWeightFeature } from "./features/weight/index.js";
export { createWeightFeature } from "./features/weight/index.js";
export const weightFeature = createWeightFeature({ api, send, uuid, getToken, pendingOperations });
export const renderWeight = weightFeature.render;
export const invalidateWeight = weightFeature.invalidate;
export const resetWeight = weightFeature.reset;
