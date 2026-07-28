function scoreSource(source, recentItems = []) {
  let score = 0;

  score += recentItems.length * 0.2;

  for (const item of recentItems) {
    const title = (item.title || "").toLowerCase();

    if (title.includes("scholarship")) score += 2;
    if (title.includes("phd")) score += 1;
    if (title.includes("master")) score += 1;

    if (title.includes("ad")) score -= 1;
    if (title.includes("clickbait")) score -= 2;
  }

  if (source.url.includes("edu")) score += 2;
  if (source.url.includes("gov")) score += 2;

  if (score > 10) score = 10;
  if (score < 0) score = 0;

  return Number(score.toFixed(2));
}

function shouldDisable(score) {
  return score < 1.5;
}

module.exports = {
  scoreSource,
  shouldDisable,
};