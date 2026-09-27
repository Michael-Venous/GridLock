// Every fixed color the maps, lists and brief draw in, in one place so the app and the tests check the same ones.
// Plan colors by side class (see side() in app.js): DESC teal, Georgia orange, then violet, brown and navy for further
// plans. They stay apart under the common color-vision deficiencies. `PLAN_TEXT_COLORS` are the darker shades the map
// labels use; styles.css sets the page's own text shades (--desc-text, …) and repeats these fills. Plans past these
// five get generated colors (extraPlanColors in match.js).
export const PLAN_COLORS = { desc: "#0a8494", gpc: "#cb6e30", "plan-x1": "#7b4fb0", "plan-x2": "#8b5a2b", "plan-x3": "#2a3f7a" };
export const PLAN_TEXT_COLORS = { desc: "#0b5f6b", gpc: "#9a4a17", "plan-x1": "#6a3fa0", "plan-x2": "#7a4d22", "plan-x3": "#2a3f7a" };
// The printable brief's map.
export const PLAN_PRINT_COLORS = { ...PLAN_COLORS, desc: "#087f8c", gpc: "#b96124" };
// The map's ground layers (styles.css repeats them as --env-* for the layer legend).
export const ENV_COLORS = { wetland: "#1f8a3c", water: "#4f78c4", flood: "#3fb6d0", flood02: "#9fdbe6", habitat: "#b0397a", protected: "#8a7a2c" };
