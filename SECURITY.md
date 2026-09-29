# Security Policy — 3MH TECHNOLOGIES

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 1.1.x   | ✅        |
| < 1.1   | ❌        |

## Reporting a Vulnerability

Do **not** open a public GitHub issue for security problems.

Report privately via Telegram: **https://t.me/j49_c**

Include: affected version, reproduction steps, and impact. We aim to
acknowledge within one day and ship a fix or mitigation as soon as practical.

## Data This Tool Touches

Handle these paths as secrets — they are git-ignored for that reason:

| Path | Contents | Risk if leaked |
| --- | --- | --- |
| `~/.chatdeep/cookies.json` | `dsc_vid` visitor cookie | Whoever holds it holds your daily quota identity |
| `~/.chatdeep/browser-profile/` | Chromium profile (playwright engine only) | Browser session state |
| `$CHATDEEP_API_KEY` env | Solver-service billing key | Paid solves on your account |

## Design Constraints

* The token bridge binds **127.0.0.1 only**, supports a shared secret
  (`X-Bridge-Key`) and rejects requests carrying a foreign `Origin` header.
* No credentials are ever sent to chat-deep.ai; the site requires no account.
* Do not paste passwords, API keys or payment data into chat prompts —
  messages are relayed by a third-party server to DeepSeek.

## Scope

This project automates a third-party website's public browser chat. Respect
chat-deep.ai's terms and published limits (50 messages/day, 10 Pro/day).
Reports of rate-limit circumvention or quota evasion are out of scope: those
limits are enforced deliberately and the client upholds them client-side too.
