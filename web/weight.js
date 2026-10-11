// Default composition and compatibility API. The feature itself accepts its data dependencies.
import { api } from "./data/api.js";
import { send, pendingOperations } from "./data/outbox.js";
import { getToken } from "./data/credentials.js";
import { uuid } from "./shared/ids.js";
import { createWeightFeature } from "./features/weight/index.js";
export { createWeightFeature } from "./features/weight/index.js";
export const weightFeature = createWeightFeature({ api, send, uuid, getToken, pendingOperations });
export const renderWeight = weightFeature.render;
export const invalidateWeight = weightFeature.invalidate;
export const resetWeight = weightFeature.reset;
