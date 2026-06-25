# Post Enhancements 2

The latest templates are much better. Please make the following enhancements.

## Goals

* Keep the WHAT → WHY → WHY IT MATTERS structure.
* Make posts sound more like a careful market analyst, less like deterministic template output.
* Add more variation in headlines and final implication lines.
* Avoid unnatural shorthand.
* Prefer descriptive, evidence-based implications over predictive or overly confident claims.
* Add back one strong context/rarity line when available.(required when data supports it) — e.g. "Largest spike in 90 days", "Sharpest compression in 21 days", "Biggest funding move in 14 days", "New high in open interest", etc. Make it factual and time-based.

For severity, use emojis such as 🚨 or 🔥 sparingly and only on truly standout moves. Maximum 1 emoji per post.
Keep posts short and highly scannable with short lines.
Differentiate tone slightly based on severity: calm for small moves, sharper for large/rare ones.

## Important tone rule

Optimize for posts that sound like they were written by a thoughtful market analyst rather than a template engine. If two phrasings are equally accurate, prefer the one a human analyst would naturally write.

Because these posts are automated, avoid claims that sound like predictions or certainty. Do not say things like "this means price will move," "squeeze risk is building," or "less forced selling pressure ahead" unless the data directly supports that wording.

### Prefer safer phrasing

* "positioning is leaning more bearish"
* "shorts are becoming the more crowded side"
* "long leverage was reduced"
* "arbitrage room is shrinking"
* "futures demand is cooling"
* "pricing is more aligned across exchanges"
* "volatility may remain elevated"
* "this is consistent with…"

### Avoid overly confident phrasing

* "squeeze risk is building"
* "less forced selling pressure ahead"
* "buyers are in control"
* "shorts take control"
* "this confirms…"
* "this means…"

## Add variation

For each indicator family, create multiple headline variants and multiple final-line variants so repeated posts do not sound identical.

### Examples of better final lines

**Exchange spread:**

* "→ Less room for cross-exchange arbitrage."
* "→ Major venues are pricing BTC more similarly."
* "→ Exchange pricing is becoming more aligned."

**Funding:**

* "→ Traders are leaning more bearish."
* "→ Shorts are becoming the more crowded side."
* "→ Positioning has shifted below neutral."

**Liquidations:**

* "→ Long leverage was reduced sharply."
* "→ A large amount of long-side leverage was cleared."
* "→ The move forced crowded longs out of the market."

**Basis:**

* "→ Futures demand is cooling relative to spot."
* "→ Traders are paying less premium for futures exposure."
* "→ Perp pricing is moving closer to spot."

**Open interest:**

* "→ More leverage is entering the market."
* "→ Positioning is becoming heavier."
* "→ Leverage is coming out of the market."

## Rarity/context line

When historical context is available, include exactly one strong context line, such as:

* "Largest move in 30 days."
* "Biggest spike in 90 days."
* "Most negative funding in 2 weeks."
* "Widest Coinbase-Kraken gap this month."
* "Open interest at a 30-day high."

Do not include weak context just to fill space. If the context is not meaningful, omit it.

## Phrase cleanup

* Replace "less easy arb left" with "less room for arbitrage."
* Replace "buyers paying up vs spot" with "buyers are paying a larger premium over spot."
* Replace "downside fuel" or "upside fuel" unless clearly explained.
* Avoid compressed trader slang unless it improves clarity.

## Implementation

Runtime templates live in `src/posting/compose.py`. Sample posts: `python scripts/generate_sample_posts.py`