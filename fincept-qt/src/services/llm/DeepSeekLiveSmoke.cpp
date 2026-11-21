#include "services/llm/DeepSeekLiveSmoke.h"

#include "core/metrics/LocalUsageMetrics.h"
#include "mcp/McpProvider.h"
#include "services/llm/LlmService.h"

#include <QJsonObject>
#include <QScopeGuard>

#include <atomic>
#include <cstdio>

namespace fincept::ai_chat {

int run_deepseek_live_smoke() {
    constexpr auto kProbeName = "live_deepseek_smoke_probe";
    auto& service = LlmService::instance();
    service.reload_config();

    for (const auto& item : deepseek_live_smoke_plan()) {
        const ChatRoute route{item.feature, QString(), QString()};
        const QJsonObject cfg = service.resolved_config_json(route);
        if (cfg.value(QStringLiteral("provider")).toString().compare(QLatin1String("deepseek"),
                                                                    Qt::CaseInsensitive) != 0 ||
            cfg.value(QStringLiteral("api_key")).toString().trimmed().isEmpty()) {
            std::fprintf(stderr, "live DeepSeek smoke: %s route is not configured with a DeepSeek API key\n",
                         qPrintable(item.id));
            return 2;
        }
    }

    std::atomic<int> probe_calls{0};
    mcp::ToolDef probe;
    probe.name = QString::fromLatin1(kProbeName);
    probe.description = QStringLiteral("Harmless live-smoke probe. Call exactly once when explicitly requested.");
    probe.category = QStringLiteral("system");
    probe.handler = [&probe_calls](const QJsonObject&) {
        probe_calls.fetch_add(1, std::memory_order_relaxed);
        return mcp::ToolResult::ok(QStringLiteral("live smoke probe completed"));
    };
    mcp::McpProvider::instance().register_tool(std::move(probe));
    const auto unregister_probe = qScopeGuard([probe_name = QString::fromLatin1(kProbeName)]() {
        mcp::McpProvider::instance().unregister_tool(probe_name);
    });

    const auto usage_before = metrics::LocalUsageMetrics::snapshot();
    qint64 observed_cached = 0;
    for (const auto& item : deepseek_live_smoke_plan()) {
        const int probes_before = probe_calls.load(std::memory_order_relaxed);
        const ChatRoute route{item.feature, QString(), QString()};
        const LlmResponse response = service.chat(item.prompt, {}, item.use_tools, route);
        const QString marker = QStringLiteral("DEEPSEEK_") + item.id.toUpper() + QStringLiteral("_OK");
        const bool provider_ok = response.provider.compare(QLatin1String("deepseek"), Qt::CaseInsensitive) == 0;
        const bool tool_ok = !item.use_tools || probe_calls.load(std::memory_order_relaxed) > probes_before;
        const bool usage_ok = response.prompt_tokens > 0 && response.completion_tokens > 0;
        const bool content_ok = response.content.contains(marker, Qt::CaseInsensitive);
        if (!response.success || !provider_ok || !tool_ok || !usage_ok || !content_ok) {
            std::fprintf(stderr,
                         "live DeepSeek smoke: %s FAILED (success=%d provider=%s tool=%d usage=%d marker=%d error=%s)\n",
                         qPrintable(item.id), response.success ? 1 : 0, qPrintable(response.provider), tool_ok ? 1 : 0,
                         usage_ok ? 1 : 0, content_ok ? 1 : 0, qPrintable(response.error.left(200)));
            return 3;
        }
        observed_cached += qMax(0, response.cached_prompt_tokens);
        std::printf("live DeepSeek smoke: %-9s PASS  in=%d out=%d cached=%d\n", qPrintable(item.id),
                    response.prompt_tokens, response.completion_tokens, response.cached_prompt_tokens);
    }

    const auto usage_after = metrics::LocalUsageMetrics::snapshot();
    const qint64 persisted_cached = usage_after.cached_prompt_tokens - usage_before.cached_prompt_tokens;
    if (persisted_cached < observed_cached) {
        std::fprintf(stderr, "live DeepSeek smoke: cache accounting was not persisted\n");
        return 4;
    }

    std::printf("live DeepSeek smoke: PASS (4 routes, probe calls=%d, cached input=%lld)\n",
                probe_calls.load(std::memory_order_relaxed), static_cast<long long>(observed_cached));
    std::fflush(stdout);
    return 0;
}

} // namespace fincept::ai_chat
