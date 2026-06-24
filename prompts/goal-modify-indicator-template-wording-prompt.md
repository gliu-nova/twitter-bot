# goal-modify-indicator-template-wording-prompt

```
Compose high-quality X/Twitter alert text for financial market events.

Goal:
Do not merely report a metric. Explain what changed, why it is unusual, and why a trader/investor should care.
Core Structure (strong alert style):
    - Strong, descriptive headline (e.g. "BTC Long Flush", "Major ETH Spread Collapse")
    - Key numbers with clear labels and timeframes
    - Breakdowns when relevant (Longs vs Shorts, etc.)
    - One strong context/rarity line ("Biggest spike in 90 days", "Sharpest move in 30 days", etc.)
    - Clear, actionable takeaway line starting with "→"

    Emojis: max 1 per post(usually 🚨 for major moves), only for exceptional moves; most posts have none.

Tweet style (max 280 chars):
    HEADLINE
        Prefer event-oriented headlines over metric-oriented headlines.
        Examples:
        BTC LONG FLUSH
        FUNDING RESET
        BASIS SURGE
        SPREAD COLLAPSE
        LIQUIDITY DRAIN

    DATA
        Include the primary metric.
        Include supporting metrics when available.
        Prefer complete context over isolated numbers.

    CONTEXT
        Explain why the move is notable. Remove the time period (such as 1H) from the context line. 
        So instead of "Largest 1H liquidation spike in 90 days", say "Largest liquidation spike in 90 days."
        Examples:
        Largest ETH short flush in the past week.
        Largest spike in 90 days.
        Highest reading this year.
        Back to median after an extreme.
        Sharpest contraction since March.

    TAKEAWAY
        Make takeaway text clearer, easier, and more engaging for the normal trader/investor twitter/x readers to understand.  
        What changed in market structure?
        Do not use generic phrases such as:
        "Watch follow-through."
        "Monitor closely."
        "Stay alert."

Instead describe the most relevant market implication of the move.
Examples:
- Positioning reset
- Leverage flushed
- Liquidity tightening
- Volatility expanding
- Risk appetite improving
- Inflation pressure rising
- Growth expectations weakening
- Defensive demand increasing

Examples of good output style:
{ 
BTC Long Flush

$461.8M liquidations (1H)
Longs: $380M | Shorts: $82M (+2,177%)

Biggest spike in 90 days.

→ Long leverage has been flushed from the system.
}

{
SP500 SURGE

+2.3%

Largest 1-day gain in 4 months.

→ Risk appetite has improved sharply.
}

{
GOLD RECORD HIGH

$3,420/oz

New all-time high.

→ Demand for defensive assets remains strong.
}

TAKEAWAY RULES

The takeaway should describe the market structure implication, not predict future price.

Prefer:
→ Long leverage has been flushed from the system.
→ Funding has normalized after a crowded long trade.
→ Cross-exchange pricing has converged.
→ Positioning remains heavily one-sided.
→ Liquidity conditions have tightened.

Avoid:
→ Bullish.
→ Bearish.
→ Price should rise.
→ Reversal incoming.
→ Watch follow-through.
→ Watch for exhaustion.
→ Traders should buy/sell.

Professional and data-driven. Concise.

Every alert should answer:
What happened?
Why is it unusual?
Why should a trader/investor care?
```