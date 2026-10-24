"""Check a wallet's FET balance on the Fetch.ai testnet (or mainnet with --mainnet).

    python -m scripts.check_balance fetch1yourwalletaddress
"""
import sys

from cosmpy.aerial.client import LedgerClient, NetworkConfig
from cosmpy.aerial.wallet import Address


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("Usage: python -m scripts.check_balance fetch1... [--mainnet]")
    mainnet = "--mainnet" in sys.argv
    client = LedgerClient(NetworkConfig.fetchai_mainnet() if mainnet else NetworkConfig.fetchai_stable_testnet())
    denom = "afet" if mainnet else "atestfet"
    base = client.query_bank_balance(Address(args[0]), denom)
    print(f"{args[0]}\n{base / 10**18:.6f} {'FET' if mainnet else 'testnet FET'} on {'mainnet' if mainnet else 'stable testnet'}")


if __name__ == "__main__":
    main()
