"""OKX market-data layer: REST backfill + live WebSocket candle stream.

Public market data only — no API key, no orders. Isolated here so the exchange
can be swapped without touching ``core/``.
"""
