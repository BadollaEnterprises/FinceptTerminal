#include "services/alpha_arena/ArenaLlmClient.h"

#include "auth/AuthManager.h"
#include "core/config/AppConfig.h"
#include "core/metrics/LocalUsageMetrics.h"
#include "core/policy/CommunityPolicy.h"
#include "services/alpha_arena/HarnessArenaClient.h"
#include "services/llm/LlmBudgetSettings.h"
#include "services/llm/DeepSeekRequestPolicy.h"
#include "services/llm/LlmEndpointPolicy.h"
#include "services/llm/LlmErrors.h"
#include "services/llm/ModelCatalog.h"
#include "services/llm/ProviderCatalog.h"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QTimer>

namespace fincept::arena {
using fincept::ai_chat::ModelCatalog;
using fincept::ai_chat::ProviderCatalog;
using fincept::ai_chat::resolve_llm_output_tokens;

ArenaLlmClient::ArenaLlmClient(QObject* parent) : IArenaLlmClient(parent) {
    nam_ = new QNetworkAccessManager(this);
}

QByteArray ArenaLlmClient::build_body(const ArenaLlmRequest& req) {
    const QString p = req.provider.toLower();
    const int catalog_cap = ModelCatalog::output_cap(p, req.model_id);
    const int max_tokens = resolve_llm_output_tokens(req.max_tokens, catalog_cap, 2048);

    QJsonObject body;
    if (p == "anthropic") {
        body["model"] = req.model_id;
        body["max_tokens"] = max_tokens;
        body["system"] = req.system_prompt;
        body["messages"] = QJsonArray{QJsonObject{{"role", "user"}, {"content", req.user_prompt}}};
    } else if (p == "gemini" || p == "google") {
        body["systemInstruction"] = QJsonObject{{"parts", QJsonArray{QJsonObject{{"text", req.system_prompt}}}}};
        body["contents"] =
            QJsonArray{QJsonObject{{"role", "user"}, {"parts", QJsonArray{QJsonObject{{"text", req.user_prompt}}}}}};
        body["generationConfig"] = QJsonObject{{"maxOutputTokens", max_tokens}};
    } else { // OpenAI-compatible family + fincept (/research/chat takes OpenAI messages)
        body["messages"] = QJsonArray{QJsonObject{{"role", "system"}, {"content", req.system_prompt}},
                                      QJsonObject{{"role", "user"}, {"content", req.user_prompt}}};
        if (p != "fincept" || (req.model_id != "fincept-llm" && !req.model_id.isEmpty()))
            body["model"] = req.model_id;
        if (p != "fincept") {
            if (p == "openai" || p == "xai")
                body["max_completion_tokens"] = max_tokens;
            else
                body["max_tokens"] = max_tokens;
        }
        // Arena decisions run on a schedule and require short, deterministic
        // JSON rather than a long chain of thought. DeepSeek currently enables
        // high-effort thinking by default when both controls are omitted, which
        // silently multiplies BYO output usage on every round. Keep the arena's
        // default on the explicit low-cost path; interactive AI Chat still lets
        // the user choose higher effort.
        if (p == QLatin1String("deepseek"))
            fincept::ai_chat::apply_deepseek_thinking(body, QStringLiteral("low"));
    }
    return QJsonDocument(body).toJson(QJsonDocument::Compact);
}

ArenaLlmResult ArenaLlmClient::parse_response(const QString& provider, const QByteArray& body) {
    ArenaLlmResult r;
    const QString p = provider.toLower();
    const QJsonObject root = QJsonDocument::fromJson(body).object();
    if (root.isEmpty()) {
        r.error = "non-JSON response";
        return r;
    }
    // Fincept's /research/chat wraps the OpenAI payload in its API envelope:
    //   {"success":true,"message":"Success","data":{"choices":[…],"usage":{…}}}
    // and reports failures as {"success":false,"message":<string|object>}. Only
    // the top level used to be read, so every Fincept agent decision came back as
    // "empty completion" and the agent tripped its circuit breaker.
    if (p == QLatin1String("fincept") && root.value("success").isBool() && !root.value("success").toBool()) {
        const QJsonValue m = root.value("message");
        r.error = m.isObject() ? m.toObject().value("message").toString() : m.toString();
        if (r.error.isEmpty())
            r.error = "Fincept request failed";
        return r;
    }
    const QJsonObject o =
        (p == QLatin1String("fincept") && root.value("data").isObject()) ? root.value("data").toObject() : root;
    if (o.contains("error")) {
        const auto e = o.value("error");
        r.error = e.isObject() ? e.toObject().value("message").toString() : e.toString();
        if (r.error.isEmpty())
            r.error = "provider error";
        return r;
    }
    if (p == "anthropic") {
        for (const auto& blk : o.value("content").toArray())
            if (blk.toObject().value("type").toString() == "text")
                r.content += blk.toObject().value("text").toString();
        const auto u = o.value("usage").toObject();
        r.prompt_tokens = u.value("input_tokens").toInt();
        r.completion_tokens = u.value("output_tokens").toInt();
        r.cached_prompt_tokens = u.value("cache_read_input_tokens").toInt();
    } else if (p == "gemini" || p == "google") {
        const auto cands = o.value("candidates").toArray();
        if (!cands.isEmpty())
            for (const auto& part : cands[0].toObject().value("content").toObject().value("parts").toArray())
                r.content += part.toObject().value("text").toString();
        const auto u = o.value("usageMetadata").toObject();
        r.prompt_tokens = u.value("promptTokenCount").toInt();
        r.completion_tokens = u.value("candidatesTokenCount").toInt();
        r.cached_prompt_tokens = u.value("cachedContentTokenCount").toInt();
    } else {
        const auto choices = o.value("choices").toArray();
        if (!choices.isEmpty())
            r.content = choices[0].toObject().value("message").toObject().value("content").toString();
        if (r.content.isEmpty()) // fincept /research/chat tolerant fallbacks
            r.content = o.value("content").toString();
        if (r.content.isEmpty())
            r.content = o.value("response").toString();
        const auto u = o.value("usage").toObject();
        r.prompt_tokens = u.value("prompt_tokens").toInt();
        r.completion_tokens = u.value("completion_tokens").toInt();
        r.cached_prompt_tokens = u.value("prompt_cache_hit_tokens").toInt();
        if (r.cached_prompt_tokens <= 0)
            r.cached_prompt_tokens = u.value("prompt_tokens_details").toObject().value("cached_tokens").toInt();
    }
    r.cached_prompt_tokens = qBound(0, r.cached_prompt_tokens, r.prompt_tokens);
    if (r.content.isEmpty()) {
        r.error = "empty completion";
        return r;
    }
    r.success = true;
    return r;
}

bool ArenaLlmClient::endpoint_allowed(const QString& provider, const QString& base_url, const QString& endpoint) {
    return !ProviderCatalog::is_blocked(provider, base_url) && !ProviderCatalog::is_blocked(provider, endpoint) &&
           fincept::ai_chat::is_allowed_llm_endpoint(endpoint);
}

void ArenaLlmClient::apply_transport_policy(QNetworkRequest& request) {
    // Arena requests carry a user-owned provider key. Never let Qt forward it
    // to another origin through a redirect, and keep ambient web cookies out of
    // this independent API client in both directions.
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::SameOriginRedirectPolicy);
    request.setAttribute(QNetworkRequest::CookieLoadControlAttribute, QNetworkRequest::Manual);
    request.setAttribute(QNetworkRequest::CookieSaveControlAttribute, QNetworkRequest::Manual);
}

bool ArenaLlmClient::request_fits_input_budget(const ArenaLlmRequest& request, qsizetype budget_chars) {
    if (budget_chars <= 0)
        return true;
    const qsizetype system_chars = request.system_prompt.size();
    return system_chars <= budget_chars && request.user_prompt.size() <= budget_chars - system_chars;
}

bool ArenaLlmClient::request_fits_turn_input_budget(const ArenaLlmRequest& request, int budget_tokens) {
    return fincept::ai_chat::llm_request_fits_prompt_budget(build_body(request).size(), budget_tokens);
}

void ArenaLlmClient::complete(const ArenaLlmRequest& req, std::function<void(ArenaLlmResult)> cb) {
    const QString p = req.provider.trimmed().toLower();
    fincept::ai_chat::LlmBudgetSettings budget_settings;
    budget_settings.history_chars = fincept::AppConfig::instance()
                                        .get(fincept::ai_chat::kLlmHistoryCharsKey,
                                             QVariant(fincept::ai_chat::kDefaultLlmHistoryChars))
                                        .toInt();
    budget_settings.prompt_tokens = fincept::AppConfig::instance()
                                        .get(fincept::ai_chat::kLlmPromptTokensKey,
                                             QVariant(fincept::ai_chat::kDefaultLlmPromptTokens))
                                        .toInt();
    const auto validated_budgets = fincept::ai_chat::validate_llm_budget_settings(budget_settings);
    const qsizetype input_budget = validated_budgets.history_chars;
    if (!request_fits_input_budget(req, input_budget)) {
        ArenaLlmResult r;
        r.error = QStringLiteral("arena prompt is %1 characters, above the configured %2-character BYO AI "
                                 "request guardrail; no provider request was sent")
                      .arg(req.system_prompt.size() + req.user_prompt.size())
                      .arg(input_budget);
        cb(r);
        return;
    }
    const QByteArray request_body = build_body(req);
    if (!request_fits_turn_input_budget(req, validated_budgets.prompt_tokens)) {
        ArenaLlmResult r;
        r.error = QStringLiteral("arena request is conservatively estimated at %1 input tokens, above the configured "
                                 "%2-token Turn Input limit; no provider request was sent")
                      .arg(fincept::ai_chat::budgeted_llm_tokens(0, request_body.size()))
                      .arg(validated_budgets.prompt_tokens);
        cb(r);
        return;
    }
    if (fincept::policy::is_deprecated_llm_provider(p)) {
        ArenaLlmResult r;
        r.error = "this retired AI backend is disabled; configure DeepSeek in LLM settings";
        cb(r);
        return;
    }
    // An agent-harness competitor ("harness:<id>"): a harness process, not HTTP.
    if (ProviderCatalog::is_harness(p)) {
        auto* harness = findChild<HarnessArenaClient*>(QString(), Qt::FindDirectChildrenOnly);
        if (!harness)
            harness = new HarnessArenaClient(this);
        harness->complete(req, std::move(cb));
        return;
    }
    if (p == "fincept" && !fincept::policy::fincept_metered_services_enabled()) {
        ArenaLlmResult r;
        r.error = "blocked by the community credit firewall; select a user-funded API provider";
        cb(r);
        return;
    }
    QString url = ProviderCatalog::chat_endpoint(p, req.base_url, req.model_id);
    if (p == "fincept")
        url = fincept::AppConfig::instance().api_base_url() + "/research/chat";
    if (url.isEmpty()) {
        ArenaLlmResult r;
        r.error = "no endpoint for provider " + p;
        cb(r);
        return;
    }
    if (!endpoint_allowed(p, req.base_url, url)) {
        ArenaLlmResult r;
        r.error = "refusing an insecure or blocked LLM endpoint";
        cb(r);
        return;
    }

    QNetworkRequest nr{QUrl(url)};
    apply_transport_policy(nr);
    nr.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    if (p == "anthropic") {
        nr.setRawHeader("x-api-key", req.api_key.toUtf8());
        nr.setRawHeader("anthropic-version", "2023-06-01");
    } else if (p == "gemini" || p == "google") {
        nr.setRawHeader("x-goog-api-key", req.api_key.toUtf8());
    } else if (p == "fincept") {
        if (!req.api_key.isEmpty())
            nr.setRawHeader("X-API-Key", req.api_key.toUtf8());
        const auto& sess = fincept::auth::AuthManager::instance().session();
        if (!sess.session_token.isEmpty())
            nr.setRawHeader("X-Session-Token", sess.session_token.toUtf8());
        nr.setRawHeader("User-Agent", "FinceptTerminal/4.0");
    } else if (!req.api_key.isEmpty()) {
        nr.setRawHeader("Authorization", ("Bearer " + req.api_key).toUtf8());
    }

    const qint64 t0 = QDateTime::currentMSecsSinceEpoch();
    QNetworkReply* reply = nam_->post(nr, request_body);
    auto* timeout = new QTimer(reply);
    timeout->setSingleShot(true);
    QObject::connect(timeout, &QTimer::timeout, reply, &QNetworkReply::abort);
    timeout->start(req.timeout_ms);

    const QString provider = p;
    QObject::connect(reply, &QNetworkReply::finished, this, [reply, provider, t0, cb]() {
        reply->deleteLater();
        ArenaLlmResult r;
        r.latency_ms = QDateTime::currentMSecsSinceEpoch() - t0;
        const QByteArray body = reply->readAll();
        if (reply->error() == QNetworkReply::OperationCanceledError) {
            r.error = "timeout";
            fincept::metrics::LocalUsageMetrics::record_llm(provider, 0, 0, false);
            cb(r);
            return;
        }
        const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        if (provider == QLatin1String("fincept")) {
            // Refused by the Fincept API (only — a 401 from another provider is
            // about the user's own key there). This QNAM bypasses HttpClient, so
            // report it: a replaced session token is dropped / a revoked key
            // signs out (401), an IP block pauses polling (403). The decision
            // row records the server's reason.
            if (status == 401 || status == 403) {
                r.error = fincept::ai_chat::report_fincept_refusal(
                    status, body, QString::fromUtf8(reply->request().rawHeader("X-Session-Token")),
                    reply->rawHeader("X-Session-Expired").trimmed().compare("true", Qt::CaseInsensitive) == 0);
                fincept::metrics::LocalUsageMetrics::record_llm(provider, 0, 0, false);
                cb(r);
                return;
            }
            // Out of credits: the same readable sentence the AI Chat shows,
            // recorded on the decision row instead of a JSON blob.
            const auto kind = fincept::ai_chat::classify_fincept_failure(status, QString::fromUtf8(body));
            if (status >= 400 && kind != fincept::ai_chat::LlmErrorKind::Other) {
                r.error = fincept::ai_chat::make_fincept_error(kind, QString::fromUtf8(body));
                fincept::metrics::LocalUsageMetrics::record_llm(provider, 0, 0, false);
                cb(r);
                return;
            }
        }
        if (reply->error() != QNetworkReply::NoError && body.isEmpty()) {
            r.error = reply->errorString();
            fincept::metrics::LocalUsageMetrics::record_llm(provider, 0, 0, false);
            cb(r);
            return;
        }
        // Provider errors often arrive as HTTP 4xx with a JSON body — parse it.
        ArenaLlmResult parsed = parse_response(provider, body);
        parsed.latency_ms = r.latency_ms;
        fincept::metrics::LocalUsageMetrics::record_llm(provider, parsed.prompt_tokens, parsed.completion_tokens,
                                                        parsed.success, parsed.cached_prompt_tokens);
        cb(parsed);
    });
}

} // namespace fincept::arena
