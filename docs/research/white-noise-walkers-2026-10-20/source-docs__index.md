---
layout: default
title: Participant Guide
permalink: /docs/
---
{% assign v = site.data.venue %}
{% assign base_id = v.dex_index | times: 10000 | plus: 100000 %}
<div class="page-content docs" markdown="1">

# Participant Guide
{: .no_toc}

This guide covers the venue, its assets and pricing, account setup, and the interfaces for trading and market data.

* TOC
{:toc}

## Venue

| Item | Value |
|---|---|
| Network | Hyperliquid **testnet** |
| REST | `{{ v.api_url }}/info` for queries. `{{ v.api_url }}/exchange` for signed actions. |
| WebSocket | `{{ v.ws_url }}` |
| Dex | `{{ v.dex }}` (dex index {{ v.dex_index }}) |
| Coins | The dex name, a colon and the coin: `{{ v.dex }}:BTC`, `{{ v.dex }}:ETH`, … |
| Collateral | `{{ v.collateral }}` |
| Account mode | Unified (`unifiedAccount`). One `{{ v.collateral }}` balance is the margin for all positions. |

The scored phase runs on `{{ v.dex }}`. The scored phase does not use the practice dex `par` or its collateral `USDPR`. Change the dex, the coin prefix and the asset IDs in your bot to the values on this page.

<div class="callout warn" markdown="1">
**WARNING:** Set the `dex` parameter to `{{ v.dex }}` in market-wide and position queries. Use the dex prefix on coin names. Without this selection, raw API queries default to the native Hyperliquid markets and may return data without an error.
</div>

## Markets

Use the `meta` info query with the `dex` parameter set to `{{ v.dex }}`. Read `szDecimals` and `maxLeverage` at runtime; the table describes the current `{{ v.dex }}` markets.

| Coin | Price units | Lot | Max leverage | Asset ID |
|---|---|---|---|---|
| `{{ v.dex }}:BTC` | USD per BTC | 0.00001 | 40x | {{ base_id }} |
| `{{ v.dex }}:ETH` | USD per ETH | 0.0001 | 25x | {{ base_id | plus: 1 }} |
| `{{ v.dex }}:HYPE` | USD per HYPE | 0.01 | 10x | {{ base_id | plus: 2 }} |
| `{{ v.dex }}:SP500` | S&P 500 index points | 0.001 | 50x | {{ base_id | plus: 3 }} |
| `{{ v.dex }}:EWY` | USD per share of the iShares MSCI South Korea ETF | 0.001 | 20x | {{ base_id | plus: 4 }} |
| `{{ v.dex }}:EWJ` | USD per share of the iShares MSCI Japan ETF | 0.001 | 20x | {{ base_id | plus: 5 }} |
| `{{ v.dex }}:BRENT` | USD per barrel of Brent crude | 0.01 | 20x | {{ base_id | plus: 6 }} |
| `{{ v.dex }}:GOLD` | USD per troy ounce of gold | 0.0001 | 25x | {{ base_id | plus: 7 }} |
| `{{ v.dex }}:USDEUR` | **USD per EUR (EUR/USD)** | 0.1 | 50x | {{ base_id | plus: 8 }} |
| `{{ v.dex }}:USDJPY` | **JPY per USD (USD/JPY)** | 0.01 | 50x | {{ base_id | plus: 9 }} |

Despite its ticker, `USDEUR` tracks EUR/USD. All markets trade 24/7 and settle in `{{ v.collateral }}`, including the FX contracts.

## Asset references and pricing

The table combines the venue's oracle and mark specification with live `{{ v.dex }}` asset annotations (`perpAnnotation`) and market metadata, checked on 19 October 2026. Market specifications come from `meta`; oracle and mark prices come from `metaAndAssetCtxs`.

All ten assets use **SEDA Signal**, delivered through SEDA Fast. The oracle is the median of quotes returned by the configured sources: Binance, Binance Futures, Lighter and Hydromancer. **EWJ excludes Lighter.** Only sources returning a quote participate; the feed requires at least one source.

| Asset | Reference instrument and mainnet counterpart | Oracle computation | Mark methodology |
|---|---|---|---|
| `{{ v.dex }}:BTC` | Bitcoin; Hyperliquid `BTC` | `BTC/USD` feed, venue median | Median method below; EMA basis bound ±2.5% of oracle |
| `{{ v.dex }}:ETH` | Ethereum; Hyperliquid `ETH` | `ETH/USD` feed, venue median | Median method below; EMA basis bound ±4% of oracle |
| `{{ v.dex }}:HYPE` | Hyperliquid's HYPE token; Hyperliquid `HYPE` | `HYPE/USD` feed, venue median | Median method below; EMA basis bound ±10% of oracle |
| `{{ v.dex }}:SP500` | S&P 500 index; `xyz:SP500` | `SP500/USD` feed, venue median | Median method below; EMA basis bound ±2% of oracle |
| `{{ v.dex }}:EWY` | iShares MSCI South Korea ETF; `xyz:EWY` | `EWY/USD` feed, venue median | Median method below; EMA basis bound ±5% of oracle |
| `{{ v.dex }}:EWJ` | iShares MSCI Japan ETF; `xyz:EWJ` | `EWJ/USD` feed, venue median excluding Lighter | Median method below; EMA basis bound ±5% of oracle |
| `{{ v.dex }}:BRENT` | Brent crude; `xyz:BRENTOIL` | `BRENT/USD` feed, venue median | Median method below; EMA basis bound ±5% of oracle |
| `{{ v.dex }}:GOLD` | Gold (XAU); `xyz:GOLD` | `XAU/USD` feed, venue median | Median method below; EMA basis bound ±4% of oracle |
| `{{ v.dex }}:USDEUR` | EUR/USD: USD per EUR; `xyz:EUR` | `EUR/USD` feed, venue median | Median method below; EMA basis bound ±2% of oracle |
| `{{ v.dex }}:USDJPY` | USD/JPY: JPY per USD; `xyz:JPY` | `USD/JPY` feed, venue median | Median method below; EMA basis bound ±2% of oracle |

### Oracle and mark prices

The **oracle** is the SEDA composite submitted by the venue's authorized oracle relayers, rounded to the market's price precision. The upstream feed computation is part of the venue's operator specification. The public API returns the resulting `oraclePx`; it does not return the individual upstream quotes.

The **mark** is the price used for unrealized PnL, margin and liquidations. The venue computes it as follows:

1. The relayer maintains a **150-second exponential moving average (EMA)** of the local book mid price minus the oracle price, using the mid and oracle from the same on-chain snapshot. This is the book basis. The EMA starts at zero and decays toward zero when there is no two-sided book.
2. The basis adjustment is capped at **±oracle price / maximum leverage**. The table expresses this bound as a percentage of the oracle.
3. The relayer prepares two mark inputs: the oracle, and the oracle plus the capped basis. Each input is limited to a **±0.5% move from the previous on-chain mark** and rounded to the market's price precision.
4. HyperCore takes the **median of those two submitted inputs and the local book mark**. The local book mark is the median of the best bid, best ask and last trade price. Accepted live `{{ v.dex }}` oracle-update transactions confirm the two submitted input lists.

HyperCore also applies its own price clamps, including a 1% limit on each mark update. After 10 seconds without a mark update, the protocol falls back to the local book mark. See the [HIP-3 mark calculation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-3-deployer-actions).

Read `oraclePx` and `markPx` separately from `metaAndAssetCtxs`. A reference instrument identifies what the contract tracks; the published oracle, the local execution price and the mark can differ.

## Venue mechanics

| Item | Detail |
|---|---|
| Fees | At the undiscounted base tier, **0.09% taker and 0.03% maker**, charged in `{{ v.collateral }}` on each fill. This venue has `deployerFeeScale=1` and no growth mode, so positive base rates from `userFees` are doubled. The actual charge is in each fill's `fee` and `feeToken` fields. See the [fee formula](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees#fee-formula-for-developers) for tiers and discounts. |
| Funding | Paid every hour. A positive rate means longs pay shorts; a negative rate means shorts pay longs. `funding` in `metaAndAssetCtxs` is the hourly rate as a decimal. Payment magnitude is `abs(position size) × oracle price × abs(hourly rate)`. Get rates from `fundingHistory` and account payments from `userFunding`. |
| Oracle price | The SEDA Signal venue median described in [Asset references and pricing](#asset-references-and-pricing). Available as `oraclePx` in `metaAndAssetCtxs`. |
| Mark price | The median calculation and basis limits described in [Asset references and pricing](#asset-references-and-pricing). It can differ from the oracle. Available as `markPx` in `metaAndAssetCtxs`. |
| Margin | Cross positions share the unified collateral balance. Losses on one position reduce the margin available to others. Set leverage with `update_leverage`; check leverage and available buying power with `activeAssetData`. The spot balance's `total` is not free margin. |
| Liquidation | Triggered when collateral no longer covers maintenance margin. Check `liquidationPx` in each position and the account's margin state. See [unified account risk](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/account-abstraction-modes#unified-account-ratio) for the maintenance-margin ratio. |

## Setup

Python users need Python 3.10 or later and the official [Hyperliquid Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk), version **{{ v.sdk_version }}**. The SDK handles asset IDs, serialization and request signing. Configure it for the testnet endpoint listed above.

For other languages, refer to [Other languages](#other-languages).

## Team wallet

The team wallet is your account. The organizers fund it. Use its address for all account queries; these queries do not require a private key.

Create and manage the wallet with your own wallet tooling. Give only its public address to the organizers. Keep the team wallet's private key outside the bot and never share it with organizers or support.

For the scored phase, the organizers send **100,000 `{{ v.collateral }}`** to the team wallet at **12:00 UTC on 19 October 2026**. They use a spot transfer (`spotSend`) to the spot balance, the same method as in the practice phase. In unified mode, this balance is the margin for `{{ v.dex }}`; do not transfer it to the dex. The sending address can be different from the practice phase. To confirm the funds, read the `{{ v.collateral }}` balance with `spotClearinghouseState`. Do not monitor a specific sending address.

The API wallet approval and unified mode from the practice phase stay valid if the approval did not expire.

## Set up the account

Do this step one time, after the organizers fund the team wallet.

Your bot signs trading actions with an **API wallet**, a separate key authorized by the team wallet. It can trade, modify and cancel orders, and set leverage. It cannot withdraw or send funds to another address. A compromised API wallet can still damage the account through trading.

| Operation | API action or query | Python SDK | Signing wallet |
|---|---|---|---|
| Approve an API wallet | `approveAgent` | `approve_agent` | Team wallet |
| Set unified mode | `userSetAbstraction`, account mode `unifiedAccount` | `user_set_abstraction` | Team wallet |
| Check approved API wallets | `extraAgents` | `Info.post` | None |
| Check account mode | `userAbstraction` | `query_user_abstraction_state` | None |
| Check collateral balance | `spotClearinghouseState` | `spot_user_state` | None |

The approval identifies the API wallet's public address, name and expiry. The SDK's `approve_agent` generates a new API wallet key and returns it with the approval response. Handle private keys through your own credential management; exclude them from source code, logs and shared files. Check the approval response and `extraAgents` before trading. Account administration is signed by the team wallet; bot trading is signed by the approved API wallet.

In unified mode, one `{{ v.collateral }}` balance backs cross-margin positions and is shared with spot. You do not move funds between spot and the dex. If the balance is zero, ask in `#general` on Discord using only the public account address.

- Use a separate API wallet for each process to avoid nonce collisions.
- An account supports up to 3 named API wallets. Approving a new wallet under an existing name replaces the previous approval.
- Set an expiry for the approval. Replace compromised or expired wallets with fresh keys; never reuse a removed API wallet's key because its nonce history may have been pruned.
- Use the team wallet address for queries. The API wallet address has no balance, positions or orders for your account.
- Hyperliquid may remove an API wallet when it expires or the account no longer has funds. See [API wallet lifecycle](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets).

## Client

The Python SDK separates reads and signed actions:

| Client | Purpose | Configuration |
|---|---|---|
| `Info` | Market data and account queries | Testnet API URL; select `{{ v.dex }}` through `perp_dexs`. Enable its WebSocket manager if using subscriptions. |
| `Exchange` | Signed trading actions | Testnet API URL; approved API wallet signer; team wallet address as `account_address`; select `{{ v.dex }}` through `perp_dexs`. |

Selecting only this dex means plain native coin names are absent from the SDK's market mapping. Use the prefixed coin names listed in [Markets](#markets).

Inspect both the request status and the result of every order. A request-level status of `ok` can contain rejected orders in `response.data.statuses`. Batch results correspond to individual operations; some may succeed while others fail.

## Trade

Use the [price and size rules](#price-and-size-rules) for every order.

Read the market specifications before constructing an order. An order identifies the market, side, quantity, limit price, time in force and whether it is reduce-only. Set leverage for the market explicitly. Track the returned order ID or your client order ID to reconcile updates, fills and cancellations.

| Operation | Python SDK method | Required selection or inputs |
|---|---|---|
| Market specifications | `meta` | Dex |
| Mark, oracle, funding and open interest | `Info.post`, query type `metaAndAssetCtxs` | Dex |
| Mid prices | `all_mids` | Dex |
| Order book | `l2_snapshot` | Coin |
| Candles | `candles_snapshot` | Coin, interval and time range |
| Collateral balance | `spot_user_state` | Team wallet address |
| Positions | `user_state` | Team wallet address and dex; positions are in `assetPositions` |
| Open orders | `open_orders` | Team wallet address and dex |
| Fills | `user_fills` | Team wallet address |
| Leverage | `update_leverage` | Coin, leverage and margin mode |
| Limit or post-only order | `order` | Coin, side, size, price and time in force (`Gtc` or `Alo`) |
| Market execution | `order` or `market_open` | IOC time in force and a price limit; the helper uses a slippage limit |
| Modify | `modify_order` | Order ID and replacement order parameters |
| Cancel | `cancel` | Coin and order ID |
| Close a position | `market_close` | Coin; optional size and slippage limit |

For the underlying instruments, oracles and mark calculation, refer to [Asset references and pricing](#asset-references-and-pricing). For other order types, batch orders and `cloid`, refer to [Orders and leverage](#orders-and-leverage). For fees, funding and margin, refer to [Venue mechanics](#venue-mechanics).

## Price and size rules

| Rule | Detail |
|---|---|
| Size | A multiple of the lot (`10^-szDecimals`). If necessary, decrease the size to a multiple of the lot. |
| Price | A maximum of 5 significant figures **and** a maximum of `6 − szDecimals` decimals. Integer prices are always correct. |
| Minimum value | $10 for each order (price × size) |
| Price band | Not more than 80% from the reference price |
| Format | Decimal strings. Remove trailing zeros from the fractional part and an empty decimal point; preserve integer zeros. The SDK handles conversion. |
| Open interest limit | $10M notional for each market, for all participants together. The API rejects orders that increase open interest above this limit. Get the values with `perpDexLimits`. |

## Orders and leverage

| Function | How to use |
|---|---|
| Good-till-cancel | Limit order with time in force `Gtc`; the unfilled remainder stays on the book. |
| Immediate-or-cancel | Limit order with time in force `Ioc`; available liquidity fills within the price limit and the remainder is cancelled. `market_open` and `market_close` use a default slippage limit of 5%. |
| Post-only | Limit order with time in force `Alo`. The API rejects it if it would immediately cross the book. |
| Stop or take-profit | Trigger order with a trigger price (`triggerPx`), market-or-limit execution (`isMarket`) and stop-loss or take-profit designation (`tpsl`: `sl` or `tp`). TP/SL orders must be reduce-only. Raw JSON prices are decimal strings; the SDK accepts numeric prices and serializes them. |
| Reduce-only | Set the reduce-only flag. The order may reduce an existing position but must not increase exposure. |
| Client order ID | A user-assigned 16-byte hexadecimal ID (`cloid`). The SDK represents it with `Cloid` from `hyperliquid.utils.types`; cancellation by this ID uses `cancel_by_cloid`. |
| Change an order | `modify_order` replaces an order's parameters, identifying it by order ID or client order ID. |
| Batch | `bulk_orders` and `bulk_cancel` submit several operations together. Each operation counts against the account budget. |
| Leverage | `update_leverage` selects leverage and cross or isolated margin for a coin. Leverage must not exceed the market's maximum. |
| Dead man's switch | `schedule_cancel` schedules cancellation of all open orders at a future timestamp, at least 5 seconds ahead. Up to 10 triggers per day, resetting at 00:00 UTC. Requires $1,000,000 of account trade volume. |

The request-level `status` can be `ok` even when an order is rejected. Inspect each result in `response.data.statuses`:

- `resting.oid`: the order ID of the remaining order on the book.
- `filled.totalSz`, `filled.avgPx`, `filled.oid`: the executed size, average fill price and order ID. An IOC can fill partially; `totalSz` may be smaller than the requested size. Its unfilled remainder is cancelled.
- `error`: the reason the API rejected the order.

If the full request fails, `status` is `err` and `response` contains the reason.

The SDK's `market_close` submits a reduce-only IOC. It can partially fill or be rejected; read the position again to confirm the remaining size. It returns no result if it finds no position for the coin.

## Info queries

Send `POST {{ v.api_url }}/info` with a JSON body. The `user` is always the **team wallet** address.

| `type` | Parameters | Result |
|---|---|---|
| `meta` | `dex` | Markets: name, szDecimals, maxLeverage |
| `metaAndAssetCtxs` | `dex` | Markets, mark, oracle, mid, funding, open interest, 24h volume |
| `allMids` | `dex` | Mid price for each coin |
| `l2Book` | `coin` | Order book, maximum 20 levels on each side |
| `recentTrades` | `coin` | Latest public trades |
| `candleSnapshot` | `req` object containing `coin`, `interval`, `startTime`, `endTime` | OHLCV, maximum 5000 candles, `1m` to `1M` |
| `fundingHistory` | `coin, startTime, endTime?` | Funding rates for each hour |
| `perpDexs` | | Dex list and funding parameters |
| `perpDexLimits` | `dex` | Open interest limits |
| `spotClearinghouseState` | `user` | Balance. In unified mode, this is the balance for all positions. |
| `clearinghouseState` | `user, dex` | Positions on the dex |
| `openOrders` | `user, dex` | Open orders |
| `orderStatus` | `user, oid` (or cloid) | Status of one order |
| `userFills` / `userFillsByTime` | `user` (`startTime`, `endTime`) | Fills |
| `userFunding` | `user, startTime, endTime?` | Funding payments |
| `activeAssetData` | `user, coin` | Leverage, margin mode, maximum order sizes |
| `userFees` | `user` | Base fee rates |
| `userRateLimit` | `user` | Account budget |
| `userAbstraction` | `user` | Account mode |
| `extraAgents` | `user` | Approved API wallets |

## Streams

Connect to the WebSocket endpoint and subscribe to the channels your algorithm needs. Market channels identify a prefixed coin; account channels identify the team wallet address. The SDK's `subscribe` method associates a subscription with your message handler.

- In the raw protocol, use the `subscribe` method with a subscription specifying the channel type and its parameters.
- Send the `ping` method at intervals of 60 seconds or less. The Python SDK's WebSocket manager sends these heartbeats automatically.
- If the connection stops, do these steps:
  1. Connect again.
  2. Subscribe again.
  3. Read the account state again through REST.
- The first `userFills` and `trades` messages contain recent history. Reconcile that history with events already processed. For public trades, identify duplicates by block time (`time`), `coin` and `tid` together; `tid` alone is not globally unique.

The supported channels are listed below.

### WebSocket

| `type` | Parameters | Data |
|---|---|---|
| `l2Book` | `coin` | Order book |
| `bbo` | `coin` | Best bid and offer |
| `trades` | `coin` | Public trades |
| `candle` | `coin, interval` | Candles |
| `activeAssetCtx` | `coin` | Mark, oracle, funding, open interest |
| `allMids` | `dex` | Mid prices |
| `userFills` | `user` | Your fills |
| `orderUpdates` | `user` | Changes to the status of your orders |
| `userEvents` | `user` | Fills, funding, liquidations |
| `userFundings` | `user` | Your funding payments |

The `coin` has the dex prefix (`{{ v.dex }}:BTC`). The `user` is the team wallet address.

The Python SDK does not send data for all channels. If a channel sends no data, use a raw WebSocket client.

## Rate limits

**Each IP address:** a maximum weight of 1200 each minute, shared by all clients using that IP.

| Request | Weight |
|---|---|
| Exchange action | 1 + floor(orders in the batch / 40) |
| `l2Book`, `allMids`, `clearinghouseState`, `orderStatus`, `spotClearinghouseState`, `exchangeStatus` | 2 |
| `userRole` | 60 |
| Other info requests | 20 |

History queries also have response-size charges: `recentTrades`, `userFills`, `userFillsByTime`, `fundingHistory` and `userFunding` add weight per 20 returned items; `candleSnapshot` adds weight per 60 returned items. See the [full rate-limit specification](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/rate-limits-and-user-limits) for all endpoints.

**Each account:**

- Each order, cancel and change uses one request from the account budget.
- The budget starts at **10,000**. It increases by **1 for each $1 that you trade**.
- When the budget is empty, you can send one request each 10 seconds.
- The cancel limit is `min(budget + 100,000, 2 × budget)`.
- Get the status with `userRateLimit` (`nRequestsUsed`, `nRequestsCap`).
- In unified mode, the account can send a maximum of 50,000 actions each day.

**Other limits:**

- 1000 open orders.
- WebSocket: 10 connections for each IP address, 1000 subscriptions and 2000 sent messages each minute.

**Nonces:**

- Use the current time in ms.
- Do not use the same nonce two times with one key.
- The nonce must be between 2 days before and 1 day after the server time.
- Hyperliquid keeps the 100 highest nonces for each key. A new nonce must be higher than the lowest of these.
- Use one API wallet for each process.
- Keep your clock correct (NTP).

## Other languages

- **TypeScript:** [`@nktkas/hyperliquid`](https://github.com/nktkas/hyperliquid)
- **Rust:** [`hyperliquid_rust_sdk`](https://github.com/hyperliquid-dex/hyperliquid-rust-sdk)
- **Raw API:** [Hyperliquid API documentation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api), [signatures](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/signing)

Use the **testnet** URLs and the dex `{{ v.dex }}`.

When you sign actions without an SDK:

- Set unified mode through `userSetAbstraction`, using the account mode `unifiedAccount` and a team-wallet signature. The agent-signed equivalent is `agentSetAbstraction`, using mode `u`.
- Send `POST {{ v.api_url }}/exchange` with a JSON object containing `action` (object), `nonce` (integer timestamp in ms), and `signature` (object with hex strings `r` and `s`, and integer `v`).
- The asset ID `a` is `100000 + 10000 × {{ v.dex_index }} + the index in meta`. For `{{ v.dex }}:BTC`, the asset ID is {{ v.dex_index | times: 10000 | plus: 100000 }}.
- Send prices and sizes as decimal strings. Remove trailing zeros only from the fractional part and remove an empty decimal point. Preserve integer zeros. The Python SDK handles this conversion.
- Use the current time in ms as the `nonce`. Do not use the same nonce two times with one API wallet.

## Errors

Order errors may end with `asset=<asset ID>`. This ID shows the market.

| Error | Action |
|---|---|
| `User or API Wallet 0x… does not exist.` | Check the approval with `extraAgents` and use the testnet URL. If the approval expired or was removed, authorize a fresh API wallet through the team wallet. |
| `KeyError: 'BTC'` (Python SDK) | Use `{{ v.dex }}:BTC`. |
| `Order must have minimum value of $10.` | Increase the size. |
| `Order has invalid price.` | Decrease the number of decimals. Refer to the [rules](#price-and-size-rules). |
| `Price must be divisible by tick size.` | Use a maximum of 5 significant figures. |
| `Order has invalid size.` | Decrease the size to a multiple of the lot. |
| `Order price cannot be more than 80% away from the reference price` | Move the price nearer to the market price. |
| `Post only order would have immediately matched, bbo was <bid>@<ask>.` | The `Alo` price crosses the book. Move the price away from the other side. |
| `Order could not immediately match against any resting orders.` | No order is available at your `Ioc` price. Move the price limit further into the book. |
| `Insufficient margin to place order.` | Decrease the size, or increase the leverage. |
| `Reduce only order would increase position.` | Make sure that the side and the size are correct. |
| `Invalid leverage value` | Use a leverage that is not more than the [max leverage](#markets). |
| `Cannot set scheduled cancel time until enough volume traded. Required: $1000000.` | `schedule_cancel` is available after a trade volume of $1,000,000. |
| `Too many cumulative requests sent` | The account budget is empty. Refer to [Rate limits](#rate-limits). |
| An error about the `nonce` | Use one API wallet for each process. Make sure that your clock is correct. |
| The balance is zero | Get the balance with `spotClearinghouseState`. Use the team wallet address. |
| No positions or orders | Use the team wallet address and set the `dex` parameter to `{{ v.dex }}`. |
| The prices are not correct | You read a native market. Add the dex prefix to the coin, and add `dex`. |

## Monitored behaviour

The organizers record all activity on `{{ v.dex }}`: every trade with both counterparties, the order book, the oracle and mark prices, and each team wallet's equity, fills, funding and transfers. The table lists the behaviour that is **prohibited** and the data used to find it.

| Behaviour | What it means | What the organizers check |
|---|---|---|
| **Wash trading and self-crossing** | Trading with yourself: your team on both sides of a trade, or accounts controlled by the same people | The buyer and seller of every trade |
| **{{ v.collateral }} transfers between teams** | Sending or receiving `{{ v.collateral }}` to or from another team's wallet, directly or through an intermediate address | Every transfer in and out of each team wallet |
| **Value transfer through trading** | One account losing on purpose to another, also when split over many trades, counterparties or days | Each team's net gain or loss against each counterparty, measured against the oracle |
| **Coordinated or mirrored trading** | Teams trading in step: the same market at the same time, on the same side or on opposite sides | The timing and direction of fills across teams |
| **Off-market trades** | Trades at prices far from the oracle, used to move value between accounts | The trade price against the oracle at the time of the trade |
| **Book or price manipulation** | Moving the mark, the oracle or the book, for example with orders on a thin book | Book depth and spread over time, abnormal premium episodes, and who traded in them |

Moving `{{ v.collateral }}` between the spot balance and `{{ v.dex }}` of the **same** team wallet is not a transfer between teams.

<div class="callout warn" markdown="1">
**WARNING:** A flag is not a verdict. The committee reviews the evidence and asks the team for an explanation before it decides. Confirmed violations result in **disqualification**.
</div>

## Questions

Ask in `#ask-anything` on Discord. Do not post a private key.

</div>
