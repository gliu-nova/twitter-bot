# Post Enhancements

I want to improve the bot's tweet/post templates and chart labels so posts are more accessible without being dumbed down.

## Current problems

* Headlines are too technical/jargony, e.g. "BTC SPREAD COLLAPSES."
* Body copy uses too much finance jargon, e.g. "Cross-exchange arb gap compressed."
* The post structure feels like indicator → template, instead of explaining what happened, why it happened, and why it matters.
* Generic endings like "→ Signal:" or "→ Takeaway:" feel repetitive and less natural.
* Chart titles/labels are too technical, e.g. "BTC Exchange Spread," instead of user-friendly, e.g. "Coinbase vs Kraken Price Difference."
* Some visuals do not clearly support the post's claim. If the alert is about a compressed spread, the chart should make that obvious; if the alert is about funding turning negative, the image should show the funding trend/flip, not a generic "Risk-On Shift" card.

## Goals

Please update the posting/template system with these goals:

### 1. Rewrite templates around this structure

- Accessible headline with key number
- WHAT happened (plain English + data)
- WHY it happened (short context)
- WHY it matters (real implication/edge)

Never use generic endings like "Signal:", "Takeaway:", or "→ Signal". Make the final line natural and tailored to the specific indicator.

Headlines should feel natural and readable while remaining precise. Avoid heavy jargon like "cross-exchange arb gap" or "venue pricing converging". Use plain language like "price gap between Kraken and Coinbase".

Keep posts relatively short and scannable.

Always include the key numbers (bps, %, annualized, % change, etc.).

End with a natural insight rather than a labeled "signal".

Do not over-explain definitions. Use a middle-ground voice: accessible to general crypto readers, but still useful to traders and professionals.

### 2. Improve chart/image generation

Add these hard rules in your generator:

- Only attach a chart if it clearly visually supports the exact narrative being posted (e.g. shows the sharp compression or the zero-line cross)
- The chart should preferably have an annotation, arrow, or highlight on the move you're reporting
- Never use generic dashboard cards or unrelated visuals (this was the fatal flaw in your BTC Funding post)
- Fallback: Text-only post with clean formatting is better than a confusing or irrelevant image

### 3. Add validation checks

Before posting, verify:

* The chart title matches the metric in the text
* Latest value in the chart matches latest value in the post
* The alert reason is visible from the chart or explained in the caption
* Final implication line is specific to the indicator
* No generic "Signal:" / "Takeaway:" label remains
* Jargon terms are either removed or paired with plain-English wording

## Implementation

Runtime templates live in `src/posting/compose.py`. Chart rendering lives in `src/posting/charts.py`. Pre-post validation runs in `src/posting/engine.py`.

Sample posts: `python scripts/generate_sample_posts.py`