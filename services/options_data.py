"""
options_data.py — options chain helpers built on top of market_data.
"""
from services.market_data import get_options_chain


def get_nearest_expiry_chain(symbol: str):
    """
    Return (calls_df, puts_df, expiry_dates_list) for the nearest expiry.
    All three are None / [] if no options data is available.
    """
    chain, dates = get_options_chain(symbol)
    if chain is None:
        return None, None, []
    return chain.calls, chain.puts, dates


def format_options_summary(calls, puts) -> str:
    """
    Build a readable string showing the top 3 calls and puts by open interest.
    """
    if calls is None or calls.empty:
        return "No options data available."

    top_calls = calls.nlargest(3, "openInterest")[
        ["strike", "lastPrice", "openInterest", "impliedVolatility"]
    ]
    top_puts = puts.nlargest(3, "openInterest")[
        ["strike", "lastPrice", "openInterest", "impliedVolatility"]
    ]

    lines = ["**Top Calls by Open Interest:**"]
    for _, r in top_calls.iterrows():
        lines.append(
            f"  Strike {r['strike']:.1f} | "
            f"Last ${r['lastPrice']:.2f} | "
            f"OI {int(r['openInterest']):,} | "
            f"IV {r['impliedVolatility'] * 100:.1f}%"
        )

    lines.append("\n**Top Puts by Open Interest:**")
    for _, r in top_puts.iterrows():
        lines.append(
            f"  Strike {r['strike']:.1f} | "
            f"Last ${r['lastPrice']:.2f} | "
            f"OI {int(r['openInterest']):,} | "
            f"IV {r['impliedVolatility'] * 100:.1f}%"
        )

    return "\n".join(lines)
