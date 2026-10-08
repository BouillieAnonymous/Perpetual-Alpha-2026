import json

from perpetual_alpha.api_client import ReadOnlyClient


def main():
    with ReadOnlyClient() as client:
        print("Environment: TESTNET / par / READ ONLY")
        print("Markets:", ", ".join(item["name"] for item in client.metadata()["universe"]))
        for symbol in ("par:BTC", "par:ETH"):
            result = client.market(symbol)
            print(json.dumps({"symbol": symbol, "bid": result["book"]["levels"][0][0]["px"],
                              "ask": result["book"]["levels"][1][0]["px"],
                              "mark": result["context"]["markPx"], "oracle": result["context"]["oraclePx"]}))
    print("CONNECTION_CHECK_PASSED")


if __name__ == "__main__":
    main()
