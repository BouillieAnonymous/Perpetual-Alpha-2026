import json

from perpetual_alpha.api_client import ReadOnlyClient, account_ready, load_public_settings


def main():
    with ReadOnlyClient(load_public_settings()["TEAM_WALLET_ADDRESS"]) as client:
        result = client.account()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not account_ready(result):
        raise SystemExit("Account not ready: check unifiedAccount and USDPR")
    print("ACCOUNT_CHECK_PASSED")


if __name__ == "__main__":
    main()
