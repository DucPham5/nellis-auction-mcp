# Nellis Auction MCP — project idea

Idea logged 2026-07-04.

Build a **personal MCP server** for Nellis Auction (nellisauction.com — liquidation/overstock auctions) so an AI assistant (Claude) can browse listings and help hunt for deals.

## Features
- **Browse & search listings** — read public listing data.
- **Evaluate deals** ← the standout feature. Compare current bid + ~15% buyer's premium + tax vs. retail/market value, factor in item condition (lots are often returns), rank lots by expected value.
- **Place orders / bidding** — wanted, but do it **assisted**: the AI suggests a max bid, *you* confirm. Not fully autonomous, because bids are binding financial commitments on a logged-in account.

## Key facts
- Nellis has **no official MCP server and no public API**. Site runs on auctionsoftware.com infra.
- Approach: ~50–100 lines with the official MCP SDK (Python or TS), wrapping either their internal JSON endpoints (find via browser DevTools → Network tab) or HTML scraping.
- MCP is an open protocol — anyone can build a personal server; runs on your own machine with your own access.

## Risk notes (not legal advice)
- Reading **public** listings = low risk (cf. hiQ v. LinkedIn re: CFAA + public data).
- Real concerns: Nellis **ToS** likely prohibits scraping (worst case = IP/account ban), and for bidding, **real-money bugs** + authenticated-automation exposure.
- Line not to cross: autonomous binding bids. Keep a human in the loop for anything touching money/account actions.

## Next step when picking this up
1. Read Nellis `robots.txt` + Terms of Service.
2. Open the site, run a search, capture the request in DevTools → Network to see if it's a clean JSON API or a scraping job.
3. Scaffold a minimal MCP server with one tool: `search_listings(query)`.
