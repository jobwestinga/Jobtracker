// Compatibility entrypoint for existing plotting and picker consumers.
export { parseWeight, selectMeasurements, pointsFor, loess, rollingMean, pchip, trendFor } from "./features/weight/math.js";
export { wholeWeightTiles, weightTileAnchor, splitWeightTiles, selectedTileWeight, dailyWeightMoment, manualDateCollision } from "./features/weight/picker.js";
