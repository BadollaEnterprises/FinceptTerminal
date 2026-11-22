// Multi-provider LLM API client. Sync paths block on QEventLoop and must run on a worker thread.

#include "services/llm/LlmToolActivity.h"
#include "services/llm/LlmService.h"
#include "services/llm/LlmHistoryPolicy.h"

#include "core/policy/CommunityPolicy.h"
#include "core/metrics/LocalUsageMetrics.h"

#include "auth/AuthManager.h"
#include "core/config/AppConfig.h"
#include "core/logging/Logger.h"
#include "core/privacy/LogRedact.h"
#include "datahub/DataHub.h"
#include "datahub/TopicPolicy.h"
#include "mcp/GeminiSchema.h"
#include "mcp/McpProvider.h"
#include "mcp/McpService.h"
#include "services/llm/LlmContentExtractors.h"
#include "services/llm/LlmEndpointPolicy.h"
#include "services/llm/LlmErrors.h"
#include "services/llm/LlmRequestPolicy.h"
#include "services/llm/LlmSummaryPolicy.h"
#include "services/llm/LlmTransportPolicy.h"
#include "services/llm/LlmUsage.h"
#include "services/llm/ModelCatalog.h"
#include "services/llm/ModelRouter.h"
#include "services/llm/ProviderCatalog.h"
#include "storage/repositories/LlmConfigRepository.h"
#include "storage/repositories/SettingsRepository.h"

#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEvent>
#include <QEventLoop>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonValue>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPointer>
#include <QRegularExpression>
#include <QScopeGuard>
#include <QSemaphore>
#include <QSet>
#include <QThread>
#include <QThreadPool>
#include <QTimer>
#include <QUuid>
#include <QVariant>
#include <QtConcurrent/QtConcurrent>

#include <algorithm>
#include <memory>
#include <vector>

namespace fincept::ai_chat {

static constexpr const char* kLlmSvcTag = "LlmService";

static bool llm_svc_provider_allowed(const QString& provider) {
    if (policy::is_deprecated_llm_provider(provider))
        return false;
    return provider.compare(QLatin1String("fincept"), Qt::CaseInsensitive) != 0 ||
           policy::fincept_metered_services_enabled();
}

// The one INFO line per LLM request (policy B.4): who answered, how long, what it
// cost. Never the prompt or the answer.
static void llm_svc_log_request_finished(const LlmResponse& resp, const QString& feature, bool stream,
                                         qint64 duration_ms) {
    metrics::LocalUsageMetrics::record_llm(resp.provider, resp.prompt_tokens, resp.completion_tokens, resp.success,
                                           resp.cached_prompt_tokens);
    if (!Logger::instance().should_log(LogLevel::Info, kLlmSvcTag))
        return;
    QList<LogField> fields{{"provider", resp.provider},
                           {"model", resp.model},
                           {"feature", feature},
                           {"stream", stream},
                           {"duration_ms", duration_ms},
                           {"tokens_in", resp.prompt_tokens},
                           {"tokens_out", resp.completion_tokens},
                           {"result", resp.success ? "ok" : (resp.cancelled ? "cancelled" : "error")}};
    if (!resp.success && !resp.cancelled)
        fields.append(LogField("error", privacy::redact_text(resp.error)));
    LOG_INFO(kLlmSvcTag, logf("LLM request finished", fields));
}

// Every provider re-posts conversation history on each turn. Budget the whole
// request-side conversation, including the new user message, so an attachment
// cannot combine with old history and unexpectedly multiply BYO input spend.
// `llm/history_max_chars` overrides the cost-conscious default; 0 disables.
static qsizetype llm_svc_history_budget_chars() {
    LlmBudgetSettings budget_settings;
    budget_settings.history_chars = AppConfig::instance()
                                        .get(kLlmHistoryCharsKey, QVariant(kDefaultLlmHistoryChars))
                                        .toInt();
    return validate_llm_budget_settings(budget_settings).history_chars;
}

static QString llm_svc_input_budget_error(qsizetype current_message_chars) {
    const qsizetype budget = llm_svc_history_budget_chars();
    if (current_message_fits_request_budget(current_message_chars, budget))
        return {};
    return QStringLiteral("This message is %1 characters, above the configured %2-character BYO AI request "
                          "guardrail. No provider request was sent. Shorten the message or raise History Chars in "
                          "Settings → LLM Configuration.")
        .arg(current_message_chars)
        .arg(budget);
}

static std::vector<ConversationMessage> llm_svc_trim_history(const std::vector<ConversationMessage>& history,
                                                             qsizetype current_message_chars) {
    const qsizetype budget = llm_svc_history_budget_chars();
    HistoryTrimResult result = trim_history_for_request(history, current_message_chars, budget);
    if (result.dropped_messages > 0) {
        LOG_INFO(kLlmSvcTag,
                 QString("Conversation request trimmed from %1 to %2 history chars (new message %3, budget %4); "
                         "dropped %5 oldest message(s)")
                     .arg(result.original_chars)
                     .arg(result.kept_chars)
                     .arg(current_message_chars)
                     .arg(budget)
                     .arg(result.dropped_messages));
    }
    return std::move(result.messages);
}

// Executor for chat_streaming() requests. A turn can hold its thread for minutes
// (reasoning model + multi-round tool loop); on QThreadPool::globalInstance() that
// starved every other QtConcurrent user. Threads never expire, so each keeps its
// thread_nam() — and its warm TLS connection to the provider — between chat turns.
// Deliberately leaked: it must outlive every singleton a request touches, and a
// static pool would block process exit on an in-flight request.
static QThreadPool& llm_svc_request_pool() {
    static QThreadPool* const pool = []() {
        auto* p = new QThreadPool();
        p->setObjectName(QStringLiteral("fincept-llm"));
        p->setMaxThreadCount(6);
        p->setExpiryTimeout(-1);
        return p;
    }();
    return *pool;
}

// Soft cap on concurrent blocking chat() requests (workflow / agent nodes, the
// non-streaming AI Chat path), so a workflow fanning out many agent nodes can't
// hammer the provider into 429s. Interactive chat_streaming() requests never wait
// on it. Soft because chat() can legitimately be called while another request
// waits on it (a tool inside a running turn that itself calls chat()); a hard
// semaphore would deadlock that nesting once the cap is reached, so after
// kLlmSvcSlotWaitMs a request proceeds without a slot.
constexpr int kLlmSvcMaxConcurrentChats = 4;
constexpr int kLlmSvcSlotWaitMs = 30000;

class LlmSvcChatSlot {
  public:
    LlmSvcChatSlot() = default;
    ~LlmSvcChatSlot() {
        if (held_)
            semaphore().release();
    }
    LlmSvcChatSlot(const LlmSvcChatSlot&) = delete;
    LlmSvcChatSlot& operator=(const LlmSvcChatSlot&) = delete;

    /// Waits for a slot (or the soft-cap timeout). Returns false only when a
    /// request armed on this thread was cancelled while waiting.
    bool acquire() {
        QElapsedTimer waited;
        waited.start();
        while (!semaphore().tryAcquire(1, 100)) {
            if (detail::cancel_requested())
                return false;
            if (waited.elapsed() >= kLlmSvcSlotWaitMs) {
                LOG_WARN(kLlmSvcTag, QString("All %1 chat() slots busy for %2 s — proceeding without one")
                                         .arg(kLlmSvcMaxConcurrentChats)
                                         .arg(kLlmSvcSlotWaitMs / 1000));
                return true;
            }
        }
        held_ = true;
        return true;
    }

  private:
    static QSemaphore& semaphore() {
        static QSemaphore s(kLlmSvcMaxConcurrentChats);
        return s;
    }
    bool held_ = false;
};

// ── Fincept credentials, copied on AuthManager's thread ─────────────────────
// AuthManager's session is plain GUI-thread state, and it is now cleared while
// the app runs (handle_unauthorized forgets a replaced session token, logout,
// key rotation). Requests read the key and X-Session-Token on worker threads,
// so reading session() there raced those writes. AuthManager offers no
// thread-safe accessor (and gets no lock of ours), so this copy is written ONLY
// on AuthManager's thread — on every auth_state_changed / logged_out, and
// whenever a caller on that thread asks — and read by everyone else under its
// own mutex. Lock order: LlmService::mutex_ (ensure_config) → this one.
namespace {
QMutex g_llm_svc_creds_mutex;
QString g_llm_svc_creds_key;   // guarded by g_llm_svc_creds_mutex
QString g_llm_svc_creds_token; // guarded by g_llm_svc_creds_mutex
bool g_llm_svc_creds_seeded = false;
} // namespace

// AuthManager's thread only.
static void llm_svc_refresh_creds_on_auth_thread() {
    auto& am = fincept::auth::AuthManager::instance();
    const QString key = am.fincept_api_key();
    const QString token = am.session().session_token;
    QMutexLocker lock(&g_llm_svc_creds_mutex);
    g_llm_svc_creds_key = key;
    g_llm_svc_creds_token = token;
    g_llm_svc_creds_seeded = true;
}

static void llm_svc_install_creds_mirror() {
    [[maybe_unused]] static const bool installed = []() {
        auto* am = &fincept::auth::AuthManager::instance();
        QObject::connect(am, &fincept::auth::AuthManager::auth_state_changed, am,
                         &llm_svc_refresh_creds_on_auth_thread);
        QObject::connect(am, &fincept::auth::AuthManager::logged_out, am, &llm_svc_refresh_creds_on_auth_thread);
        if (QThread::currentThread() == am->thread())
            llm_svc_refresh_creds_on_auth_thread();
        else
            QMetaObject::invokeMethod(am, &llm_svc_refresh_creds_on_auth_thread, Qt::QueuedConnection);
        return true;
    }();
}

LlmService::FinceptCredentials LlmService::fincept_credentials() {
    llm_svc_install_creds_mirror();
    auto& am = fincept::auth::AuthManager::instance();
    if (QThread::currentThread() == am.thread()) {
        llm_svc_refresh_creds_on_auth_thread(); // live values, and the copy stays current
    } else {
        QMutexLocker lock(&g_llm_svc_creds_mutex);
        if (!g_llm_svc_creds_seeded) {
            // A worker got here before the queued first copy ran (only possible
            // when LlmService is first used off the GUI thread at startup): read
            // directly once, as every request used to.
            lock.unlock();
            return FinceptCredentials{am.fincept_api_key(), am.session().session_token};
        }
    }
    QMutexLocker lock(&g_llm_svc_creds_mutex);
    return FinceptCredentials{g_llm_svc_creds_key, g_llm_svc_creds_token};
}

QString LlmService::handle_fincept_refusal(const HttpResult& r, const QString& sent_session_token) {
    if (r.status == 401 && r.session_expired && !sent_session_token.isEmpty()) {
        // The server says this token is dead: stop sending it from this turn's
        // next round on, before AuthManager's thread has processed the report.
        QMutexLocker lock(&g_llm_svc_creds_mutex);
        if (g_llm_svc_creds_token == sent_session_token)
            g_llm_svc_creds_token.clear();
    }
    const QString error = report_fincept_refusal(r.status, r.body, sent_session_token, r.session_expired);
    if (r.status == 401) {
        // Re-copy once AuthManager has acted on the report (queued behind it):
        // the key or token held here may be the stale half.
        QMetaObject::invokeMethod(&fincept::auth::AuthManager::instance(), &llm_svc_refresh_creds_on_auth_thread,
                                  Qt::QueuedConnection);
    }
    return error;
}

LlmService::LlmService() = default;

LlmService& LlmService::instance() {
    static LlmService s;
    // Start mirroring AuthManager as early as possible (normally on the GUI thread).
    llm_svc_install_creds_mirror();
    return s;
}

void LlmService::reload_config() {
    {
        QMutexLocker lock(&mutex_);
        config_loaded_ = false;
        ensure_config();
    }
    emit config_changed();
}

void LlmService::ensure_config() const {
    // Called with mutex_ held. Cache is one-shot except for Fincept credentials —
    // those re-resolve every call so a login that happens after first ensure_config() doesn't 401 forever.
    if (config_loaded_) {
        if (provider_ == "fincept") {
            // Resolve via AuthManager (session → SecureStorage), through the
            // thread-safe copy: this runs on request threads too. Never the
            // legacy plaintext settings row (CR-08).
            const QString key = fincept_credentials().api_key;
            if (!key.isEmpty())
                api_key_ = key;
        }
        return;
    }

    provider_ = model_ = api_key_ = base_url_ = system_prompt_ = {};
    config_error_.clear();
    temperature_ = 0.7;
    // 0 means "no user override → ModelCatalog::output_cap()". The v15 migration reset legacy 2000 defaults.
    max_tokens_ = 0;
    tools_enabled_ = true;

    auto providers = LlmConfigRepository::instance().list_providers();
    if (providers.is_ok()) {
        for (const auto& c : providers.value()) {
            if (c.is_active && llm_svc_provider_allowed(c.provider)) {
                provider_ = c.provider.toLower();
                api_key_ = c.api_key;
                base_url_ = c.base_url;
                model_ = c.model;
                tools_enabled_ = c.tools_enabled;
                break;
            }
        }
        // No allowed active provider — pick the first allowed user-owned row.
        if (provider_.isEmpty()) {
            for (const auto& c : providers.value()) {
                if (!llm_svc_provider_allowed(c.provider))
                    continue;
                provider_ = c.provider.toLower();
                api_key_ = c.api_key;
                base_url_ = c.base_url;
                model_ = c.model;
                tools_enabled_ = c.tools_enabled;
                break;
            }
        }
    } else {
        config_error_ = QStringLiteral("Could not read LLM configuration: %1")
                            .arg(QString::fromStdString(providers.error()));
        LOG_ERROR(kLlmSvcTag, privacy::redact_text(config_error_));
    }

    // Nothing configured — use the BYO DeepSeek route. Without the user's key
    // it fails visibly; a metered Fincept provider is never selected silently.
    if (provider_.isEmpty()) {
        provider_ = policy::default_llm_provider();
        model_ = policy::default_llm_model();
        base_url_ = policy::default_llm_base_url();
        LOG_INFO(kLlmSvcTag, "No LLM provider configured — using BYO DeepSeek default");
    }
    model_ = policy::normalize_llm_model(provider_, model_);

    // Fincept key resolves via AuthManager (live session → encrypted
    // SecureStorage). The legacy plaintext settings row is no longer read (CR-08).
    if (provider_ == "fincept") {
        const QString key = fincept_credentials().api_key;
        if (!key.isEmpty())
            api_key_ = key;
    }

    auto gs = LlmConfigRepository::instance().get_global_settings();
    if (gs.is_ok()) {
        temperature_ = gs.value().temperature;
        max_tokens_ = gs.value().max_tokens;
        system_prompt_ = gs.value().system_prompt;
        // Clamp [1, 200] so a corrupt DB row can't lock the loop at 0 or run forever.
        max_tool_rounds_ = std::clamp(gs.value().max_tool_rounds, 1, 200);
    } else {
        const QString error = QStringLiteral("Could not read global LLM settings: %1")
                                  .arg(QString::fromStdString(gs.error()));
        if (config_error_.isEmpty())
            config_error_ = error;
        else
            config_error_ += QLatin1Char('\n') + error;
        LOG_ERROR(kLlmSvcTag, privacy::redact_text(error));
    }

    // Default system prompt — primes the model to use the terminal's tools instead of
    // declining tool-feasible requests. Kept short: it is re-sent on every tool round.
    if (system_prompt_.trimmed().isEmpty()) {
        system_prompt_ =
            "You are Fincept AI, the assistant inside Fincept Terminal, a professional financial terminal. "
            "You work the terminal the way the user does, through tools: ui_overview (what is open — call it "
            "first), ui_open (open any screen, optionally on a symbol), ui_snapshot (a panel's controls, each "
            "with a ref), ui_act (click, type, select by ref or by the words on a control; several steps per "
            "call), ui_read (a table, text or chart in full), ui_wait (until something loads), ui_screenshot, "
            "ui_workspace (arrange panels and layouts), ui_invoke (the terminal's own commands) and python "
            "(find and run the bundled analytics scripts).\n"
            "\n"
            "Rules:\n"
            "- Use the tools whenever a request needs live data or an action in the terminal; never say you "
            "cannot open screens or read data. Greetings and questions you can answer from knowledge need no "
            "tool call.\n"
            "- Look before you act: ui_overview, then ui_snapshot the panel you need, then act by ref. After "
            "starting a load, ui_wait(until=\"idle\") before reading results.\n"
            "- Every figure you report must come from what the terminal shows or a script returns. Never "
            "invent numbers.\n"
            "- For analytics: python(action=\"search\") to find a script, action=\"info\" for its commands, "
            "then run it.\n"
            "- Orders, money moves, spends and deletions ask the user in the terminal first; if they decline, "
            "stop and say so. Never try to read or type secrets (API keys, passwords, PINs).\n"
            "- Finish with a concise answer in chat: the findings, the numbers and where they came from.";
    }

    config_loaded_ = true;
    const int resolved = resolved_max_tokens();
    const int catalog_cap = ModelCatalog::output_cap(provider_, model_);
    LOG_INFO(kLlmSvcTag, QString("LLM config loaded: provider=%1 model=%2 tools_enabled=%3 "
                                 "max_tokens(user=%4 catalog=%5 resolved=%6)")
                             .arg(provider_, model_, tools_enabled_ ? "TRUE" : "FALSE")
                             .arg(max_tokens_)
                             .arg(catalog_cap)
                             .arg(resolved));
}

LlmService::ConfigSnapshot LlmService::snapshot_config_locked() const {
    // Called with mutex_ held, after ensure_config().
    ConfigSnapshot s;
    s.provider = provider_;
    s.api_key = api_key_;
    s.base_url = base_url_;
    s.model = model_;
    s.system_prompt = system_prompt_;
    s.config_error = config_error_;
    s.temperature = temperature_;
    s.max_tokens = max_tokens_;
    s.max_tool_rounds = max_tool_rounds_;
    s.tools_enabled = tools_enabled_;
    s.tool_filter = tool_filter_;
    s.harness_effort = harness_effort_;
    s.feature = route_feature_;
    s.harness_require_key_source = harness_require_key_source_;
    return s;
}

void LlmService::adopt_config_snapshot(const ConfigSnapshot& snap) {
    // Only ever called on a private per-request instance, before its request
    // starts and on the thread that runs it.
    QMutexLocker lock(&mutex_);
    provider_ = snap.provider;
    api_key_ = snap.api_key;
    base_url_ = snap.base_url;
    model_ = snap.model;
    system_prompt_ = snap.system_prompt;
    config_error_ = snap.config_error;
    temperature_ = snap.temperature;
    max_tokens_ = snap.max_tokens;
    max_tool_rounds_ = snap.max_tool_rounds;
    tools_enabled_ = snap.tools_enabled;
    tool_filter_ = snap.tool_filter;
    harness_effort_ = snap.harness_effort;
    route_feature_ = snap.feature;
    harness_require_key_source_ = snap.harness_require_key_source;
    config_loaded_ = true; // never re-read the DB; Fincept still refreshes its key
}

// ── Routes (ModelRouter) ─────────────────────────────────────────────────────

QVector<ModelChoice> LlmService::route_chain(const ChatRoute& route) {
    const QString feature = route.feature.isEmpty() ? QString::fromLatin1(kFeatureAiChat) : route.feature;
    QVector<ModelChoice> chain = ModelRouter::instance().resolve_chain(feature, route.context_id);
    if (!route.skip_harness.isEmpty()) {
        chain.erase(std::remove_if(chain.begin(), chain.end(),
                                   [&route](const ModelChoice& c) {
                                       return c.is_harness() && c.harness_id == route.skip_harness;
                                   }),
                    chain.end());
    }
    if (chain.isEmpty()) {
        ModelChoice byo;
        byo.kind = ModelChoice::Kind::Api;
        byo.provider = policy::default_llm_provider();
        byo.model = policy::default_llm_model();
        byo.label = QStringLiteral("DeepSeek · ") + byo.model;
        byo.reason = QStringLiteral("byo_fallback");
        byo.vendor = QStringLiteral("DeepSeek");
        chain.append(byo);
    }
    return chain;
}

LlmService::ConfigSnapshot LlmService::snapshot_for(const ModelChoice& choice, const QString& feature) const {
    ConfigSnapshot s;
    if (!choice.config_error.isEmpty()) {
        s.provider = choice.provider.toLower();
        s.model = choice.model;
        s.feature = feature;
        s.tools_enabled = false;
        s.config_error = choice.config_error;
        return s;
    }
    QString legacy_provider;
    {
        QMutexLocker lock(&mutex_);
        ensure_config();
        s = snapshot_config_locked(); // globals + the active provider row (what LlmService always used)
        legacy_provider = provider_;
    }
    s.feature = feature;
    s.harness_effort.clear();
    s.harness_require_key_source = true;
    switch (choice.kind) {
        case ModelChoice::Kind::Harness:
            s.provider = choice.provider_id();
            s.model = choice.model;
            s.api_key.clear(); // a harness authenticates with the user's own CLI setup
            s.base_url.clear();
            s.tools_enabled = true; // the request's ToolPolicy still decides
            s.harness_effort = choice.effort;
            s.harness_require_key_source = choice.require_api_key_source;
            break;
        case ModelChoice::Kind::Fincept: {
            const bool row_is_fincept = legacy_provider == QLatin1String("fincept");
            s.provider = QStringLiteral("fincept");
            if (!choice.model.isEmpty())
                s.model = choice.model;
            else if (!row_is_fincept)
                s.model = QStringLiteral("MiniMax-M2.7");
            s.base_url.clear();
            if (!row_is_fincept)
                s.tools_enabled = true;
            s.api_key = fincept_credentials().api_key;
            break;
        }
        case ModelChoice::Kind::Api:
            {
            bool applied_profile = false;
            if (!choice.profile_id.isEmpty()) {
                const auto p = LlmProfileRepository::instance().get_profile(choice.profile_id);
                if (p.is_ok()) {
                    const LlmProfile& prof = p.value();
                    s.provider = prof.provider.toLower();
                    s.model = prof.model_id;
                    s.api_key = prof.api_key;
                    s.base_url = prof.base_url;
                    s.temperature = prof.temperature;
                    if (prof.max_tokens > 0)
                        s.max_tokens = prof.max_tokens;
                    if (!prof.system_prompt.trimmed().isEmpty())
                        s.system_prompt = prof.system_prompt;
                    s.tools_enabled = true;
                    applied_profile = true;
                } else {
                    s.provider = choice.provider.toLower();
                    s.model = choice.model;
                    s.api_key.clear();
                    s.base_url = ProviderCatalog::default_base_url(s.provider);
                    s.tools_enabled = true;
                    s.config_error = QStringLiteral("Could not read selected LLM profile: %1")
                                         .arg(QString::fromStdString(p.error()));
                    applied_profile = true;
                }
            }
            // A route fallback can differ from the active settings row (for
            // example, a saved Ollama row is rejected and DeepSeek is chosen).
            // Adopt the chosen provider's own row, or a clean keyless snapshot,
            // instead of accidentally executing the rejected active backend.
            if (!applied_profile && s.provider.compare(choice.provider, Qt::CaseInsensitive) != 0) {
                bool found = false;
                auto rows = LlmConfigRepository::instance().list_providers();
                if (rows.is_ok()) {
                    for (const LlmConfig& row : rows.value()) {
                        if (row.provider.compare(choice.provider, Qt::CaseInsensitive) != 0)
                            continue;
                        s.provider = row.provider.toLower();
                        s.model = row.model;
                        s.api_key = row.api_key;
                        s.base_url = row.base_url;
                        s.tools_enabled = row.tools_enabled;
                        found = true;
                        break;
                    }
                } else {
                    s.config_error = QStringLiteral("Could not read LLM configuration: %1")
                                         .arg(QString::fromStdString(rows.error()));
                }
                if (!found) {
                    s.provider = choice.provider.toLower();
                    s.model = choice.model;
                    s.api_key.clear();
                    s.base_url = ProviderCatalog::default_base_url(s.provider);
                    s.tools_enabled = true;
                }
            }
            if (s.model.isEmpty())
                s.model = choice.model;
            break;
            }
    }
    if (!choice.is_harness())
        s.model = policy::normalize_llm_model(s.provider, s.model);
    return s;
}

void LlmService::stamp_route(LlmResponse& resp, const ModelChoice& choice, const ConfigSnapshot& cfg,
                             const QString& fallback_notice) {
    resp.provider = choice.provider_id();
    if (resp.model.isEmpty())
        resp.model = cfg.model; // a harness reports its own; the others answer with the configured one
    resp.route_label = choice.label;
    if (choice.is_harness() && !resp.model.isEmpty() && resp.model != choice.model)
        resp.route_label = ModelRouter::harness_label(choice.label.section(QStringLiteral(" · "), 0, 0), resp.model,
                                                      choice.effort);
    resp.fallback_notice = fallback_notice;
    resp.billing_note = ModelRouter::billing_note(choice);
}

// ============================================================================
// Accessors
// ============================================================================

QString LlmService::active_provider() const {
    // What answers AI Chat now: the first choice of its route.
    return ModelRouter::instance().resolve(QString::fromLatin1(kFeatureAiChat)).provider_id();
}

QString LlmService::active_model() const {
    const ModelChoice c = ModelRouter::instance().resolve(QString::fromLatin1(kFeatureAiChat));
    if (!c.model.isEmpty() || c.is_harness())
        return c.model; // a harness without a model = the CLI's default
    QMutexLocker lock(&mutex_);
    ensure_config();
    return c.is_fincept() && provider_ != QLatin1String("fincept") ? QStringLiteral("MiniMax-M2.7") : model_;
}

QString LlmService::active_api_key() const {
    QMutexLocker lock(&mutex_);
    ensure_config();
    return api_key_;
}

QString LlmService::active_base_url() const {
    QMutexLocker lock(&mutex_);
    ensure_config();
    return base_url_;
}

double LlmService::active_temperature() const {
    QMutexLocker lock(&mutex_);
    ensure_config();
    return temperature_;
}

int LlmService::active_max_tokens() const {
    QMutexLocker lock(&mutex_);
    ensure_config();
    return max_tokens_;
}

QJsonObject LlmService::resolved_config_json(const ChatRoute& route) const {
    const QString feature = route.feature.isEmpty() ? QString::fromLatin1(kFeatureAiChat) : route.feature;
    const QVector<ModelChoice> chain = route_chain(route);
    if (chain.isEmpty() || chain.constFirst().is_harness())
        return {};

    const ConfigSnapshot cfg = snapshot_for(chain.constFirst(), feature);
    if (!cfg.config_error.isEmpty()) {
        QJsonObject out;
        out[QStringLiteral("configuration_error")] = privacy::redact_text(cfg.config_error);
        return out;
    }
    if (!llm_svc_provider_allowed(cfg.provider) || ProviderCatalog::is_blocked(cfg.provider, cfg.base_url) ||
        (ProviderCatalog::requires_api_key(cfg.provider) && cfg.api_key.trimmed().isEmpty()))
        return {};

    QJsonObject out;
    out[QStringLiteral("provider")] = cfg.provider;
    out[QStringLiteral("model_id")] = cfg.model;
    out[QStringLiteral("api_key")] = cfg.api_key;
    out[QStringLiteral("base_url")] = cfg.base_url;
    out[QStringLiteral("temperature")] = cfg.temperature;
    const int catalog_cap = ModelCatalog::output_cap(cfg.provider, cfg.model);
    out[QStringLiteral("max_tokens")] = resolve_llm_output_tokens(cfg.max_tokens, catalog_cap);
    return out;
}

int LlmService::active_max_tool_rounds() const {
    QMutexLocker lock(&mutex_);
    ensure_config();
    return max_tool_rounds_;
}

bool LlmService::tools_enabled() const {
    QMutexLocker lock(&mutex_);
    ensure_config();
    return tools_enabled_;
}

bool LlmService::is_configured() const {
    return is_configured(ChatRoute{});
}

bool LlmService::is_configured(const ChatRoute& route) const {
    const QString feature = route.feature.isEmpty() ? QString::fromLatin1(kFeatureAiChat) : route.feature;
    const QVector<ModelChoice> chain = route_chain(route);
    if (chain.isEmpty())
        return false;
    const ModelChoice& c = chain.constFirst();
    if (c.is_harness())
        return true;

    // Resolve the exact profile/provider row the request would use. The router
    // deliberately keeps a keyless DeepSeek fallback in its chain so the UI can
    // explain what to configure; that fallback must not make the composer look
    // ready or trigger an unauthenticated network request.
    const ConfigSnapshot cfg = snapshot_for(c, feature);
    if (!llm_svc_provider_allowed(cfg.provider) || ProviderCatalog::is_blocked(cfg.provider, cfg.base_url))
        return false;
    return !ProviderCatalog::requires_api_key(cfg.provider) || !cfg.api_key.trimmed().isEmpty();
}

ResolvedLlmProfile LlmService::resolve_profile(const QString& context_type, const QString& context_id) const {
    return LlmProfileRepository::instance().resolve_for_context(context_type, context_id);
}

// static
QJsonObject LlmService::profile_to_json(const ResolvedLlmProfile& p) {
    // Only fields the Python model constructor accepts. profile_id / profile_name are internal metadata —
    // sending them lands in extra_model_kwargs and fails as unexpected kwargs.
    QJsonObject obj;
    if (!p.config_error.isEmpty()) {
        obj[QStringLiteral("configuration_error")] = privacy::redact_text(p.config_error);
        return obj;
    }
    obj["provider"] = p.provider;
    obj["model_id"] = p.model_id;
    obj["api_key"] = p.api_key;
    obj["base_url"] = p.base_url;
    obj["temperature"] = p.temperature;
    // Apply the same automatic allowance and per-model hard ceiling used by
    // interactive chat. Python agents must not bypass the cost and validity
    // guardrails merely because their configuration crosses a JSON boundary.
    const int catalog_cap = ModelCatalog::output_cap(p.provider, p.model_id);
    obj["max_tokens"] = resolve_llm_output_tokens(p.max_tokens, catalog_cap);
    if (!p.system_prompt.isEmpty())
        obj["system_prompt"] = p.system_prompt;
    return obj;
}

int LlmService::resolved_max_tokens() const {
    // Called with mutex_ held.
    const int catalog_cap = ModelCatalog::output_cap(provider_, model_);
    return resolve_llm_output_tokens(max_tokens_, catalog_cap);
}

QString LlmService::get_endpoint_url() const {
    // Called with mutex_ held.
    const QString& p = provider_;

    // Blocked provider (AtlasCloud, removed) — refuse to resolve any endpoint,
    // including a base_url that was hand-pointed at the host. do_request /
    // do_streaming_request already block earlier with a clear message; this is
    // the last-line backstop for any other caller.
    if (ProviderCatalog::is_blocked(p, base_url_))
        return {};

    // Fincept sync chat endpoint (async lives in fincept_async_request).
    // The only provider whose route is not derivable from a base_url, so it is
    // the only one ProviderCatalog::chat_endpoint leaves to the caller.
    if (p == "fincept" && !policy::fincept_metered_services_enabled())
        return {};
    if (p == "fincept")
        return fincept::AppConfig::instance().api_base_url() + "/research/chat";

    // Everything else — default hosts, custom/proxy base_urls, and Gemini's
    // native `:generateContent` path — is composed by ProviderCatalog.
    //
    // This used to be a hand-maintained copy of that function, kept in step by
    // a comment. It drifted: ProviderCatalog grew a `default_base_url` fallback
    // for providers whose host lives there rather than in the switch (AstraFlow,
    // AstraFlow CN), while this copy special-cased only AIHubMix and returned an
    // empty string for the other two. A user who cleared the prefilled base_url
    // then got "No endpoint URL for provider: astraflow" from chat while every
    // UI surface, reading ProviderCatalog, showed a working endpoint. Delegating
    // removes the mirror rather than re-synchronising it.
    return ProviderCatalog::chat_endpoint(p, base_url_, model_);
}

QMap<QString, QString> LlmService::get_headers() const {
    // Called with mutex_ held.
    QMap<QString, QString> h;
    const QString& p = provider_;

    if (p == "anthropic") {
        if (!api_key_.isEmpty())
            h["x-api-key"] = api_key_;
        h["anthropic-version"] = "2023-06-01";
    } else if (p == "gemini" || p == "google") {
        if (!api_key_.isEmpty())
            h["x-goog-api-key"] = api_key_;
    } else if (p == "fincept") {
        if (!api_key_.isEmpty())
            h["X-API-Key"] = api_key_;
        // session_token only lives in AuthManager. This runs on request threads,
        // so it is read through the copy made on AuthManager's thread (never
        // session() itself, which that thread may be clearing right now).
        const QString session_token = fincept_credentials().session_token;
        if (!session_token.isEmpty())
            h["X-Session-Token"] = session_token;
        h["User-Agent"] = "FinceptTerminal/4.0"; // Cloudflare requires it.
    } else {
        if (!api_key_.isEmpty())
            h["Authorization"] = "Bearer " + api_key_;
        if (p == "openrouter") {
            // Attribution for the openrouter.ai/rankings leaderboard.
            h["HTTP-Referer"] = "https://fincept.in";
            h["X-Title"] = "Fincept Terminal";
        }
    }
    return h;
}

LlmResponse LlmService::do_request(const QString& user_message, const std::vector<ConversationMessage>& history) {
    LlmResponse resp;

    // Final execution boundary. Config loading and ModelRouter already filter
    // these providers, but a manually constructed snapshot must not revive a
    // retired Ollama/Codex route or the metered Fincept backend.
    if (!llm_svc_provider_allowed(provider_)) {
        resp.error = QStringLiteral("This AI backend is disabled. Configure a user-owned provider such as DeepSeek.");
        return resp;
    }
    if (!config_error_.isEmpty()) {
        resp.error = config_error_;
        return resp;
    }
    if (ProviderCatalog::requires_api_key(provider_) && api_key_.trimmed().isEmpty()) {
        resp.error = QStringLiteral(
            "No LLM provider configured: add your own API key in Settings › LLM Configuration.");
        return resp;
    }

    // An agent harness (ModelRouter route "harness:<id>"): the same branch point
    // as the Fincept special case below.
    if (ProviderCatalog::is_harness(provider_))
        return do_harness_request(user_message, history, {});

    if (ProviderCatalog::is_blocked(provider_, base_url_)) {
        resp.error = "AtlasCloud has been banned by Fincept and cannot be used.";
        return resp;
    }

    QString url = get_endpoint_url();
    if (url.isEmpty()) {
        resp.error = "No endpoint URL for provider: " + provider_;
        return resp;
    }
    if (!is_allowed_llm_endpoint(url)) {
        resp.error = QStringLiteral(
            "Refusing an insecure LLM endpoint. Use https://, or http:// only for a loopback address.");
        return resp;
    }

    auto hdr = get_headers();
    QJsonObject req_body;

    if (provider_ == "anthropic") {
        req_body = build_anthropic_request(user_message, history, false);
    } else if (provider_ == "gemini" || provider_ == "google") {
        req_body = build_gemini_request(user_message, history);
        // Auth via x-goog-api-key header — do NOT append ?key= (would leak into access logs).
    } else if (provider_ == "fincept") {
        // Fincept uses two separate endpoints:
        // Primary response → async (submit + poll, returns richer model output)
        // Follow-ups (tool results) → sync /research/chat
        LOG_DEBUG(kLlmSvcTag, "do_request: routing to fincept_async_request");
        return fincept_async_request(user_message, history);
    } else {
        req_body = build_openai_request(user_message, history, false, true);
    }

    LOG_DEBUG(kLlmSvcTag, QString("POST %1 provider=%2 model=%3").arg(log_url(url), provider_, model_));

    auto http = blocking_post(url, req_body, hdr);

    if (!http.success) {
        resp.error = http.error;
        if (http.cancelled)
            LOG_DEBUG(kLlmSvcTag, logf("Chat request cancelled", {{"provider", provider_}, {"model", model_}}));
        else
            LOG_ERROR(kLlmSvcTag, logf("Chat request failed", {{"provider", provider_},
                                                               {"model", model_},
                                                               {"status", http.status},
                                                               {"error", privacy::redact_text(http.error)}}));
        return resp;
    }

    auto doc = QJsonDocument::fromJson(http.body);
    if (doc.isNull()) {
        resp.error = "Failed to parse response JSON";
        LOG_ERROR(kLlmSvcTag, logf("Chat response is not valid JSON",
                                   {{"provider", provider_}, {"model", model_}, {"bytes", http.body.size()}}));
        return resp;
    }
    QJsonObject rj = doc.object();

    if (provider_ == "anthropic") {
        QJsonArray content = rj["content"].toArray();
        QString stop_reason = rj["stop_reason"].toString();

        // Check for tool_use blocks
        if (stop_reason == "tool_use") {
            LOG_DEBUG(kLlmSvcTag, "Anthropic requested tool_use");

            LlmResponse ant_usage;
            parse_usage(ant_usage, rj, provider_);
            ProviderUsageScope preserve_usage(resp, ant_usage);

            // Build follow-up messages: original + assistant turn + tool results
            QJsonArray loop_msgs;
            if (!system_prompt_.isEmpty()) {
            } // Anthropic puts system at top level, not in messages.
            for (const auto& h : history)
                if (h.role != "system")
                    loop_msgs.append(QJsonObject{{"role", h.role}, {"content", h.content}});
            loop_msgs.append(QJsonObject{{"role", "user"}, {"content", user_message}});
            loop_msgs.append(QJsonObject{{"role", "assistant"}, {"content", content}});

            QJsonArray tool_results;
            for (const auto& block_val : content) {
                QJsonObject block = block_val.toObject();
                if (block["type"].toString() != "tool_use")
                    continue;
                if (detail::cancel_requested()) {
                    resp.cancelled = true;
                    resp.error = "Request cancelled";
                    return resp;
                }
                QString tool_id = block["id"].toString();
                QString tool_name = block["name"].toString();
                QJsonObject input = block["input"].toObject();

                LOG_DEBUG(kLlmSvcTag, "Executing Anthropic tool: " + tool_name);
                const QString bare = mcp::McpProvider::parse_openai_function_name(tool_name).second;
                detail::emit_tool_progress(bare, input);
                QElapsedTimer tool_timer;
                tool_timer.start();
                auto tr = mcp::McpService::instance().execute_openai_function(tool_name, input);
                emit_tool_done(bare, input, tr, tool_timer.elapsed());
                tool_results.append(
                    QJsonObject{{"type", "tool_result"},
                                {"tool_use_id", tool_id},
                                {"content", detail::encode_tool_result_for_llm(bare, tr)}});
            }

            loop_msgs.append(QJsonObject{{"role", "user"}, {"content", tool_results}});

            // ── Multi-round tool loop ──
            // Previously this was a SINGLE follow-up with NO tools attached, so
            // the model could execute exactly one tool and never chain a second
            // (multi-step flows like "create a portfolio then add assets" broke).
            // We now loop, re-advertising the tool set each round, until the
            // model returns a final text answer or we hit the round cap.
            const int kMaxRounds = active_max_tool_rounds();
            // Rounds cap the token spend; the deadline caps the wait. See ToolLoopBudget.
            detail::ToolLoopBudget ant_budget(kMaxRounds);
            ant_budget.note_prompt_tokens(ant_usage.prompt_tokens);
            ant_budget.note_completion_tokens(ant_usage.completion_tokens);
            for (int round = 0; !ant_budget.exhausted(); ++round) {
                if (detail::cancel_requested()) {
                    resp.cancelled = true;
                    resp.error = "Request cancelled";
                    return resp;
                }
                ant_budget.note_round();
                const QJsonArray ant_tools = build_anthropic_tools();
                QJsonObject fu;
                fu["model"] = model_;
                fu["messages"] = loop_msgs;
                fu["max_tokens"] = ant_budget.clamp_completion_allowance(resolved_max_tokens());
                if (!system_prompt_.isEmpty())
                    fu["system"] = system_prompt_;
                if (!ant_tools.isEmpty())
                    fu["tools"] = ant_tools;

                auto fu_http = blocking_post(url, fu, hdr);
                if (!fu_http.success) {
                    resp.error = "Anthropic tool follow-up failed: " + fu_http.error;
                    return resp;
                }
                auto fu_doc = QJsonDocument::fromJson(fu_http.body);
                if (fu_doc.isNull()) {
                    resp.error = "Anthropic tool follow-up parse error";
                    return resp;
                }
                const QJsonObject frj = fu_doc.object();
                const QJsonArray fcontent = frj["content"].toArray();
                LlmResponse round_usage;
                parse_usage(round_usage, frj, provider_);
                add_provider_usage(ant_usage, round_usage);
                ant_budget.note_prompt_tokens(round_usage.prompt_tokens);
                ant_budget.note_completion_tokens(round_usage.completion_tokens);

                if (frj["stop_reason"].toString() != "tool_use") {
                    resp.content = extract_anthropic_content_text(fcontent); // final answer
                    break;
                }

                // More tools requested — record the assistant turn, execute each
                // tool_use block, and feed the results back for the next round.
                loop_msgs.append(QJsonObject{{"role", "assistant"}, {"content", fcontent}});
                // Collect the round, then dispatch it together — read-only calls
                // concurrently, writes as barriers (detail::execute_tool_calls).
                std::vector<detail::PendingToolCall> calls;
                for (const auto& block_val : fcontent) {
                    const QJsonObject block = block_val.toObject();
                    if (block["type"].toString() != "tool_use")
                        continue;
                    detail::PendingToolCall c;
                    c.call_id = block["id"].toString();
                    c.wire_name = block["name"].toString();
                    c.args = block["input"].toObject();
                    c.display = mcp::McpProvider::parse_openai_function_name(c.wire_name).second;
                    LOG_DEBUG(kLlmSvcTag, QString("Anthropic tool loop r%1: %2").arg(round).arg(c.wire_name));
                    detail::emit_tool_progress(c.display, c.args);
                    calls.push_back(std::move(c));
                }
                QElapsedTimer batch_timer;
                batch_timer.start();
                const auto results = detail::execute_tool_calls(calls);
                std::vector<QString> result_names;
                result_names.reserve(calls.size());
                for (const auto& call : calls)
                    result_names.push_back(call.display);
                const auto encoded_results = detail::encode_tool_results_for_llm(result_names, results);
                {
                    const qint64 ms = batch_timer.elapsed();
                    for (std::size_t k = 0; k < calls.size(); ++k)
                        emit_tool_done(calls[k].display, calls[k].args, results[k], ms, calls.size() > 1);
                }
                QJsonArray more_results;
                for (std::size_t k = 0; k < calls.size(); ++k) {
                    // tool_result blocks are appended in the model's order: an
                    // Anthropic content array is positional, so completion order
                    // must not leak into the transcript.
                    more_results.append(
                        QJsonObject{{"type", "tool_result"},
                                    {"tool_use_id", calls[k].call_id},
                                    {"content", encoded_results[k]}});
                }
                loop_msgs.append(QJsonObject{{"role", "user"}, {"content", more_results}});
            }
            if (resp.content.isEmpty() && resp.error.isEmpty()) {
                const QString instruction =
                    QStringLiteral("The tool budget is spent (%1). Give a concise final answer from the completed "
                                   "tool evidence. Do not call tools.")
                        .arg(ant_budget.exhaustion_note());
                const QString prompt = bounded_tool_summary_prompt(
                    loop_msgs, instruction, detail::tool_loop_summary_context_max_bytes());
                QJsonObject summary_body{{QStringLiteral("model"), model_},
                                         {QStringLiteral("messages"),
                                          QJsonArray{QJsonObject{{QStringLiteral("role"), QStringLiteral("user")},
                                                                {QStringLiteral("content"), prompt}}}},
                                         {QStringLiteral("max_tokens"),
                                          std::min(resolved_max_tokens(),
                                                   detail::tool_loop_summary_max_tokens())}};
                auto summary_http = blocking_post(url, summary_body, hdr);
                if (summary_http.success) {
                    const QJsonDocument summary_doc = QJsonDocument::fromJson(summary_http.body);
                    if (!summary_doc.isNull()) {
                        const QJsonObject summary_json = summary_doc.object();
                        resp.content = extract_anthropic_content_text(summary_json["content"].toArray());
                        LlmResponse summary_usage;
                        parse_usage(summary_usage, summary_json, provider_);
                        add_provider_usage(ant_usage, summary_usage);
                    }
                }
                if (resp.content.isEmpty())
                    resp.error = "Anthropic tool loop ended without an answer — " + ant_budget.exhaustion_note();
            }
        } else {
            resp.content = extract_anthropic_content_text(content);
        }

    } else if (provider_ == "gemini" || provider_ == "google") {
        QJsonArray cands = rj["candidates"].toArray();
        if (!cands.isEmpty()) {
            QJsonArray parts = cands[0].toObject()["content"].toObject()["parts"].toArray();

            bool has_function_calls = false;
            for (const auto& part_val : parts) {
                if (part_val.toObject().contains("functionCall")) {
                    has_function_calls = true;
                    break;
                }
            }

            if (has_function_calls) {
                LOG_DEBUG(kLlmSvcTag, "Gemini requested function call(s)");
                // ── Multi-round tool loop ──
                // Gemini's multi-turn contract: user → model(functionCall parts)
                // → user(functionResponse parts) → … Previously this ran a SINGLE
                // follow-up with NO tools attached, so the model could execute one
                // tool and never chain a second. We now loop, re-attaching the tool
                // set each round, until a text answer or the round cap.
                const int kMaxRounds = active_max_tool_rounds();
                detail::ToolLoopBudget gem_budget(kMaxRounds);
                LlmResponse gem_usage;
                parse_usage(gem_usage, rj, provider_);
                ProviderUsageScope preserve_usage(resp, gem_usage);
                gem_budget.note_prompt_tokens(gem_usage.prompt_tokens);
                gem_budget.note_completion_tokens(gem_usage.completion_tokens);

                // Constant conversation prefix: prior history + the user turn.
                QJsonArray fu_contents;
                for (const auto& m : history) {
                    if (m.role == "system")
                        continue;
                    const QString role = (m.role == "assistant") ? "model" : "user";
                    fu_contents.append(
                        QJsonObject{{"role", role}, {"parts", QJsonArray{QJsonObject{{"text", m.content}}}}});
                }
                fu_contents.append(
                    QJsonObject{{"role", "user"}, {"parts", QJsonArray{QJsonObject{{"text", user_message}}}}});

                // model_parts = the model turn whose functionCalls we execute this
                // round; seeded with the initial response's parts.
                QJsonArray model_parts = parts;
                QJsonArray last_response_parts;
                for (int round = 0; !gem_budget.exhausted(); ++round) {
                    if (detail::cancel_requested()) {
                        resp.cancelled = true;
                        resp.error = "Request cancelled";
                        return resp;
                    }
                    gem_budget.note_round();
                    fu_contents.append(QJsonObject{{"role", "model"}, {"parts", model_parts}});

                    std::vector<detail::PendingToolCall> calls;
                    for (const auto& part_val : model_parts) {
                        const QJsonObject part = part_val.toObject();
                        if (!part.contains("functionCall"))
                            continue;
                        const QJsonObject fc = part["functionCall"].toObject();
                        detail::PendingToolCall c;
                        c.wire_name = fc["name"].toString();
                        c.call_id = c.wire_name; // Gemini correlates by name, not by id
                        // Gemini's declaration of this tool is a lossy translation
                        // of its real schema: a free-form object parameter goes out
                        // as a JSON-carrying STRING because Gemini's Schema cannot
                        // express "object with arbitrary keys" (mcp/GeminiSchema.h).
                        // Reconcile against the original schema so the handler gets
                        // the object it declared, not a string it will read as {}.
                        c.args = mcp::restore_gemini_call_args(
                            mcp::McpService::instance().input_schema_for_function(c.wire_name),
                            fc["args"].toObject());
                        c.display = mcp::McpProvider::parse_openai_function_name(c.wire_name).second;
                        LOG_DEBUG(kLlmSvcTag, QString("Gemini tool loop r%1: %2").arg(round).arg(c.wire_name));
                        detail::emit_tool_progress(c.display, c.args);
                        calls.push_back(std::move(c));
                    }
                    QElapsedTimer batch_timer;
                    batch_timer.start();
                    const auto results = detail::execute_tool_calls(calls);
                    {
                        const qint64 ms = batch_timer.elapsed();
                        for (std::size_t k = 0; k < calls.size(); ++k)
                            emit_tool_done(calls[k].display, calls[k].args, results[k], ms, calls.size() > 1);
                    }
                    std::vector<QString> result_names;
                    std::vector<QJsonObject> response_objects;
                    result_names.reserve(calls.size());
                    response_objects.reserve(calls.size());
                    for (std::size_t k = 0; k < calls.size(); ++k) {
                        const auto& tr = results[k];
                        QJsonObject response_obj;
                        if (!tr.data.isNull() && !tr.data.isUndefined() && tr.data.isObject())
                            response_obj = tr.data.toObject();
                        else if (!tr.message.isEmpty())
                            response_obj["result"] = tr.message;
                        else
                            response_obj = tr.to_json();
                        result_names.push_back(calls[k].display);
                        response_objects.push_back(std::move(response_obj));
                    }
                    const auto shaped_results =
                        detail::fit_llm_payloads_for_round(result_names, std::move(response_objects));
                    QJsonArray fn_response_parts;
                    for (std::size_t k = 0; k < calls.size(); ++k) {
                        const auto& c = calls[k];
                        // Parts are positional and must mirror the model's
                        // functionCall order, not completion order.
                        fn_response_parts.append(QJsonObject{
                            {"functionResponse",
                             QJsonObject{{"name", c.wire_name}, {"response", shaped_results[k]}}}});
                    }
                    fu_contents.append(QJsonObject{{"role", "user"}, {"parts", fn_response_parts}});
                    last_response_parts = fn_response_parts;

                    QJsonObject fu_body;
                    fu_body["contents"] = fu_contents;
                    QJsonObject gen_cfg;
                    gen_cfg["maxOutputTokens"] =
                        gem_budget.clamp_completion_allowance(resolved_max_tokens());
                    fu_body["generationConfig"] = gen_cfg;
                    if (!system_prompt_.isEmpty())
                        fu_body["systemInstruction"] =
                            QJsonObject{{"parts", QJsonArray{QJsonObject{{"text", system_prompt_}}}}};
                    const QJsonArray gem_tools = build_gemini_tools();
                    if (!gem_tools.isEmpty())
                        fu_body["tools"] = gem_tools;

                    auto fu = blocking_post(url, fu_body, hdr);
                    if (!fu.success) {
                        resp.error = "Gemini tool follow-up failed: " + fu.error;
                        return resp;
                    }
                    auto fu_doc = QJsonDocument::fromJson(fu.body);
                    if (fu_doc.isNull()) {
                        resp.error = "Gemini tool follow-up parse error";
                        return resp;
                    }
                    const QJsonObject fu_json = fu_doc.object();
                    LlmResponse round_usage;
                    parse_usage(round_usage, fu_json, provider_);
                    add_provider_usage(gem_usage, round_usage);
                    gem_budget.note_prompt_tokens(round_usage.prompt_tokens);
                    gem_budget.note_completion_tokens(round_usage.completion_tokens);
                    const QJsonArray fu_cands = fu_json["candidates"].toArray();
                    const QJsonArray resp_parts = fu_cands.isEmpty()
                                                      ? QJsonArray{}
                                                      : fu_cands[0].toObject()["content"].toObject()["parts"].toArray();

                    bool more_calls = false;
                    for (const auto& pv : resp_parts) {
                        if (pv.toObject().contains("functionCall")) {
                            more_calls = true;
                            break;
                        }
                    }
                    if (!more_calls) {
                        resp.content = extract_gemini_parts_text(resp_parts); // final answer
                        break;
                    }
                    model_parts = resp_parts; // next round executes these
                }

                if (resp.content.isEmpty()) {
                    const QString instruction =
                        QStringLiteral("The tool budget is spent (%1). Give a concise final answer from the "
                                       "completed tool evidence. Do not call tools.")
                            .arg(gem_budget.exhaustion_note());
                    const QString prompt = bounded_tool_summary_prompt(
                        fu_contents, instruction, detail::tool_loop_summary_context_max_bytes());
                    QJsonObject summary_body{
                        {QStringLiteral("contents"),
                         QJsonArray{QJsonObject{
                             {QStringLiteral("role"), QStringLiteral("user")},
                             {QStringLiteral("parts"),
                              QJsonArray{QJsonObject{{QStringLiteral("text"), prompt}}}}}}},
                        {QStringLiteral("generationConfig"),
                         QJsonObject{{QStringLiteral("maxOutputTokens"),
                                      std::min(resolved_max_tokens(),
                                               detail::tool_loop_summary_max_tokens())}}}};
                    auto summary_http = blocking_post(url, summary_body, hdr);
                    if (summary_http.success) {
                        const QJsonDocument summary_doc = QJsonDocument::fromJson(summary_http.body);
                        if (!summary_doc.isNull()) {
                            const QJsonObject summary_json = summary_doc.object();
                            const QJsonArray summary_candidates = summary_json["candidates"].toArray();
                            if (!summary_candidates.isEmpty()) {
                                resp.content = extract_gemini_parts_text(
                                    summary_candidates[0]
                                        .toObject()["content"]
                                        .toObject()["parts"]
                                        .toArray());
                            }
                            LlmResponse summary_usage;
                            parse_usage(summary_usage, summary_json, provider_);
                            add_provider_usage(gem_usage, summary_usage);
                        }
                    }
                }

                if (resp.content.isEmpty()) {
                    // Loop exhausted without a text turn — render the last round's
                    // function responses as readable text so the chat isn't blank.
                    // This is raw tool output, NOT an answer: label it as such, say
                    // why the loop stopped, and mark any payload that gets clipped.
                    // A silently-truncated JSON blob presented as a reply is the
                    // worst of both — neither the user nor a model reading the
                    // transcript later can tell it is partial (§M4).
                    constexpr int kFallbackPayloadChars = 4000;
                    QString fallback = "*(the tool loop ended before the model produced an answer — " +
                                       gem_budget.exhaustion_note() + ". Raw output from the last round below.)*\n";
                    for (const auto& pv : last_response_parts) {
                        const QJsonObject fr = pv.toObject()["functionResponse"].toObject();
                        const QString fn_name = fr["name"].toString();
                        const int sep = fn_name.indexOf("__");
                        const QString short_name = (sep >= 0) ? fn_name.mid(sep + 2) : fn_name;
                        QString payload = QString::fromUtf8(
                            QJsonDocument(fr["response"].toObject()).toJson(QJsonDocument::Compact));
                        if (payload.size() > kFallbackPayloadChars) {
                            payload = payload.left(kFallbackPayloadChars) +
                                      QStringLiteral("\n[... truncated %1 of %2 characters ...]")
                                          .arg(payload.size() - kFallbackPayloadChars)
                                          .arg(payload.size());
                        }
                        fallback += "\n**Tool: " + short_name + "**\n" + payload + "\n";
                    }
                    resp.content = fallback;
                }
            } else {
                resp.content = extract_gemini_parts_text(parts);
            }
        }

    } else if (provider_ == "fincept") {
        // {"success":..., "data":{"choices":[{"message":{"content":...}}]}} or top-level shape.
        QJsonObject data = rj.contains("data") ? rj["data"].toObject() : rj;
        QJsonArray choices = data["choices"].toArray();
        if (!choices.isEmpty())
            resp.content = extract_openai_message_text(choices[0].toObject()["message"].toObject());
        // success=true with empty content is a soft error.
        if (resp.content.isEmpty()) {
            resp.error = "Fincept LLM returned an empty response. Please try again.";
            LOG_WARN(kLlmSvcTag, "Fincept /research/chat returned empty choices or content");
            return resp;
        }

    } else {
        // OpenAI-compatible.
        QJsonArray choices = rj["choices"].toArray();
        if (!choices.isEmpty()) {
            QJsonObject msg = choices[0].toObject()["message"].toObject();
            QJsonArray tcs = msg["tool_calls"].toArray();

            if (!tcs.isEmpty()) {
                LOG_DEBUG(kLlmSvcTag, QString("LLM requested %1 tool calls").arg(tcs.size()));

                QJsonArray loop_msgs;
                if (!system_prompt_.isEmpty())
                    loop_msgs.append(QJsonObject{{"role", "system"}, {"content", system_prompt_}});
                for (const auto& h : history)
                    loop_msgs.append(QJsonObject{{"role", h.role}, {"content", h.content}});
                loop_msgs.append(QJsonObject{{"role", "user"}, {"content", user_message}});
                loop_msgs.append(msg); // assistant turn with tool_calls

                for (const auto& tc_val : tcs) {
                    if (detail::cancel_requested()) {
                        resp.cancelled = true;
                        resp.error = "Request cancelled";
                        return resp;
                    }
                    QJsonObject tc = tc_val.toObject();
                    QString call_id = tc["id"].toString();
                    QString fn_name = tc["function"].toObject()["name"].toString();
                    QJsonObject fn_args =
                        QJsonDocument::fromJson(tc["function"].toObject()["arguments"].toString("{}").toUtf8())
                            .object();

                    LOG_DEBUG(kLlmSvcTag,
                              logf("LLM tool call started",
                                   {{"tool", fn_name},
                                    {"round", 0},
                                    {"args_bytes", QJsonDocument(fn_args).toJson(QJsonDocument::Compact).size()}}));
                    const QString bare = mcp::McpProvider::parse_openai_function_name(fn_name).second;
                    // The Anthropic and Gemini first rounds and every later OpenAI round announce
                    // their tool calls on the progress channel; this one did not, so the chat's
                    // Tools card only started at round 2 and the opening step was invisible.
                    detail::emit_tool_progress(bare, fn_args);
                    QElapsedTimer tool_timer;
                    tool_timer.start();
                    auto tr = mcp::McpService::instance().execute_openai_function(fn_name, fn_args);
                    emit_tool_done(bare, fn_args, tr, tool_timer.elapsed()); // logs "LLM tool call finished"
                    loop_msgs.append(QJsonObject{{"role", "tool"},
                                                 {"tool_call_id", call_id},
                                                 {"content", detail::encode_tool_result_for_llm(bare, tr)}});
                }

                LlmResponse r1;
                parse_usage(r1, rj, provider_);
                resp = do_tool_loop(loop_msgs, url, hdr, r1.prompt_tokens, r1.completion_tokens);
                // do_tool_loop sums the follow-up rounds; add round 1 instead of
                // overwriting the total with it.
                add_provider_usage(resp, r1);
                return resp;

            } else {
                // Helper handles reasoning models, content-as-parts arrays, and refusal messages.
                resp.content = strip_think_blocks(extract_openai_message_text(msg));
            }
        }
    }

    // ── Detect text-based tool calls ────────────────────────────────────
    // Some models (e.g. minimax, certain OpenRouter models) don't use the
    // structured tool_calls field. Instead they emit tool invocations as
    // XML or custom markup in the text response. Detect these patterns and
    // execute the tools, then ask the LLM to summarize the results.
    if (!resp.content.isEmpty()) {
        LOG_DEBUG(kLlmSvcTag, logf("Checking response for text-based tool calls", {{"chars", resp.content.size()}}));
        LlmResponse accumulated_usage = resp;
        if (accumulated_usage.prompt_tokens == 0 && accumulated_usage.completion_tokens == 0 &&
            accumulated_usage.total_tokens == 0)
            parse_usage(accumulated_usage, rj, provider_);
        auto text_tool_result = try_extract_and_execute_text_tool_calls(resp.content, user_message, url, hdr);
        if (text_tool_result.has_value()) {
            resp = text_tool_result.value();
            if (resp.success || resp.cancelled) {
                add_provider_usage(resp, accumulated_usage);
                return resp;
            }
        }
    }

    if (resp.prompt_tokens == 0 && resp.completion_tokens == 0 && resp.total_tokens == 0)
        parse_usage(resp, rj, provider_);
    resp.success = true;
    return resp;
}

LlmResponse LlmService::do_streaming_request(const QString& user_message,
                                             const std::vector<ConversationMessage>& history, StreamCallback on_chunk) {
    if (!llm_svc_provider_allowed(provider_)) {
        LlmResponse resp;
        resp.error = QStringLiteral("This AI backend is disabled. Configure a user-owned provider such as DeepSeek.");
        on_chunk("", true);
        return resp;
    }
    if (!config_error_.isEmpty()) {
        LlmResponse resp;
        resp.error = config_error_;
        on_chunk("", true);
        return resp;
    }
    if (ProviderCatalog::requires_api_key(provider_) && api_key_.trimmed().isEmpty()) {
        LlmResponse resp;
        resp.error = QStringLiteral(
            "No LLM provider configured: add your own API key in Settings › LLM Configuration.");
        on_chunk("", true);
        return resp;
    }

    // An agent harness streams through its own process (text, thinking, tool cards).
    if (ProviderCatalog::is_harness(provider_)) {
        LlmResponse resp = do_harness_request(user_message, history, on_chunk);
        on_chunk("", true);
        return resp;
    }
    // Gemini uses :streamGenerateContent, Fincept uses async submit/poll — neither
    // fits this OpenAI-compat SSE path. Fall back to do_request and emit as one chunk.
    if (provider_ == "gemini" || provider_ == "google" || provider_ == "fincept") {
        detail::ProgressEmitterGuard pg([on_chunk](const QString& s) { on_chunk(s, false); });
        auto resp = do_request(user_message, history);
        if (resp.success && !resp.content.isEmpty())
            on_chunk(resp.content, false);
        on_chunk("", true);
        return resp;
    }

    // The SSE parser can identify a structured tool call but cannot continue
    // it with the provider's full message object. Its old fallback aborted that
    // already-billed generation and resent the whole turn through do_request().
    // Start tool-enabled turns in the bounded tool loop instead; tool-free chat
    // keeps token-by-token streaming.
    if (!should_stream_initial_request(detail::effective_tools_enabled(tools_enabled_))) {
        detail::ProgressEmitterGuard pg([on_chunk](const QString& s) { on_chunk(s, false); });
        auto resp = do_request(user_message, history);
        if (resp.success && !resp.content.isEmpty())
            on_chunk(resp.content, false);
        on_chunk("", true);
        return resp;
    }

    LlmResponse resp;
    if (ProviderCatalog::is_blocked(provider_, base_url_)) {
        resp.error = "AtlasCloud has been banned by Fincept and cannot be used.";
        on_chunk("", true);
        return resp;
    }

    QString url = get_endpoint_url();
    if (url.isEmpty()) {
        resp.error = "No endpoint URL for provider: " + provider_;
        on_chunk("", true);
        return resp;
    }
    if (!is_allowed_llm_endpoint(url)) {
        resp.error = QStringLiteral(
            "Refusing an insecure LLM endpoint. Use https://, or http:// only for a loopback address.");
        on_chunk("", true);
        return resp;
    }

    auto hdr = get_headers();
    // Anthropic streaming handles tools out-of-band so it goes through a different request shape.
    QJsonObject req_body = (provider_ == "anthropic") ? build_anthropic_request(user_message, history, true)
                                                      : build_openai_request(user_message, history, true, true);

    // The thread's long-lived QNAM (keep-alive TLS reuse across turns and tool
    // rounds); the reply is deleteLater'd and DeferredDelete is drained before
    // this function returns — a stack QNAM here used to leak socket-notifier
    // teardown work onto the main runloop and crash hours later inside
    // qt_mac_socket_callback. See eventloop_request() / thread_nam().
    QNetworkAccessManager* nam = thread_nam();
    QNetworkRequest req{QUrl(url)};
    prepare_thread_nam_request(req);
    req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    req.setRawHeader("Accept", "text/event-stream");
    req.setRawHeader("Cache-Control", "no-cache");
    // Per Qt 6 network docs: HTTP/2 is enabled by default and batches frames,
    // which causes SSE chunks to arrive in bursts instead of as the server
    // flushes them. Force HTTP/1.1 so readyRead fires per TCP packet.
    req.setAttribute(QNetworkRequest::Http2AllowedAttribute, false);
    for (auto it = hdr.constBegin(); it != hdr.constEnd(); ++it)
        req.setRawHeader(it.key().toUtf8(), it.value().toUtf8());

    QByteArray json_data = QJsonDocument(req_body).toJson(QJsonDocument::Compact);
    QNetworkReply* reply = nam->post(req, json_data);

    QByteArray partial_line;
    QByteArray non_sse_body; // lines outside the SSE framing — the JSON error body on a non-2xx reply
    QString accumulated;
    QJsonObject final_usage_obj;
    bool done = false;
    bool tool_call_detected = false;
    bool saw_finish = false;      // the model reported a terminal finish_reason / message_stop
    bool timed_out = false;       // idle watchdog fired
    QString stream_error;         // {"error": …} event delivered inside a 200 stream
    int anthropic_input_tokens = 0; // Anthropic reports these in message_start, not in the final usage event

    // <think>…</think> reasoning filter. Reasoning models (MiniMax M2.7,
    // DeepSeek-R1 derivatives, …) stream their chain-of-thought inline in
    // `delta.content` wrapped in <think> tags. The user shouldn't see that —
    // only the answer. State persists across SSE chunks because tags can
    // straddle the chunk boundary ("<thi" + "nk>...").
    // Returns the visible (answer) text; any reasoning found inside <think>…</think>
    // is appended to *think_out so the caller can route it to the Thinking channel
    // rather than dropping it.
    bool in_think = false;
    QString think_pending;
    auto filter_think = [&](const QString& chunk, QString* think_out) -> QString {
        QString out;
        QString work = think_pending + chunk;
        think_pending.clear();
        qsizetype pos = 0;
        const qsizetype n = work.length();
        while (pos < n) {
            if (in_think) {
                qsizetype end = work.indexOf(QStringLiteral("</think>"), pos, Qt::CaseInsensitive);
                if (end < 0) {
                    // Hold last 8 chars in case </think> straddles.
                    qsizetype safe = std::max<qsizetype>(pos, n - 8);
                    if (think_out)
                        *think_out += work.mid(pos, safe - pos);
                    think_pending = work.mid(safe);
                    return out;
                }
                if (think_out)
                    *think_out += work.mid(pos, end - pos);
                in_think = false;
                pos = end + 8;
            } else {
                qsizetype start = work.indexOf(QStringLiteral("<think>"), pos, Qt::CaseInsensitive);
                if (start < 0) {
                    // Emit everything except the last 7 chars (length of "<think>")
                    // which might be a partial open tag.
                    qsizetype safe = std::max<qsizetype>(pos, n - 7);
                    out += work.mid(pos, safe - pos);
                    think_pending = work.mid(safe);
                    return out;
                }
                out += work.mid(pos, start - pos);
                in_think = true;
                pos = start + 7;
            }
        }
        return out;
    };

    // IDLE watchdog, not a wall-clock cap: it is re-armed on every byte received
    // (readyRead below). The old fixed 120 s from request start cut off any
    // reasoning model that legitimately streams for longer — and because the
    // reply was still healthy at that point, the truncated text was returned as a
    // *successful* answer.
    constexpr int kStreamIdleTimeoutMs = 120000;
    QEventLoop loop;
    QTimer timeout;
    timeout.setSingleShot(true);
    timeout.start(kStreamIdleTimeoutMs);
    // Polls the cancel flag (cancel_active_request) so Stop lands within ~100 ms.
    QTimer cancel_poll;
    cancel_poll.setInterval(100);

    QObject::connect(&timeout, &QTimer::timeout, &loop, [&]() {
        timed_out = true;
        loop.quit();
    });
    QObject::connect(&cancel_poll, &QTimer::timeout, &loop, [&]() {
        if (detail::cancel_requested())
            loop.quit();
    });
    cancel_poll.start();
    QObject::connect(reply, &QNetworkReply::finished, &loop, [&]() {
        done = true;
        loop.quit();
    });
    QObject::connect(reply, &QNetworkReply::readyRead, &loop, [&]() {
        timeout.start(kStreamIdleTimeoutMs);
        partial_line += reply->readAll();
        while (true) {
            int nl = partial_line.indexOf('\n');
            if (nl < 0)
                break;
            QByteArray raw_line = partial_line.left(nl);
            partial_line.remove(0, nl + 1);

            if (!raw_line.isEmpty() && raw_line.back() == '\r')
                raw_line.chop(1);

            QString line = QString::fromUtf8(raw_line).trimmed();
            // "data:" with or without the optional space — both are valid SSE.
            if (line.isEmpty() || !line.startsWith(QLatin1String("data:"))) {
                // Keep anything that isn't SSE framing: on a 4xx/5xx the body is a plain
                // (often pretty-printed, multi-line) JSON document, and only its last line
                // used to survive for parse_server_error_message().
                if (!line.isEmpty() && !line.startsWith(QLatin1String("event:")) &&
                    !line.startsWith(QLatin1Char(':')) && !line.startsWith(QLatin1String("id:")) &&
                    !line.startsWith(QLatin1String("retry:")) && non_sse_body.size() < 16384) {
                    non_sse_body += raw_line;
                    non_sse_body += '\n';
                }
                continue;
            }

            QString data = line.mid(5).trimmed(); // strip "data:"
            if (data == "[DONE]") {
                if (!tool_call_detected)
                    on_chunk("", true);
                done = true;
                loop.quit();
                return;
            }

            // OpenAI sends a trailing usage chunk (empty choices[]) when stream_options.include_usage=true.
            {
                auto usage_doc = QJsonDocument::fromJson(data.toUtf8());
                if (!usage_doc.isNull() && usage_doc.isObject()) {
                    QJsonObject uobj = usage_doc.object();
                    if (uobj.contains("usage") && uobj["usage"].isObject())
                        final_usage_obj = uobj["usage"].toObject();
                    // A provider can fail AFTER the 200 has gone out (OpenRouter and
                    // Anthropic both do: {"error":{...}} / {"type":"error",...} as a
                    // data event). Nothing read it, so the turn "succeeded" with
                    // whatever had streamed — usually an empty reply.
                    // (Non-null only: some gateways put "error": null on every normal chunk.)
                    const QJsonValue err_val = uobj.value(QLatin1String("error"));
                    if (stream_error.isEmpty() &&
                        (err_val.isObject() || (err_val.isString() && !err_val.toString().trimmed().isEmpty()))) {
                        stream_error = parse_server_error_message(data.toUtf8());
                        if (stream_error.isEmpty())
                            stream_error = QStringLiteral("The provider reported an error mid-stream");
                    }
                    // Terminal markers — lets an idle timeout after a complete answer
                    // still count as success (some gateways never close the socket).
                    if (uobj["type"].toString() == QLatin1String("message_stop"))
                        saw_finish = true;
                    if (uobj["type"].toString() == QLatin1String("message_start"))
                        anthropic_input_tokens =
                            uobj["message"].toObject()["usage"].toObject()["input_tokens"].toInt();
                    const QJsonArray fin_choices = uobj["choices"].toArray();
                    if (!fin_choices.isEmpty()) {
                        const QString fr = fin_choices[0].toObject()["finish_reason"].toString();
                        if (!fr.isEmpty() && fr != QLatin1String("tool_calls"))
                            saw_finish = true;
                    }
                }
            }

            if (!tool_call_detected) {
                auto doc = QJsonDocument::fromJson(data.toUtf8());
                if (!doc.isNull() && doc.isObject()) {
                    QJsonObject obj = doc.object();

                    if (provider_ == "anthropic") {
                        const QString type = obj["type"].toString();
                        if (type == "content_block_start" &&
                            obj["content_block"].toObject()["type"].toString() == "tool_use") {
                            LOG_DEBUG(kLlmSvcTag, "STREAM: Anthropic tool_use content_block_start detected");
                            tool_call_detected = true;
                            loop.quit();
                            return;
                        }
                        if (type == "message_delta" &&
                            obj["delta"].toObject()["stop_reason"].toString() == "tool_use") {
                            LOG_DEBUG(kLlmSvcTag, "STREAM: Anthropic stop_reason=tool_use detected");
                            tool_call_detected = true;
                            loop.quit();
                            return;
                        }
                    }

                    QJsonArray choices = obj["choices"].toArray();
                    if (!choices.isEmpty()) {
                        const QString finish = choices[0].toObject()["finish_reason"].toString();
                        if (finish == "tool_calls") {
                            LOG_DEBUG(
                                kLlmSvcTag,
                                QString("STREAM: OpenAI-compat finish_reason=tool_calls detected (%1)").arg(provider_));
                            tool_call_detected = true;
                            loop.quit();
                            return;
                        }
                        QJsonObject delta = choices[0].toObject()["delta"].toObject();
                        if (!delta["tool_calls"].isUndefined() && !delta["tool_calls"].isNull()) {
                            LOG_DEBUG(kLlmSvcTag,
                                      QString("STREAM: OpenAI-compat delta.tool_calls detected (%1)").arg(provider_));
                            tool_call_detected = true;
                            loop.quit();
                            return;
                        }
                    }

                    // Fincept can also surface tool_calls at top level.
                    if (!obj["tool_calls"].isUndefined() && !obj["tool_calls"].isNull() &&
                        obj["tool_calls"].toArray().size() > 0) {
                        LOG_DEBUG(kLlmSvcTag, "STREAM: top-level tool_calls detected (fincept)");
                        tool_call_detected = true;
                        loop.quit();
                        return;
                    }
                }
            }

            const SseDelta delta = parse_sse_chunk(data, provider_);
            if (delta.is_reasoning) {
                // Chain-of-thought (reasoning_content / Anthropic thinking_delta).
                // Route to the UI's separate Thinking channel via the sentinel
                // prefix; never accumulate it into the answer or stored content.
                if (!delta.text.isEmpty())
                    on_chunk(think_stream_prefix() + delta.text, false);
            } else if (!delta.text.isEmpty()) {
                accumulated += delta.text;

                // Some providers stream tool calls as XML/text markup — detect, suppress output, fall back to
                // do_request. Patterns covered: <tool_call>, </tool_call>, <minimax:tool_call>, <invoke name=,
                // ```tool_call code fences, and bare `minimax:tool_call ... /minimax:tool_call`.
                if (!tool_call_detected) {
                    static const QRegularExpression rx_text_tool(
                        QStringLiteral("<\\s*/?\\s*(?:\\w+\\s*:\\s*)?tool_call\\b"
                                       "|<\\s*invoke\\s+name="
                                       "|```\\s*tool_call\\b"
                                       "|\\b\\w+\\s*:\\s*tool_call\\b"),
                        QRegularExpression::CaseInsensitiveOption);
                    if (rx_text_tool.match(accumulated).hasMatch()) {
                        tool_call_detected = true;
                        LOG_DEBUG(kLlmSvcTag,
                                  "Tool call markup detected in streamed text — falling back to non-streaming");
                        loop.quit();
                        return;
                    }
                }

                // Inline <think>…</think> reasoning (MiniMax M2.7, DeepSeek-R1
                // derivatives) gets split out here: think_seg → Thinking channel,
                // visible → answer bubble.
                QString think_seg;
                const QString visible = filter_think(delta.text, &think_seg);
                if (!think_seg.isEmpty())
                    on_chunk(think_stream_prefix() + think_seg, false);
                if (!visible.isEmpty())
                    on_chunk(visible, false);
            }
        }
    });

    loop.exec();
    timeout.stop();
    cancel_poll.stop();

    if (!in_think && !think_pending.isEmpty())
        on_chunk(think_pending, false);
    think_pending.clear();

    auto drain_nam = [&]() {
        reply->deleteLater();
        // Pump DeferredDelete on this thread so the reply's teardown completes
        // while a dispatcher still exists — prevents the qt_mac_socket_callback
        // use-after-free on the main thread later.
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
    };

    // The user pressed Stop. Hand back what streamed so far (flagged cancelled)
    // rather than a success or a generic error, and skip the tool-loop fallback
    // below — a cancelled turn must not go on to execute tools.
    // (A Stop that lands just after the stream finished on its own — terminal marker
    // seen, socket closed — is not a cancellation: the answer is complete.)
    const bool stream_completed = done && saw_finish && !tool_call_detected;
    if (detail::cancel_requested() && !stream_completed) {
        // DEBUG: the request's outcome line (LLM request finished result=cancelled) follows.
        LOG_DEBUG(kLlmSvcTag, QString("Stream cancelled by user after %1 chars").arg(accumulated.size()));
        reply->abort();
        drain_nam();
        resp.content = strip_think_blocks(accumulated);
        resp.cancelled = true;
        resp.error = QStringLiteral("Request cancelled");
        on_chunk("", true);
        return resp;
    }

    if (tool_call_detected) {
        LOG_DEBUG(kLlmSvcTag, "Tool call detected in stream — falling back to tool loop");
        reply->abort();
        drain_nam();

        // Sentinel tells the chat screen to reset the bubble before the real reply arrives.
        on_chunk("\x01__TOOL_CALL_CLEAR__", false);

        // Wire per-tool progress into the chat bubble so the user sees something
        // during the multi-second tool execution phase instead of a blank wait.
        detail::ProgressEmitterGuard pg([on_chunk](const QString& s) { on_chunk(s, false); });
        auto tool_resp = do_request(user_message, history);
        if (tool_resp.success && !tool_resp.content.isEmpty())
            on_chunk(QStringLiteral("\n") + tool_resp.content, false);
        on_chunk("", true);
        return tool_resp;
    }

    int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
    // Only "the provider did not run this" failures that produced no output are
    // safe for the caller to resubmit (chat_streaming does so once).
    auto transient_status = [](int s) { return s == 429 || s == 500 || s == 502 || s == 503 || s == 504 || s == 529; };
    if (timed_out && !done && status == 0) {
        // The watchdog fired before any response headers arrived (hung connect / TLS).
        resp.error = QStringLiteral("No response from the provider (timed out after %1 s)").arg(kStreamIdleTimeoutMs / 1000);
        resp.retryable = true;
        LOG_WARN(kLlmSvcTag, privacy::redact_text(resp.error));
    } else if (reply->error() != QNetworkReply::NoError) {
        // A non-2xx (e.g. 400 from AIHubMix routing to a model that rejects a
        // param) carries a JSON error body that explains why. The SSE reader
        // never parses it because it isn't a "data:" line, so pull it from the
        // lines kept aside + the unconsumed buffer + any remainder and surface
        // the real reason instead of Qt's opaque "server replied: Bad Request".
        // (OpenAI pretty-prints its error JSON across many lines; only the last
        // one used to survive, so its message was never found.)
        const QByteArray err_body = non_sse_body + partial_line + reply->readAll();
        const QString server_msg = parse_server_error_message(err_body);
        if (!server_msg.isEmpty())
            resp.error = status > 0 ? QString("HTTP %1: %2").arg(status).arg(server_msg) : server_msg;
        else
            resp.error = reply->errorString();
        resp.content = strip_think_blocks(accumulated);
        resp.retryable = accumulated.isEmpty() &&
                         (transient_status(status) || reply->error() == QNetworkReply::RemoteHostClosedError);
        resp.safe_automatic_retry = accumulated.isEmpty() && status == 429;
        LOG_ERROR(kLlmSvcTag, "Stream request failed: " + privacy::redact_text(resp.error));
    } else if (status >= 200 && status < 300) {
        if (!stream_error.isEmpty()) {
            // The 200 was followed by an error event: report it, keep any partial text.
            resp.error = stream_error;
            resp.content = strip_think_blocks(accumulated);
            resp.retryable = accumulated.isEmpty();
            LOG_ERROR(kLlmSvcTag, "Stream failed mid-flight: " + privacy::redact_text(resp.error));
        } else if (timed_out && !done && !saw_finish) {
            // No bytes for the whole idle window and no terminal marker: the stream
            // died. Say so instead of presenting the partial text as a finished reply.
            resp.error = QStringLiteral("The response stalled — no data received for %1 s").arg(kStreamIdleTimeoutMs / 1000);
            resp.content = strip_think_blocks(accumulated);
            resp.retryable = accumulated.isEmpty();
            LOG_WARN(kLlmSvcTag, privacy::redact_text(resp.error));
        } else {
            // `accumulated` holds only answer deltas (reasoning_content was routed to
            // the Thinking channel and never added here). Strip any inline <think>…
            // </think> blocks so the persisted/replayed content is the answer alone.
            resp.content = strip_think_blocks(accumulated);
            resp.success = true;
            // The closing Anthropic usage event carries output_tokens only; the prompt side
            // arrived in message_start. Without it the token counter showed output alone.
            if (provider_ == "anthropic" && anthropic_input_tokens > 0 && !final_usage_obj.contains("input_tokens"))
                final_usage_obj["input_tokens"] = anthropic_input_tokens;
            if (!final_usage_obj.isEmpty()) {
                QJsonObject wrap{{"usage", final_usage_obj}};
                parse_usage(resp, wrap, provider_);
            }
        }
    } else {
        const QString server_msg = parse_server_error_message(non_sse_body + partial_line + reply->readAll());
        resp.error = server_msg.isEmpty() ? QString("HTTP %1").arg(status) : QString("HTTP %1: %2").arg(status).arg(server_msg);
    }

    // On the timeout path we fall straight out of `loop.exec()` with the reply
    // still streaming, and drain_nam() makes the delete synchronous — tearing
    // the reply down from under Qt's HTTP machinery. Abort first, as the
    // sibling helper does (LlmHttpHelpers.cpp:120). No-op when already finished.
    if (!reply->isFinished())
        reply->abort();
    drain_nam();

    if (!done)
        on_chunk("", true);

    return resp;
}
// ============================================================================
// Public API
// ============================================================================

LlmResponse LlmService::chat(const QString& user_message, const std::vector<ConversationMessage>& history,
                             bool use_tools, const ChatRoute& route) {
    // The feature's route (ModelRouter). Each attempt runs on a private
    // instance holding its own config snapshot (see "Request concurrency" in
    // the header), so it runs concurrently with every other request and a
    // reload_config() mid-request can't hand it another provider's key or URL.
    const QString feature = route.feature.isEmpty() ? QString::fromLatin1(kFeatureAiChat) : route.feature;
    const QVector<ModelChoice> chain = route_chain(route);
    QElapsedTimer request_clock;
    request_clock.start();
    if (const QString error = llm_svc_input_budget_error(user_message.size()); !error.isEmpty()) {
        LOG_WARN(kLlmSvcTag, error);
        return LlmResponse{.content = {}, .error = error};
    }

    // No session id → not cancellable via cancel_active_request(), so no
    // CancelScope is armed here (arming one would let an AI Chat Stop abort this
    // request too). A chat() nested inside an armed request on the same thread
    // still observes that request's cancellation.
    LlmSvcChatSlot slot;
    if (!slot.acquire())
        return LlmResponse{.content = {}, .error = QStringLiteral("Request cancelled"), .cancelled = true};

    // thread_local guard avoids racing with concurrent chat_streaming calls from the floating bubble.
    detail::ToolPolicyGuard guard(use_tools ? ToolPolicy::All : ToolPolicy::None);
    const std::vector<ConversationMessage> trimmed = llm_svc_trim_history(history, user_message.size());
    QString notice;
    int idx = 0;
    LlmResponse resp;
    ConfigSnapshot cfg;
    detail::ReasoningEffortGuard effort_guard(route.effort);
    for (;;) {
        cfg = snapshot_for(chain[idx], feature);
        LlmService request;
        request.adopt_config_snapshot(cfg);
        resp = request.do_request(user_message, trimmed);
        if (!resp.success && chain[idx].is_harness() && notice.isEmpty() && !detail::cancel_requested()) {
            const FallbackDecision d = ModelRouter::decide_fallback(
                chain, idx, resp.harness_failure, resp.harness_output_started, resp.cancelled,
                resp.harness_subscription, ModelRouter::instance().unavailable_behaviour());
            if (d.fall_back) {
                LOG_WARN(kLlmSvcTag, QStringLiteral("%1 unavailable for %2 — next model of the route answers")
                                         .arg(chain[idx].harness_id, feature));
                notice = d.notice;
                idx = d.next_index;
                continue; // once: `notice` is set now
            }
            resp.unavailable_notice = d.notice;
            if (d.ask && d.next_index >= 0) {
                resp.fallback_offer = chain[d.next_index].label;
                resp.unavailable_harness = chain[idx].harness_id;
            }
        }
        break;
    }
    if (!resp.success && detail::cancel_requested()) {
        resp.cancelled = true;
        resp.error = QStringLiteral("Request cancelled");
    }
    stamp_route(resp, chain[idx], cfg, notice);
    llm_svc_log_request_finished(resp, feature, false, request_clock.elapsed());
    return resp;
}

void detail::notify_ui_operating() {
    const QString session = t_chat_session_id;
    if (session.isEmpty())
        return;
    LlmService* svc = &LlmService::instance();
    QMetaObject::invokeMethod(svc, [svc, session]() { emit svc->ui_operating(session); }, Qt::QueuedConnection);
}

void LlmService::note_activity(int delta, const QString& what) {
    QPointer<LlmService> self = this;
    QMetaObject::invokeMethod(
        this,
        [self, delta, what]() {
            if (!self)
                return;
            self->activity_running_ = std::max(0, self->activity_running_ + delta);
            self->activity_text_ = self->activity_running_ > 0 ? what : QString();
            emit self->activity_changed(self->activity_running_, self->activity_text_);
        },
        Qt::QueuedConnection);
}

void LlmService::cancel_active_request(const QString& chat_session_id) {
    QMutexLocker lock(&mutex_);
    if (session_lane_busy_ && (chat_session_id.isEmpty() || chat_session_id == active_chat_session_)) {
        LOG_INFO(kLlmSvcTag, "Cancelling the active LLM request");
        // Bumped with mutex_ held: the lane cannot change hands in between, so
        // the bump reaches the request that holds it and no later one (a new
        // holder captures its CancelScope baseline under mutex_, after this).
        detail::g_llm_cancel_generation.fetch_add(1, std::memory_order_acq_rel);
        return;
    }
    // Still queued behind another session's request: it finishes cancelled
    // without being sent once it is woken.
    if (!chat_session_id.isEmpty() && session_lane_waiters_.value(chat_session_id) > 0) {
        LOG_INFO(kLlmSvcTag, "Cancelling a queued LLM request before it is sent");
        session_lane_cancelled_.insert(chat_session_id);
        session_lane_cv_.wakeAll();
    }
}

void LlmService::chat_streaming(const QString& user_message, const std::vector<ConversationMessage>& history,
                                StreamCallback on_chunk, bool use_tools) {
    chat_streaming(user_message, history, std::move(on_chunk), use_tools ? ToolPolicy::All : ToolPolicy::None);
}

void LlmService::chat_streaming(const QString& user_message, const std::vector<ConversationMessage>& history,
                                StreamCallback on_chunk, ToolPolicy policy, const QString& chat_session_id) {
    chat_streaming(user_message, history, std::move(on_chunk), policy, chat_session_id, ChatRoute{});
}

void LlmService::chat_streaming(const QString& user_message, const std::vector<ConversationMessage>& history,
                                StreamCallback on_chunk, ToolPolicy policy, const QString& chat_session_id,
                                const ChatRoute& route) {
    // The feature's route as of this call (ModelRouter: explicit choice →
    // connected harness → API provider → Fincept LLM). Each attempt runs on a
    // private instance with its own config snapshot (see "Request concurrency").
    const QString feature = route.feature.isEmpty() ? QString::fromLatin1(kFeatureAiChat) : route.feature;
    const QVector<ModelChoice> chain = route_chain(route);
    const UnavailableBehaviour behaviour = ModelRouter::instance().unavailable_behaviour();

    std::vector<ConversationMessage> history_copy = history;

    QPointer<LlmService> self = this;
    // Shadow-publish every chunk to DataHub under a per-stream session id so observer panels
    // can tail `llm:session:*:stream` without being wired in. Push-only, coalesced.
    const QString stream_id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    const QString stream_topic = QStringLiteral("llm:session:") + stream_id + QStringLiteral(":stream");
    [[maybe_unused]] static bool policy_once = []() {
        auto& hub = fincept::datahub::DataHub::instance();
        fincept::datahub::TopicPolicy policy;
        policy.push_only = true;
        policy.coalesce_within_ms = 50;
        policy.ttl_ms = 5 * 60 * 1000;
        hub.set_policy_pattern(QStringLiteral("llm:session:*"), policy);
        LOG_DEBUG("LlmService", "Registered DataHub policy for llm:session:*");
        return true;
    }();
    // Skip on_chunk if LlmService dies before the chunk arrives.
    if (!chat_session_id.isEmpty())
        emit request_started(chat_session_id, user_message);
    StreamCallback guarded_chunk = [self, on_chunk, stream_id, stream_topic, chat_session_id](const QString& chunk,
                                                                                               bool done) {
        if (!self)
            return;
        if (!chat_session_id.isEmpty() && !chunk.isEmpty())
            emit self->request_chunk(chat_session_id, chunk);
        // A tool call starting ("Opening equity_research AAPL"): the header names the step.
        // Completion records on the same channel carry a 0x1F field separator and are skipped.
        if (chunk.startsWith(tool_stream_prefix()) && !chunk.contains(QChar(0x1F))) {
            const QString step = chunk.mid(tool_stream_prefix().size()).trimmed();
            if (!step.isEmpty())
                self->note_activity(0, step);
        }
        on_chunk(chunk, done);
        QJsonObject payload{
            {"session_id", stream_id},
            {"chunk", chunk},
            {"done", done},
        };
        auto& hub = fincept::datahub::DataHub::instance();
        hub.publish(stream_topic, QVariant(payload));
        if (done) {
            // Subscribers stay attached but cached state is discarded.
            hub.retire_topic(stream_topic);
        }
    };
    (void)QtConcurrent::run(
        &llm_svc_request_pool(), [self, chain, feature, behaviour, user_message, history_copy, guarded_chunk, policy,
                                  chat_session_id, effort = route.effort]() {
            if (!self)
                return;
            detail::ReasoningEffortGuard effort_guard(effort);
            // In flight from here until this worker returns, whichever way it leaves.
            struct ActivityMark {
                QPointer<LlmService> svc;
                ~ActivityMark() {
                    if (svc)
                        svc->note_activity(-1, {});
                }
            } activity_mark{self};
            self->note_activity(+1, {});
            QElapsedTimer request_clock;
            request_clock.start();
            const auto finish = [self, &chat_session_id](LlmResponse resp) {
                resp.origin_session_id = chat_session_id;
                if (!self)
                    return;
                QMetaObject::invokeMethod(
                    self,
                    [self, resp]() {
                        if (self)
                            emit self->finished_streaming(resp);
                    },
                    Qt::QueuedConnection);
            };
            if (const QString error = llm_svc_input_budget_error(user_message.size()); !error.isEmpty()) {
                LOG_WARN(kLlmSvcTag, error);
                guarded_chunk(QString(), true);
                finish(LlmResponse{.content = {}, .error = error});
                return;
            }

            // Session lane — only requests carrying a chat_session_id (the AI Chat
            // tab) are cancellable, and they run one at a time so the process-wide
            // cancel generation can only ever reach the caller's own request. The
            // CancelScope baseline is captured under mutex_, in the same critical
            // section that hands over the lane, so a Stop aimed at the previous
            // holder can never leak into this request.
            const bool session_lane = !chat_session_id.isEmpty();
            std::optional<detail::CancelScope> cancel_scope;
            if (session_lane) {
                QMutexLocker lock(&self->mutex_);
                ++self->session_lane_waiters_[chat_session_id];
                while (self->session_lane_busy_ && !self->session_lane_cancelled_.contains(chat_session_id))
                    self->session_lane_cv_.wait(&self->mutex_);
                if (--self->session_lane_waiters_[chat_session_id] <= 0)
                    self->session_lane_waiters_.remove(chat_session_id);
                if (self->session_lane_cancelled_.remove(chat_session_id)) {
                    lock.unlock();
                    LOG_INFO(kLlmSvcTag, "Queued LLM request cancelled before it was sent");
                    guarded_chunk("", true);
                    LlmResponse cancelled;
                    cancelled.cancelled = true;
                    cancelled.error = QStringLiteral("Request cancelled");
                    finish(cancelled);
                    return;
                }
                self->session_lane_busy_ = true;
                self->active_chat_session_ = chat_session_id;
                cancel_scope.emplace();
            }
            [[maybe_unused]] const auto lane_done = qScopeGuard([self, session_lane]() {
                if (!session_lane || !self)
                    return;
                QMutexLocker lock(&self->mutex_);
                self->session_lane_busy_ = false;
                self->active_chat_session_.clear();
                self->session_lane_cv_.wakeAll();
            });

            detail::ToolPolicyGuard guard(policy);
            detail::ChatSessionGuard session_guard(chat_session_id);

            // Retry ONCE only when the provider explicitly rejected the request
            // before inference (HTTP 429). A timeout, disconnect or 5xx can happen
            // after a paid generation started, so resubmitting it automatically can
            // double-charge the user's BYO key. Anything that already produced output
            // — including tool progress — is never replayed either.
            // The same rule decides a harness fallback (ModelRouter::decide_fallback).
            // An attempt's own "done" is held back: a fallback attempt may follow, and
            // the consumer gets exactly one at the end.
            auto emitted_output = std::make_shared<bool>(false);
            const StreamCallback tracked_chunk = [guarded_chunk, emitted_output](const QString& chunk, bool done) {
                if (done)
                    return;
                if (!chunk.isEmpty())
                    *emitted_output = true;
                guarded_chunk(chunk, false);
            };
            const std::vector<ConversationMessage> trimmed_history =
                llm_svc_trim_history(history_copy, user_message.size());
            QString notice;
            int idx = 0;
            LlmResponse resp;
            ConfigSnapshot cfg;
            for (;;) {
                cfg = self->snapshot_for(chain[idx], feature);
                LlmService request;
                request.adopt_config_snapshot(cfg);
                resp = request.do_streaming_request(user_message, trimmed_history, tracked_chunk);
                // (Fincept already resubmits once inside fincept_async_request; a harness falls back instead.)
                if (!resp.success && resp.safe_automatic_retry && !*emitted_output &&
                    cfg.provider != QLatin1String("fincept") &&
                    !chain[idx].is_harness() && !detail::cancel_requested()) {
                    LOG_WARN(kLlmSvcTag, logf("LLM stream failed before any output, retrying once",
                                              {{"provider", cfg.provider}, {"model", cfg.model}, {"error", resp.error}}));
                    if (detail::cancellable_sleep(2000))
                        resp = request.do_streaming_request(user_message, trimmed_history, tracked_chunk);
                }
                if (!resp.success && chain[idx].is_harness() && notice.isEmpty() && !detail::cancel_requested()) {
                    const FallbackDecision d = ModelRouter::decide_fallback(
                        chain, idx, resp.harness_failure, resp.harness_output_started || *emitted_output,
                        resp.cancelled, resp.harness_subscription, behaviour);
                    if (d.fall_back) {
                        LOG_WARN(kLlmSvcTag, QStringLiteral("%1 unavailable for %2 before any output — the next model "
                                                            "of the route answers")
                                                 .arg(chain[idx].harness_id, feature));
                        notice = d.notice;
                        QMetaObject::invokeMethod(
                            self,
                            [self, chat_session_id, feature, notice]() {
                                if (self)
                                    emit self->route_notice(chat_session_id, feature, notice);
                            },
                            Qt::QueuedConnection);
                        idx = d.next_index;
                        continue; // once: `notice` is set now
                    }
                    resp.unavailable_notice = d.notice;
                    if (d.ask && d.next_index >= 0) {
                        resp.fallback_offer = chain[d.next_index].label;
                        resp.unavailable_harness = chain[idx].harness_id;
                    }
                }
                break;
            }
            guarded_chunk(QString(), true);
            if (!resp.success && detail::cancel_requested()) {
                resp.cancelled = true;
                resp.error = QStringLiteral("Request cancelled");
            }
            stamp_route(resp, chain[idx], cfg, notice);
            llm_svc_log_request_finished(resp, feature, true, request_clock.elapsed());
            finish(resp);
        });
}

} // namespace fincept::ai_chat
