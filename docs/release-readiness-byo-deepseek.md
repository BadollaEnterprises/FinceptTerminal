# BYO DeepSeek release-readiness record

Last verified: 2026-11-22 on macOS (Apple Silicon)

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
| Native regression suite | Pass | 146 of 146 tests passed after the command-catalog regression was added. |
| Analytics regression suite | Pass | 246 of 246 tests passed. |
| Packaged application diagnostics | Pass | All 10 packaged CI self-tests passed with the bundled Qt offscreen plugin. |
| macOS bundle integrity | Pass for local distribution | `codesign --verify --deep --strict` passed after the final release rebuild. The bundle is ad-hoc signed. OpenSSL 3.5.9 is bundle-relative, arm64, and built for macOS 13.0. |
| Live unlocked UI walkthrough | Pass | The signed release was unlocked locally. AI Chat displayed `DeepSeek / deepseek-flash`, `0 credits`, and billing to the user's DeepSeek key; Settings displayed all five BYO guardrails; `AAPL DES` opened Overview and `MSFT financials` opened Financials. |
| Cross-platform packaging contracts | Pass locally; hosted rerun blocked | Hosted lint run [44961400096](https://github.com/BadollaEnterprises/FinceptTerminal/actions/runs/44961400096) passed the architecture-specific OpenSSL/zlib, Qt host-tool, PE-filter fixture, and release-workflow contracts on `31721fd`. Through `bbbbd33`, ten local packaging-script tests cover architecture filtering, release-plugin selection, VC dependency closure, and rejection of non-redistributable debug CRT/UCRT files; GitHub paused fork workflows before those revisions could receive a hosted rerun. |
| Linux package | Pass | Matrix run [44961406531](https://github.com/BadollaEnterprises/FinceptTerminal/actions/runs/44961406531) compiled and ran the Linux x64 self-tests; release run [44953568374](https://github.com/BadollaEnterprises/FinceptTerminal/actions/runs/44953568374) installed, opened, and removed the `.deb` on Ubuntu 22.04/24.04/26.04 and Debian 12/13. |
| Windows x64 package | Needs clean rebuild | Matrix run [44961406531](https://github.com/BadollaEnterprises/FinceptTerminal/actions/runs/44961406531) compiled, ran self-tests, and passed its x64 PE audit, but independent import-table inspection found three debug Qt plugins whose non-redistributable debug VC/UCRT dependencies were absent. Through `bbbbd33`, packaging excludes paired debug plugins and debug zlib, requires every VC redistributable inside the artifact, and bans debug CRT/UCRT files; the corrected artifact still needs a hosted rebuild. |
| Windows ARM64 package | Blocked by hosted CI | The application compiled and its executable passed the ARM64 check in run [44955322888](https://github.com/BadollaEnterprises/FinceptTerminal/actions/runs/44955322888). That run exposed an x64 compatibility DLL inside Visual Studio's ARM64 redist directory; `31721fd` filters CRT files by PE machine header, and the gates through `bbbbd33` additionally reject unstaged VC-runtime imports, debug Qt plugins, and debug CRT/UCRT files. The exact package retry was cancelled when GitHub automatically paused workflows on the fork for Actions usage, before ARM64 packaging ran. |
| Live public-data adapters | Pass on 2026-11-22 | AAPL technical rating, options positioning, analyst, ownership, and earnings adapters plus Market Pulse returned valid zero-credit public-data envelopes. The earnings result was valid but empty. |
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
| `HP AAPL` | Chart / price history |
| `MSFT financials` | Equity Research > Financials |
| `AAPL GF` | Equity Research > Financials |
| `CF NVDA` | Equity Research > Filings |
| `TSLA technicals` | Equity Research > Technicals |
| `valuation AMZN` | Equity Research > Valuation |
| `META analyst ratings` | Equity Research > Analyst |
| `HDS GOOG` | Equity Research > Ownership |
| `NFLX ERN` | Equity Research > Earnings |
| `OMON AAPL` | Equity Research > Options |
| `TAS MSFT` | Trading workspace |
| `HMS AAPL MSFT` | Multi-security comparison |
| `WCDS` | Derivatives > Credit (CDS analytics) |

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

1. Re-enable GitHub Actions for the fork and rebuild both Windows x64 and ARM64
   on `bbbbd33` or later. Each artifact must pass `check_windows_pe_arch.py`
   and `check_windows_dependency_closure.py`; independently inspect the
   uploaded artifacts before declaring either target ready.
2. Replace ad-hoc signing with Developer ID signing and complete notarization.
3. Repeat the live public-feed smoke immediately before distribution because
   upstream availability, schemas, limits, and latency remain outside this repository.

## Reproduction commands

From `fincept-qt`:

```sh
ctest --test-dir build/tdd --output-on-failure
/usr/bin/python3 -m unittest discover -s scripts/Analytics/tests -p 'test_*.py'
python3 ../.github/scripts/check_packaging_contracts.py ..
python3 ../.github/scripts/test_check_windows_pe_arch.py
python3 ../.github/scripts/test_check_windows_dependency_closure.py
python3 ../.github/scripts/test_copy_windows_release_plugins.py
../.github/scripts/build_macos_release.sh
../.github/scripts/ci_app_checks.sh selftests \
  build/macos-release/FinceptTerminal.app/Contents/MacOS/FinceptTerminal ci
../.github/scripts/macos_verify_openssl.sh \
  build/macos-release/FinceptTerminal.app/Contents/MacOS/FinceptTerminal 13.0
codesign --verify --deep --strict build/macos-release/FinceptTerminal.app
```

Run `--selftest-live-deepseek` only with its explicit opt-in environment guard
and a disposable usage budget. Never place an API key in a command, test log,
source file, or commit.
