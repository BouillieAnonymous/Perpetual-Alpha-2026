import json

from perpetual_alpha.api_client import ReadOnlyClient, load_public_settings


def main():
    settings = load_public_settings()
    with ReadOnlyClient(settings["TEAM_WALLET_ADDRESS"]) as client:
        result = client.authorization(settings["API_WALLET_ADDRESS"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["authorized"]:
        raise SystemExit("API wallet missing or expired; no changes made")
    print("API_WALLET_CHECK_PASSED; key ownership/signing not tested")


if __name__ == "__main__":
    main()
