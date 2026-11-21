#pragma once

#include <QString>

namespace fincept::policy {

// Commercial screens remain reachable when the user explicitly opens them,
// but the community build does not interrupt startup with them.
inline constexpr bool automatic_commercial_prompts_enabled() {
    return false;
}

// Keep explicit account and pricing screens available, but do not place
// commercial CTAs in the normal research workspace.
inline constexpr bool commercial_promotions_enabled() {
    return false;
}

// Legacy local-server and Codex harness routes stay out of this product's
// automatic discovery and UI. DeepSeek is the supported BYO AI path.
inline constexpr bool deprecated_ai_backends_enabled() {
    return false;
}

// The community build is BYO/local-first. A false value is a hard safety
// boundary: services may replay free client caches, but must not start a
// Fincept request that can consume credits or paid quota.
inline constexpr bool fincept_metered_services_enabled() {
    return false;
}

// Saved conversations can outlive provider configuration. Treat routes removed
// from the community product as historical data only: they may be described in
// a transcript, but must never become selectable again.
inline bool is_deprecated_llm_provider(const QString& provider) {
    if (deprecated_ai_backends_enabled())
        return false;
    const QString id = provider.trimmed().toLower();
    return id == QLatin1String("ollama") || id == QLatin1String("codex") ||
           id == QLatin1String("harness:codex");
}

// All model and harness pickers must use this gate instead of maintaining
// local deny-lists. Historical rows may still describe a retired backend, but
// no saved configuration or registry entry may make it selectable again.
inline bool is_ai_backend_selectable(const QString& provider_or_harness) {
    const QString id = provider_or_harness.trimmed().toLower();
    if (is_deprecated_llm_provider(id))
        return false;
    return id != QLatin1String("fincept") || fincept_metered_services_enabled();
}

// Cloud Sync is a Fincept-hosted network service. Until a user-owned sync
// endpoint is supported, the community build keeps its database device-local.
inline constexpr bool fincept_cloud_sync_enabled() {
    return false;
}

// Chat Mode cloud turns and plans call Fincept-hosted agent endpoints and spend
// credits. Community tasks run through the configured local/BYO agent instead.
inline constexpr bool fincept_cloud_agents_enabled() {
    return false;
}

// Chat Mode's historical harness engine could revive Codex-era saved runs and
// schedules even after those choices disappeared from the UI. Community tasks
// use the configured DeepSeek/BYO path; runtime_engine() migrates old harness
// records to the local engine instead of registering a harness executor.
inline constexpr bool chat_mode_harnesses_enabled() {
    return false;
}

// Conversations, memories, profiles, MCP configuration and tool polling under
// /chat are Fincept-hosted. Community Chat Mode stores and runs tasks locally.
inline constexpr bool fincept_chat_api_enabled() {
    return false;
}

// The dashboard's legacy health lamp probed the Fincept backend even when
// every hosted product route was disabled. Community mode reports its own
// device-local runtime state instead; public data and BYO providers expose
// their health at their actual feature surfaces.
inline constexpr bool fincept_hosted_health_enabled() {
    return false;
}

// Symbol search and the symbol-master delta feed are Fincept-hosted. The
// community product uses its bundled/local index, broker masters and public
// Yahoo/DBnomics discovery instead.
inline constexpr bool fincept_hosted_symbol_services_enabled() {
    return false;
}

// An unconfigured installation must never select Fincept's metered provider.
// DeepSeek is the community BYO default: it requires the user's own API key,
// so a missing key fails visibly instead of silently spending Fincept credits.
inline QString default_llm_provider() {
    return QStringLiteral("deepseek");
}

inline QString default_llm_base_url() {
    return {};
}

inline QString default_llm_model() {
    return QStringLiteral("deepseek-flash");
}

// DeepSeek retired the legacy `deepseek-chat` / `deepseek-reasoner` aliases in
// July 2026. Migrate saved rows at every execution boundary while leaving
// current and future explicit model ids intact.
inline QString normalize_llm_model(const QString& provider, const QString& model) {
    const QString normalized_provider = provider.trimmed().toLower();
    const QString normalized_model = model.trimmed();
    if (normalized_provider == QLatin1String("deepseek")) {
        if (normalized_model.isEmpty() ||
            normalized_model.compare(QLatin1String("deepseek-flash"), Qt::CaseInsensitive) == 0 ||
            normalized_model.compare(QLatin1String("deepseek-chat"), Qt::CaseInsensitive) == 0 ||
            normalized_model.compare(QLatin1String("deepseek-reasoner"), Qt::CaseInsensitive) == 0)
            return default_llm_model();
        if (normalized_model.compare(QLatin1String("deepseek-v4-pro"), Qt::CaseInsensitive) == 0)
            return QStringLiteral("deepseek-v4-pro");
    }
    return normalized_model;
}

} // namespace fincept::policy
