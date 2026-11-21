#include "services/report_builder/ReportAiLlmBackend.h"

#include "core/config/AppPaths.h"
#include "core/policy/CommunityPolicy.h"
#include "services/llm/LlmErrors.h"
#include "services/llm/LlmService.h"
#include "services/llm/ModelRouter.h"
#include "services/report_builder/ReportAiDraftCache.h"

#include <QCoreApplication>
#include <QDateTime>
#include <QMetaObject>
#include <QPointer>

namespace fincept::services {

namespace report_ai_llm_backend_detail {

QString ralb_tr(const char* text) {
    return QCoreApplication::translate("ReportAiLlmBackend", text);
}

/// LlmService's answer when no provider is configured at all.
bool ralb_is_no_provider(const QString& error) {
    return error.startsWith(QLatin1String("No LLM provider configured"));
}

QString ralb_cache_directory() {
    return AppPaths::cache() + QStringLiteral("/report_ai_drafts");
}

} // namespace report_ai_llm_backend_detail

using namespace report_ai_llm_backend_detail;

ReportAiLlmBackend::ReportAiLlmBackend(QObject* parent) : QObject(parent) {
    // finished_streaming() is process-wide (AI Chat, Quick Chat, Quick Ask …): on_finished()
    // keeps only the results of this backend's own session ids.
    connect(&ai_chat::LlmService::instance(), &ai_chat::LlmService::finished_streaming, this,
            [this](const ai_chat::LlmResponse& r) { on_finished(r); });
}

ReportAiLlmBackend::~ReportAiLlmBackend() {
    // A panel closed mid-request: stop what it started (never another surface's request).
    for (auto it = pending_.cbegin(); it != pending_.cend(); ++it)
        if (it->remote_started)
            ai_chat::LlmService::instance().cancel_active_request(it.key());
    pending_.clear();
}

QString ReportAiLlmBackend::session_id_for(quint64 id) {
    return QString::fromLatin1(kReportDraftFeature) + QLatin1Char(':') + QString::number(id);
}

void ReportAiLlmBackend::start(quint64 id, const QString& system, const QString& user, AiCachePolicy cache_policy,
                               std::function<void(quint64, const QString&)> on_chunk,
                               std::function<void(quint64, const AiBackendResult&)> on_done) {
    const QString sid = session_id_for(id);
    const ai_chat::ModelChoice choice =
        ai_chat::ModelRouter::instance().resolve(QString::fromLatin1(kReportDraftFeature));
    const QString cache_key = report_ai_cache::draft_cache_key(system, user, choice.provider_id(), choice.model);
    Pending p;
    p.id = id;
    p.cache_key = cache_key;
    p.on_chunk = std::move(on_chunk);
    p.on_done = std::move(on_done);

    if (cache_policy == AiCachePolicy::PreferCache) {
        const auto cached = report_ai_cache::load_draft_cache(
            ralb_cache_directory(), cache_key, QDateTime::currentMSecsSinceEpoch());
        if (cached) {
            pending_.insert(sid, std::move(p));
            QMetaObject::invokeMethod(
                this,
                [this, sid, cached = *cached]() {
                    const auto it = pending_.find(sid);
                    if (it == pending_.end())
                        return;
                    const Pending hit = pending_.take(sid);
                    if (hit.on_chunk)
                        hit.on_chunk(hit.id, cached.text);
                    AiBackendResult result;
                    result.ok = true;
                    result.text = cached.text;
                    result.route_label = cached.route_label;
                    result.fallback_notice = ralb_tr("Saved draft reused · no API call");
                    if (hit.on_done)
                        hit.on_done(hit.id, result);
                },
                Qt::QueuedConnection);
            return;
        }
    }

    p.remote_started = true;
    pending_.insert(sid, std::move(p));

    // The instruction leads the user turn (see the header: "system" history entries reach
    // OpenAI-compatible providers only).
    const QString message = QStringLiteral("INSTRUCTIONS:\n") + system + QStringLiteral("\n\n") + user;

    // on_chunk runs on an LlmService worker thread: hop to the GUI thread (qApp lives there)
    // and drop the chunk there if this backend is gone or the request was cancelled.
    QPointer<ReportAiLlmBackend> self(this);
    ai_chat::LlmService::instance().chat_streaming(
        message, {},
        [self, sid](const QString& chunk, bool done) {
            if (done || chunk.isEmpty())
                return;
            QMetaObject::invokeMethod(
                QCoreApplication::instance(),
                [self, sid, chunk]() {
                    if (self)
                        self->deliver_chunk(sid, chunk);
                },
                Qt::QueuedConnection);
        },
        ai_chat::LlmService::ToolPolicy::None, sid,
        ai_chat::ChatRoute{QString::fromLatin1(kReportDraftFeature), QString(), QString()});
}

void ReportAiLlmBackend::cancel(quint64 id) {
    const QString sid = session_id_for(id);
    const auto it = pending_.find(sid);
    if (it == pending_.end())
        return;
    const bool remote_started = it->remote_started;
    pending_.erase(it);
    if (remote_started)
        ai_chat::LlmService::instance().cancel_active_request(sid);
}

AiModelInfo ReportAiLlmBackend::model_info() const {
    const ai_chat::ChatRoute route{QString::fromLatin1(kReportDraftFeature), QString(), QString()};
    const ai_chat::ModelChoice c = ai_chat::ModelRouter::instance().resolve(route.feature);
    AiModelInfo info;
    const bool blocked_fincept = c.is_fincept() && !policy::fincept_metered_services_enabled();
    if (blocked_fincept) {
        info.label = ralb_tr("No AI configured");
        info.needs_setup = true;
        info.setup_reason = ralb_tr(
            "Add your DeepSeek or another BYO API key in Settings › LLM Configuration before drafting. Nothing was sent.");
        return info;
    }
    info.label = c.label;
    if (info.label.isEmpty())
        info.label = c.is_fincept() ? QStringLiteral("Fincept LLM") : c.provider_id();
    if (c.is_fincept()) {
        info.cost_note = ralb_tr("Fincept LLM — uses your Fincept credits");
    } else {
        info.cost_note = ai_chat::ModelRouter::billing_note(c);
    }
    if (!ai_chat::LlmService::instance().is_configured(route)) {
        info.needs_setup = true;
        info.setup_reason =
            ralb_tr("Add your own API key in Settings › LLM Configuration before drafting. Nothing was sent.");
    }
    return info;
}

void ReportAiLlmBackend::deliver_chunk(const QString& session_id, const QString& chunk) {
    const auto it = pending_.constFind(session_id);
    if (it == pending_.cend())
        return; // cancelled, or already finished
    // Copies: the callback may cancel or start a request (and so change pending_).
    const auto cb = it->on_chunk;
    const quint64 id = it->id;
    if (cb)
        cb(id, chunk);
}

void ReportAiLlmBackend::on_finished(const ai_chat::LlmResponse& r) {
    if (r.origin_session_id.isEmpty() || !pending_.contains(r.origin_session_id))
        return; // another surface's request, or one this backend cancelled
    const Pending p = pending_.take(r.origin_session_id);
    AiBackendResult res;
    res.ok = r.success;
    res.cancelled = r.cancelled;
    res.text = r.content;
    // "Ask" / "fail" after an unavailable harness: what happened, not the raw error.
    res.error = r.unavailable_notice.isEmpty() ? r.error : r.unavailable_notice;
    if (!res.ok && !res.cancelled && res.error.trimmed().isEmpty())
        res.error = ralb_tr("The model did not answer — nothing was changed.");
    if (ralb_is_no_provider(r.error))
        res.error = ralb_tr("No AI model is set up. Choose one in Settings › LLM Configuration — nothing was changed.");
    res.settings_hint = !res.ok && (ralb_is_no_provider(r.error) ||
                                    ai_chat::classify_llm_error(r.error) != ai_chat::LlmErrorKind::Other);
    res.route_label = r.route_label;
    res.fallback_notice = r.fallback_notice;
    if (res.ok && !res.cancelled && !res.text.trimmed().isEmpty() && r.fallback_notice.isEmpty() &&
        !p.cache_key.isEmpty()) {
        report_ai_cache::store_draft_cache(
            ralb_cache_directory(), p.cache_key,
            report_ai_cache::DraftCacheEntry{res.text, res.route_label, QDateTime::currentMSecsSinceEpoch()});
    }
    if (p.on_done)
        p.on_done(p.id, res);
}

} // namespace fincept::services
