# EGX Lite Market Radar v2.0

Market activity scanner for the Egyptian Exchange (EGX).
Detects unusual volume, buying/selling pressure, and inconclusive signals across Shariah-compliant stocks.

**Lite does NOT make trading decisions.** ISM performs deep analysis and final decisions.

## Architecture

```
EGX Market
    |
Lite Market Radar
    |
Buying / Selling / Unusual Activity
    |
Manual selection or ISM handoff
    |
ISM Deep Analysis
    |
Final decision, entry, stop and targets
```

## What Lite Does

- Scans all 34 Shariah-compliant EGX stocks
- Detects unusual volume activity using RVOL, volume percentile, traded value
- Classifies activity as BUYING, SELLING, or UNUSUAL
- Scores activity strength from 0-100
- Ranks by activity level (EXTREME > HIGH > ELEVATED)
- Provides factual reasons for each detection
- Prepares neutral handoff payloads for ISM

## What Lite Does NOT Do

- BUY / SELL recommendations
- Entry, stop loss, or target prices
- Probability of success
- Trend-based filtering of bearish stocks

**Volume is the primary signal.** High bearish volume is intentionally included.

## Data Mode

Current: `DAILY_COMPLETED_SESSION` (latest completed daily candle).
Live and intraday features require a verified provider and are ready for future activation.

## Setup

1. Activate the virtual environment
2. Install dependencies:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. Create `.env` based on `.env.example`
4. Run the radar:
   ```powershell
   python main.py radar
   python main.py radar --top 10
   python main.py radar -t ARCC -t COMI
   ```

## Commands

### CLI
```bash
python main.py                  # Full radar scan (default)
python main.py radar            # Same as above
python main.py radar --top 10   # Top 10 results
python main.py radar -t ARCC    # Single symbol
python main.py scan             # Legacy scan (deprecated)
```

### Telegram Bot
```
/radar              Run market radar
/radar 10           Top 10 results
/radar_buying       Buying activity only
/radar_selling      Selling activity only
/radar_unusual      Unusual activity only
/radar_symbol ARCC  Single symbol analysis
/send_to_ism ARCC   Prepare ISM handoff
/scan               Legacy scan (deprecated)
/results            Last scan results
/market             Market status
/stocks             Stock list
```

## Activity Levels

| Level | Trigger |
|-------|---------|
| EXTREME | RVOL >= 3.0 or volume percentile >= 95 |
| HIGH | RVOL >= 2.0 or volume percentile >= 90 |
| ELEVATED | RVOL >= 1.35 or volume percentile >= 75 |
| NORMAL | Below thresholds |

## Activity Categories

| Category | Meaning |
|----------|---------|
| BUYING_ACTIVITY | Positive price action, buying signals |
| SELLING_ACTIVITY | Negative price action, selling pressure |
| UNUSUAL_ACTIVITY | Conflicting or inconclusive signals |

## Scoring Formula (0-100)

| Component | Max Points |
|-----------|-----------|
| Volume (RVOL + percentile) | 50 |
| Liquidity (traded value) | 15 |
| Price-Volume (candle, body, returns) | 15 |
| RSI momentum | 10 |
| MACD histogram momentum | 10 |

## Configuration

All settings configurable via environment variables in `.env`:

```
RADAR_TOP_N=20
RADAR_MIN_RVOL=1.35
RADAR_HIGH_RVOL=2.0
RADAR_EXTREME_RVOL=3.0
RADAR_MIN_AVG_TRADED_VALUE_20=1000000
RADAR_MIN_HISTORY_CANDLES=60
RADAR_INCLUDE_NORMAL=false
```

## Files

| File | Purpose |
|------|---------|
| `scanner/market_radar.py` | Core radar engine |
| `scanner/radar_data.py` | Data abstraction layer |
| `scanner/radar_output.py` | Console and Telegram formatters |
| `scanner/ism_handoff.py` | Neutral ISM handoff model |
| `scanner/config.py` | All configuration settings |
| `main.py` | CLI entry point |
| `bot.py` | Telegram bot |

## Testing

```bash
python -m pytest tests/test_market_radar.py -v    # 35 tests
python -m pytest tests/test_shadow.py -v          # 26 tests
python -m pytest tests/ -v                        # All tests
```

## No Live Orders

This scanner never places orders, never connects to live trading, and never generates fake live data.
