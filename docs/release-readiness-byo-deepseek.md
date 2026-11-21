# BYO DeepSeek release-readiness record

Last verified: 2026-11-21 on macOS (Apple Silicon)

This document records the integration state of the community BYO-AI work on
`community-byo-production-hardening`. It is an evidence log, not a claim that
third-party data or platform services can be replaced by an LLM.

## Release gates

| Gate | State | Evidence |
| --- | --- | --- |
| Community AI uses a user-owned provider | Pass | DeepSeek/OpenAI-compatible provider routes are active; retired Ollama and Codex routes are rejected by provider policy. |
| Fincept credits are not consumed by AI workflows | Pass | The guarded live smoke completed chat, tool use, news analysis, and analytics explanation with the configured DeepSeek key and recorded zero Fincept-credit use. |
| Provider request limits are enforced | Pass | `LlmBudgetSettings.h` enforces the persisted history, input, output, summary-context, and summary-output budgets. The controls are exposed under Settings > LLM Configuration > Providers. |
| Cache accounting survives restart | Pass | Provider cache-hit usage is written through local usage metrics and covered by the live smoke diagnostic. |
| Native regression suite | Pass | 145 of 145 tests passed after the symbol-first workflow was added. |
| Analytics regression suite | Pass | 246 of 246 tests passed. |
| Packaged application diagnostics | Pass | All 14 flags returned by `FinceptTerminal --selftest-list` passed in the signed release bundle. |
| macOS bundle integrity | Pass for local distribution | `codesign --verify --deep --strict` passed after the final release rebuild. The bundle is ad-hoc signed. |
| Live unlocked UI walkthrough | Pending user action | The release reaches the secure six-digit PIN screen. AI Chat and symbol-command rendering still require the owner to unlock the running app locally. |
| Windows and Linux packages | Not verified | No Windows or Linux build host was available for this validation. |
| Apple distribution | Not complete | Developer ID signing, hardened-runtime entitlements, notarization, and stapling require the owner's Apple distribution identity. |

## Verified BYO AI routes

The opt-in live diagnostic (`--selftest-live-deepseek`) is guarded so it cannot
run accidentally. With an explicitly supplied DeepSeek key, it verifies:

1. normal chat completion;
2. a tool-capable request and tool-call parsing;
3. news-article analysis;
4. analytics explanation;
5. provider cache-hit reporting and persistence; and
6. zero Fincept-credit usage.

The diagnostic never prints or persists the API key. It does make controlled,
paid requests against the user's DeepSeek account when explicitly enabled.

## Symbol-first research workflow

The global command bar now accepts terminal-style commands and routes them to
existing first-party research views. Representative commands are:

| Command | Destination |
| --- | --- |
| `AAPL DES` | Equity Research > Overview |
| `MSFT financials` | Equity Research > Financials |
| `CF NVDA` | Equity Research > Filings |
| `TSLA technicals` | Equity Research > Technicals |
| `valuation AMZN` | Equity Research > Valuation |
| `META analyst ratings` | Equity Research > Analyst |
| `HDS GOOG` | Equity Research > Ownership |
| `NFLX ERN` | Equity Research > Earnings |
| `OMON AAPL` | Equity Research > Options |

Aliases are intentionally mapped to Fincept's own screens and data contracts.
No Godel Terminal code, licensed feed, or proprietary UI asset is included.

## External-data boundaries

DeepSeek can summarize, explain, rank, or orchestrate data, but it must not be
used as a substitute for factual market feeds.

- Delayed/public option chains can support exploratory positioning analytics,
  but exchange-grade open interest and real-time Greeks require a broker or
  licensed data source.
- Public market and macro feeds can power Market Pulse with provenance and
  freshness labels; they do not guarantee exchange-grade uptime or latency.
- Full AIS vessel positions and history require a user-owned maritime provider.
  The community credit firewall refuses paid Fincept maritime calls and directs
  the user to configure a BYO source.
- Broker execution, cloud sync, premium exchange feeds, and account services
  continue to require their respective servers and user-owned credentials.

## Remaining release blockers

1. Complete the unlocked visual walkthrough of AI Chat, the DeepSeek provider
   badge, Settings guardrails, and at least one symbol-first command.
2. Build and run the same test gates on Windows and Linux packaging hosts.
3. Replace ad-hoc signing with Developer ID signing and complete notarization.
4. Resolve or document the macOS deployment mismatch reported for Homebrew
   OpenSSL dylibs built for a newer macOS than the application's 13.0 target.
5. Revalidate live external feeds immediately before distribution because their
   availability, schemas, limits, and latency are outside this repository.

## Reproduction commands

From `fincept-qt`:

```sh
ctest --test-dir build/tdd --output-on-failure
python3 -m pytest scripts/Analytics/tests
build/macos-release/FinceptTerminal.app/Contents/MacOS/FinceptTerminal --selftest-list
codesign --verify --deep --strict build/macos-release/FinceptTerminal.app
```

Run `--selftest-live-deepseek` only with its explicit opt-in environment guard
and a disposable usage budget. Never place an API key in a command, test log,
source file, or commit.
