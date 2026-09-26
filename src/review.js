export function sortPairs(pairs, order = 'distance') {
  return [...pairs].sort((a, b) => order === 'timing'
    ? (a.gapDays ?? Infinity) - (b.gapDays ?? Infinity) || a.miles - b.miles
    : a.miles - b.miles || (a.gapDays ?? Infinity) - (b.gapDays ?? Infinity));
}

export function impactScenario(budget, sharePercent, lowPercent, highPercent, extraCost) {
  const values = [budget, sharePercent, lowPercent, highPercent, extraCost];
  if (values.some(v => !Number.isFinite(v) || v < 0) || sharePercent > 100 || highPercent > 100 || lowPercent > highPercent) return null;
  const shareable = budget * sharePercent / 100;
  return { shareable, low: shareable * lowPercent / 100 - extraCost, high: shareable * highPercent / 100 - extraCost };
}

export const money = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
