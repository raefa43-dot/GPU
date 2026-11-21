# GPU — Quantitative Spot Signal Hunter

GPU is a **signal-only** Binance Spot market scanner. It analyzes markets and sends
potential setups to Telegram so the user can make the trading decision manually.

## Safety by design

- GPU does not place orders.
- GPU does not contain buy/sell order functions.
- GPU does not require Binance API keys or trading permissions.
- Use Telegram secrets only; never commit real credentials.
- Signals are analytical examples, not guarantees of profit.

## Watchlist

BTC/USDT, SOL/USDT, RENDER/USDT, NEAR/USDT, PROM/USDT,
LINK/USDT, LTC/USDT, ZEC/USDT, DEXE/USDT, HYPE/USDT.

The program checks which pairs are currently available as Binance Spot markets and
skips unavailable pairs.

## Timeframes and schedule

15m, 30m, 1h, 4h. GPU scans every 30 minutes and excludes the currently forming
candle from indicator calculations.

## Analysis

- EMA20 / EMA50 trend context
- RSI and MACD momentum
- Bollinger middle band
- Relative volume
- ATR-based risk reference
- Recent 15m swing-low stop reference
- Illustrative 2R and 3R target references
- BTC correlation and beta over 7, 30 and 90 days using available 4h candles

The 80/100 threshold is an indicator-confluence score, **not** an 80% win probability.
The first target is designed around at least 1:2 reward-to-risk. Real fills, slippage,
fees, gaps, and market conditions can change outcomes.

## Setup in GitHub Codespaces

1. Create a new repository named `GPU`.
2. Upload `gpu.py`, `requirements.txt`, `.env.example`, `.gitignore`, and this README.
3. Add GitHub Codespaces Secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   Grant the secret access to the GPU repository.
4. Stop and recreate/restart the Codespace after adding or changing secrets.
5. In the terminal run:

```bash
pip install -r requirements.txt
python gpu.py
```

GPU sends a startup message to Telegram. Keep the terminal session running while
using Codespaces; stopping the Codespace stops GPU.

## Important limitations

- This is an initial research prototype, not a production-validated strategy.
- No historical backtest or paper-forward test has been performed for your account.
- Correlation and beta are descriptive statistics, not forecasts.
- The SQLite duplicate-alert database is local to the running environment. It may
  not persist if the environment is deleted or recreated.
- The scanner may skip a symbol if Binance does not offer its Spot pair.
- No trade is recommended when higher-timeframe confirmation or score thresholds fail.

## GPU identity

The project name is GPU in the code, startup Telegram message, logs, README, and
configuration-file comments.
