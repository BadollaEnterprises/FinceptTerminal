#include "screens/chat_mode/ChatModeService.h"

#include "screens/chat_mode/PromptOptimizationPolicy.h"

#include "auth/AuthManager.h"
#include "core/config/AppConfig.h"
#include "core/logging/Logger.h"
#include "core/policy/CommunityPolicy.h"
#include "core/privacy/LogRedact.h"
#include "core/telemetry/ClientInfo.h"
#include "network/http/HttpClient.h"
#include "services/llm/LlmService.h"
#include "services/llm/ModelRouter.h"
#include "storage/repositories/ChatRepository.h"

#include <QDateTime>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMetaEnum>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QTimer>
#include <QUrl>
#include <QUrlQuery>
#include <QtConcurrent>

#include <algorithm>
#include <cmath>

namespace fincept::chat_mode {

namespace {

/// 401 from the backend's require_auth because the X-Session-Token we sent is no
/// longer the account's (signed in elsewhere, password reset): the response has
/// `X-Session-Expired: true` and message.error == "session_invalidated"
/// (apiback auth.py require_auth). Either marker is enough.
/// One WARN per `key` (path + status) per minute: a server that keeps failing the
/// same request (every visit, every retry) is reported once, the repeats at Debug.
/// GUI thread only (handle_reply runs there).
bool chat_svc_warn_due(const QString& key) {
    static QHash<QString, qint64> last_warned;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    const auto it = last_warned.constFind(key);
    if (it != last_warned.constEnd() && now - it.value() < 60 * 1000)
        return false;
    if (last_warned.size() > 256)
        last_warned.clear();
    last_warned.insert(key, now);
    return true;
}

bool chat_svc_is_session_expired(const QNetworkReply* reply, const QJsonObject& body) {
    if (reply->rawHeader("X-Session-Expired").trimmed().toLower() == "true")
        return true;
    const QJsonValue m = body.value(QLatin1String("message"));
    return m.isObject() &&
           m.toObject().value(QLatin1String("error")).toString() == QLatin1String("session_invalidated");
}

QString chat_svc_network_error_name(QNetworkReply::NetworkError err) {
    const QMetaObject& mo = QNetworkReply::staticMetaObject;
    const char* key = mo.enumerator(mo.indexOfEnumerator("NetworkError")).valueToKey(err);
    return key ? QString::fromLatin1(key) : QString::number(static_cast<int>(err));
}

/// Seconds from a Retry-After header (delta-seconds form; an HTTP date is ignored),
/// 0 when there is none. Capped so a bad value cannot park a caller for hours.
int chat_svc_retry_after(const QNetworkReply* reply) {
    bool ok = false;
    const int s = reply->rawHeader("Retry-After").trimmed().toInt(&ok);
    return ok && s > 0 ? std::min(s, 3600) : 0;
}

/// The machine-readable reason of a refusal: the X-Error-Code response header
/// (apiback finagent_client.coded_http_error; the body's error_code stays
/// "HTTP_ERROR"). Empty from servers that do not send it.
QString chat_svc_error_code(const QNetworkReply* reply) {
    return QString::fromLatin1(reply->rawHeader("X-Error-Code").trimmed()).toLower();
}

/// The server has no route for this request — an older deployment. Every refusal
/// of the newer routes carries X-Error-Code, so a coded reply never counts. Without
/// a code: FastAPI's own answer for an unknown path is 404 {"detail": "Not Found"}
/// (405 for a known path with another method), while a 404 the API raises itself
/// goes through main.py's handler and carries the {"success": false, …} envelope.
/// (Checked against fastapi 0.123 / starlette 0.50, the versions apiback runs.)
bool chat_svc_route_missing(int status, const QJsonObject& body, const QString& code) {
    if (!code.isEmpty())
        return false;
    return status == 405 || (status == 404 && !body.contains(QLatin1String("success")));
}

QString chat_svc_sentence(QString text) {
    text = text.trimmed();
    if (!text.isEmpty() && !text.endsWith(QLatin1Char('.')) && !text.endsWith(QLatin1Char('!')) &&
        !text.endsWith(QLatin1Char('?')))
        text += QLatin1Char('.');
    return text;
}

/// What the user is shown for a failed Fincept API reply: the server's own
/// explanation (same extraction rules as HttpClient), plus what to do about it
/// where the user can act — instead of "HTTP 402 /chat/…: UnknownContentError".
/// 429 and 502/503/504 are temporary conditions (busy AI slots — 429
/// llm_concurrency_limit / concurrent_limit_exceeded —, the agent service down or
/// slow): they say so and when to try again (`retry_after_s`, 0 = not given).
/// Nothing here retries on its own.
QString chat_svc_reply_error_text(int status, const QJsonObject& body, QNetworkReply::NetworkError net_err,
                                  bool ip_blocked, int retry_after_s = 0) {
    const QString server = HttpClient::error_text_from_body(body);
    const auto when = [retry_after_s, &server]() {
        if (retry_after_s > 0)
            return ChatModeService::tr("This is temporary: try again in %1 s.").arg(retry_after_s);
        // "…temporarily unavailable. Please try again shortly." needs no second advice.
        if (server.contains(QLatin1String("try again"), Qt::CaseInsensitive))
            return QString();
        return ChatModeService::tr("This is temporary: try again in a moment.");
    };
    switch (status) {
        case 0:
            return ChatModeService::tr("Cannot reach the Fincept server (%1).").arg(chat_svc_network_error_name(net_err));
        case 401:
            return server.isEmpty() ? ChatModeService::tr("Not signed in to Fincept (HTTP 401). Sign in, then retry.")
                                    : server;
        case 402: {
            return ChatModeService::tr(
                "The Fincept-hosted route is unavailable under the community credit firewall. "
                "Select your configured DeepSeek or another BYO provider in Settings → LLM Configuration.");
        }
        case 403:
            if (ip_blocked)
                return (server.isEmpty()
                            ? ChatModeService::tr("The Fincept server has temporarily blocked your network address.")
                            : server) +
                       QLatin1Char(' ') +
                       ChatModeService::tr("This is a temporary network block, not a problem with your account: it "
                                           "usually lifts within 30 minutes (up to 24 hours if it repeats).");
            return server.isEmpty() ? ChatModeService::tr("The Fincept server refused this request (HTTP 403).") : server;
        case 429:
            return (chat_svc_sentence(server.isEmpty() ? ChatModeService::tr("Too many requests at once") : server) +
                    QLatin1Char(' ') + when())
                .trimmed();
        case 502:
        case 503:
        case 504:
            return (chat_svc_sentence(server.isEmpty() ? ChatModeService::tr(
                                                             "The Fincept AI service is temporarily unavailable (HTTP %1)")
                                                             .arg(status)
                                                       : server) +
                    QLatin1Char(' ') + when())
                .trimmed();
        default:
            return server.isEmpty()
                       ? ChatModeService::tr("The Fincept server could not complete the request (HTTP %1).").arg(status)
                       : server;
    }
}

ChatSession chat_svc_local_session(const ::fincept::ChatSession& row) {
    ChatSession out;
    out.uuid = row.id;
    out.title = row.title;
    out.message_count = row.message_count;
    out.created_at = row.created_at;
    out.updated_at = row.updated_at;
    out.last_message_at = row.updated_at;
    return out;
}

ChatMessage chat_svc_local_message(const ::fincept::ChatMessage& row) {
    ChatMessage out;
    out.uuid = row.id;
    out.session_uuid = row.session_id;
    out.role = row.role;
    out.content = row.content;
    out.created_at = row.timestamp;
    out.tokens_used = row.tokens_used;
    out.provider = row.provider;
    out.model = row.model;
    return out;
}

} // namespace

// ── Shared reply helpers (also used by agent_tasks::CloudAgentClient) ────────

QString ChatModeService::reply_error_text(int status, const QJsonObject& body, QNetworkReply::NetworkError net_err,
                                          bool ip_blocked, int retry_after_s) {
    return chat_svc_reply_error_text(status, body, net_err, ip_blocked, retry_after_s);
}

int ChatModeService::reply_retry_after(const QNetworkReply* reply) {
    return chat_svc_retry_after(reply);
}

QString ChatModeService::reply_error_code(const QNetworkReply* reply) {
    return chat_svc_error_code(reply);
}

bool ChatModeService::reply_route_missing(int status, const QJsonObject& body, const QString& code) {
    return chat_svc_route_missing(status, body, code);
}

QString ChatModeService::network_error_name(QNetworkReply::NetworkError err) {
    return chat_svc_network_error_name(err);
}

// ── Singleton ─────────────────────────────────────────────────────────────────

ChatModeService& ChatModeService::instance() {
    static ChatModeService s;
    return s;
}

ChatModeService::ChatModeService(QObject* parent) : QObject(parent) {
    nam_ = new QNetworkAccessManager(this);
    sse_nam_ = new QNetworkAccessManager(this);
}

// ── Auth helpers ──────────────────────────────────────────────────────────────

QString ChatModeService::base_url() const {
    return fincept::AppConfig::instance().api_base_url();
}

QString ChatModeService::api_key() const {
    return auth::AuthManager::instance().session().api_key;
}

QString ChatModeService::session_token() const {
    return auth::AuthManager::instance().session().session_token;
}

QNetworkRequest ChatModeService::build_request(const QString& path) const {
    QNetworkRequest req{QUrl(base_url() + path)};
    req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    telemetry::ClientInfo::apply_headers(req);
    const QString key = api_key();
    const QString tok = session_token();
    if (!key.isEmpty())
        req.setRawHeader("X-API-Key", key.toUtf8());
    if (!tok.isEmpty())
        req.setRawHeader("X-Session-Token", tok.toUtf8());
    return req;
}

// ── Auth failures ─────────────────────────────────────────────────────────────

bool ChatModeService::report_auth_failure(QNetworkReply* reply, int status, const QByteArray& raw_body,
                                          bool* ip_blocked) {
    if (ip_blocked)
        *ip_blocked = false;

    if (status == 403) {
        // IP_BLOCKED is a network condition (too many failed credentials from this
        // address), never an account problem: HttpClient keeps that state so pollers
        // (TerminalToolBridge) pause until it lifts.
        const bool blocked = HttpClient::instance().report_forbidden_response(raw_body);
        if (ip_blocked)
            *ip_blocked = blocked;
        LOG_WARN("ChatModeService", QString("403 on %1%2").arg(reply->url().path(),
                                                               blocked ? QStringLiteral(" (IP blocked)") : QString()));
        return false;
    }
    if (status != 401)
        return false;

    // Every 401 counts toward the server's IP ban, so the first one must stop the
    // dead session token from being sent again. AuthManager drops it (if it is still
    // the current one) and re-validates the API key, at most once at a time.
    const QJsonObject body = QJsonDocument::fromJson(raw_body).object();
    const QByteArray sent_token = reply->request().rawHeader("X-Session-Token");
    const QByteArray sent_key = reply->request().rawHeader("X-API-Key");
    const bool expired = chat_svc_is_session_expired(reply, body);
    LOG_WARN("ChatModeService", QString("401 on %1%2").arg(reply->url().path(), expired
                                                                               ? QStringLiteral(" (session token expired)")
                                                                               : QString()));
    auth::AuthManager::instance().handle_unauthorized(QString::fromUtf8(sent_token), expired);

    // Re-sending is safe only when the dead token was the sole reason (the server
    // checks it before the handler runs, so nothing was done or charged), that token
    // is no longer ours (dropped just now or by an earlier reply) and the key is still
    // the one this request carried. A replay never replays again, so this cannot loop.
    const QString key = api_key();
    return expired && !sent_token.isEmpty() && sent_token != session_token().toUtf8() && !key.isEmpty() &&
           sent_key == key.toUtf8();
}

// ── Reply handler ─────────────────────────────────────────────────────────────

ChatModeService::StatusCallback ChatModeService::adapt(RawCallback cb) {
    return [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err, ReplyInfo) {
        cb(ok, std::move(doc), std::move(err));
    };
}

void ChatModeService::handle_reply(QNetworkReply* reply, StatusCallback cb, Resend resend, int timeout_ms) {
    // Watchdog — abort hanging requests so they don't silently block
    auto* timer = new QTimer(reply);
    timer->setSingleShot(true);
    timer->setInterval(timeout_ms);
    connect(timer, &QTimer::timeout, reply, [reply]() {
        LOG_WARN("ChatModeService", QString("Request timed out: %1").arg(reply->url().path()));
        reply->abort();
    });
    timer->start();

    connect(reply, &QNetworkReply::finished, this,
            [this, reply, cb = std::move(cb), resend = std::move(resend), timeout_ms]() mutable {
        // Read all data BEFORE deleteLater
        const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const QByteArray data = reply->readAll();
        const auto net_err = reply->error();
        const QString url_path = reply->url().path();
        ReplyInfo info;
        info.status = status;
        info.retry_after_s = chat_svc_retry_after(reply);
        info.code = chat_svc_error_code(reply);
        reply->deleteLater();

        LOG_DEBUG("ChatModeService", QString("%1  HTTP %2  %3 bytes%4")
                                         .arg(url_path)
                                         .arg(status)
                                         .arg(data.size())
                                         .arg(info.code.isEmpty() ? QString() : QStringLiteral("  ") + info.code));

        // The only thing that aborts one of these replies is the watchdog above.
        // It used to fall through to the JSON parser and surface as the baffling
        // "JSON parse error on /chat/…: unterminated object" for what is a timeout.
        if (net_err == QNetworkReply::OperationCanceledError) {
            const QString err = tr("Request timed out");
            LOG_WARN("ChatModeService",
                     QString("Chat request timed out path=%1 timeout_ms=%2").arg(url_path).arg(timeout_ms));
            cb(false, {}, err, ReplyInfo{});
            return;
        }

        QJsonParseError pe;
        const QJsonDocument doc = QJsonDocument::fromJson(data, &pe);

        if (net_err != QNetworkReply::NoError || status >= 400) {
            bool ip_blocked = false;
            if (report_auth_failure(reply, status, data, &ip_blocked) && resend) {
                if (QNetworkReply* again = resend()) {
                    LOG_INFO("ChatModeService", "Re-sending " + url_path + " without the expired session token");
                    handle_reply(again, std::move(cb), {}, timeout_ms);
                    return;
                }
            }
            const QJsonObject body = doc.isObject() ? doc.object() : QJsonObject{};
            info.route_missing = chat_svc_route_missing(status, body, info.code);
            const QString err = chat_svc_reply_error_text(status, body, net_err, ip_blocked, info.retry_after_s);
            // The one line for a failed chat request (callers log at Debug only).
            const QString line = QString("Chat request failed path=%1 status=%2 error=\"%3\"")
                                     .arg(url_path)
                                     .arg(status)
                                     .arg(privacy::redact_text(err));
            if (chat_svc_warn_due(url_path + QLatin1Char('|') + QString::number(status)))
                LOG_WARN("ChatModeService", line);
            else
                LOG_DEBUG("ChatModeService", line);
            if (status == 402)
                emit insufficient_credits(err);
            cb(false, doc, err, info);
            return;
        }

        if (pe.error != QJsonParseError::NoError) {
            const QString err = QString("JSON parse error on %1: %2").arg(url_path, pe.errorString());
            LOG_WARN("ChatModeService", err);
            cb(false, {}, err, info);
            return;
        }

        // Check API-level success flag if present
        const QJsonObject root = doc.object();
        if (root.contains("success") && !root["success"].toBool()) {
            QString err = HttpClient::error_text_from_body(root);
            if (err.isEmpty())
                err = tr("The server reported a failure.");
            LOG_WARN("ChatModeService", QString("Chat request refused by the server path=%1 error=\"%2\"")
                                            .arg(url_path, privacy::redact_text(err)));
            cb(false, doc, err, info);
            return;
        }

        cb(true, doc, {}, info);
    });
}

// ── HTTP verbs ────────────────────────────────────────────────────────────────
// Each verb hands handle_reply() a Resend that rebuilds the request from scratch,
// so the one permitted replay goes out with the credentials current at that time.

void ChatModeService::get(const QString& path, RawCallback cb) {
    get_status(path, adapt(std::move(cb)));
}

void ChatModeService::get_status(const QString& path, StatusCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        cb(false, {}, tr("Fincept-hosted Chat Mode is disabled; use local runs with your BYO model."), {});
        return;
    }
    Resend send = [this, path]() { return nam_->get(build_request(path)); };
    QNetworkReply* reply = send();
    handle_reply(reply, std::move(cb), std::move(send));
}

void ChatModeService::post(const QString& path, const QJsonObject& body, RawCallback cb) {
    post_slow(path, body, kDefaultTimeoutMs, std::move(cb));
}

void ChatModeService::post_slow(const QString& path, const QJsonObject& body, int timeout_ms, RawCallback cb) {
    post_status(path, body, timeout_ms, adapt(std::move(cb)));
}

void ChatModeService::post_status(const QString& path, const QJsonObject& body, int timeout_ms, StatusCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        cb(false, {}, tr("Fincept-hosted Chat Mode is disabled; use local runs with your BYO model."), {});
        return;
    }
    const QByteArray payload = QJsonDocument(body).toJson(QJsonDocument::Compact);
    Resend send = [this, path, payload]() { return nam_->post(build_request(path), payload); };
    QNetworkReply* reply = send();
    handle_reply(reply, std::move(cb), std::move(send), timeout_ms);
}

void ChatModeService::put(const QString& path, const QJsonObject& body, RawCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        cb(false, {}, tr("Fincept-hosted Chat Mode is disabled; use local runs with your BYO model."));
        return;
    }
    const QByteArray payload = QJsonDocument(body).toJson(QJsonDocument::Compact);
    Resend send = [this, path, payload]() { return nam_->put(build_request(path), payload); };
    QNetworkReply* reply = send();
    handle_reply(reply, adapt(std::move(cb)), std::move(send));
}

void ChatModeService::del(const QString& path, RawCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        cb(false, {}, tr("Fincept-hosted Chat Mode is disabled; use local runs with your BYO model."));
        return;
    }
    Resend send = [this, path]() { return nam_->deleteResource(build_request(path)); };
    QNetworkReply* reply = send();
    handle_reply(reply, adapt(std::move(cb)), std::move(send));
}

// ── Session CRUD ──────────────────────────────────────────────────────────────

void ChatModeService::create_session(const QString& title, SessionCallback cb) {
    LOG_DEBUG("ChatModeService", QString("Creating chat session title_chars=%1").arg(title.size()));
    if (!policy::fincept_chat_api_enabled()) {
        auto result = ::fincept::ChatRepository::instance().create_session(
            title, policy::default_llm_provider(), policy::default_llm_model());
        if (result.is_err()) {
            cb(false, {}, QString::fromStdString(result.error()));
            return;
        }
        cb(true, chat_svc_local_session(result.value()), {});
        return;
    }
    QJsonObject body;
    body["title"] = title;
    post("/chat/sessions", body, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            LOG_WARN("ChatModeService", "create_session failed: " + err);
            cb(false, {}, err);
            return;
        }
        const QJsonObject data = doc.object().value("data").toObject();
        const QJsonObject session = data.value("session").toObject();
        const QString uuid = session.value("session_uuid").toString();
        LOG_INFO("ChatModeService", QString("Session created: %1").arg(uuid));
        cb(true, ChatSession::from_json(session), {});
    });
}

void ChatModeService::list_sessions(int limit, SessionsCallback cb) {
    LOG_DEBUG("ChatModeService", QString("Listing sessions (limit %1)").arg(limit));
    if (!policy::fincept_chat_api_enabled()) {
        auto result = ::fincept::ChatRepository::instance().list_sessions();
        if (result.is_err()) {
            cb(false, {}, QString::fromStdString(result.error()));
            return;
        }
        QVector<ChatSession> sessions;
        const int count = std::min(std::max(1, limit), static_cast<int>(result.value().size()));
        sessions.reserve(count);
        for (int i = 0; i < count; ++i)
            sessions.append(chat_svc_local_session(result.value().at(i)));
        cb(true, sessions, {});
        return;
    }
    get(QString("/chat/sessions?limit=%1").arg(std::max(1, limit)),
        [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            LOG_DEBUG("ChatModeService", "list_sessions failed: " + err);
            cb(false, {}, err);
            return;
        }
        const QJsonArray arr = doc.object().value("data").toObject().value("sessions").toArray();
        QVector<ChatSession> sessions;
        sessions.reserve(arr.size());
        for (const auto& v : arr)
            sessions.append(ChatSession::from_json(v.toObject()));
        LOG_DEBUG("ChatModeService", QString("Listed %1 sessions").arg(sessions.size()));
        cb(true, sessions, {});
    });
}

void ChatModeService::list_sessions_page(int limit, int offset, SessionsPageCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        auto result = ::fincept::ChatRepository::instance().list_sessions();
        if (result.is_err()) {
            cb(false, {}, QString::fromStdString(result.error()));
            return;
        }
        ChatSessionsPage page;
        page.has_more_known = true;
        const int begin = std::clamp(offset, 0, static_cast<int>(result.value().size()));
        const int end = std::min(begin + std::max(1, limit), static_cast<int>(result.value().size()));
        page.sessions.reserve(end - begin);
        for (int i = begin; i < end; ++i)
            page.sessions.append(chat_svc_local_session(result.value().at(i)));
        page.has_more = end < result.value().size();
        cb(true, page, {});
        return;
    }
    QString path = QString("/chat/sessions?limit=%1").arg(std::max(1, limit));
    if (offset > 0)
        path += QString("&offset=%1").arg(offset);
    get(path, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            LOG_DEBUG("ChatModeService", "list_sessions_page failed: " + err);
            cb(false, {}, err);
            return;
        }
        // {"data": {"sessions": [...], "total", "limit", "offset", "has_more"}} — the
        // last two only on servers that page (apiback chat.py get_chat_sessions).
        const QJsonObject data = doc.object().value("data").toObject();
        ChatSessionsPage page;
        const QJsonArray arr = data.value("sessions").toArray();
        page.sessions.reserve(arr.size());
        for (const auto& v : arr)
            page.sessions.append(ChatSession::from_json(v.toObject()));
        page.has_more_known = data.value("has_more").isBool();
        page.has_more = data.value("has_more").toBool();
        cb(true, page, {});
    });
}

void ChatModeService::load_session(const QString& uuid, SessionDetailCallback cb) {
    LOG_DEBUG("ChatModeService", QString("Loading session: %1").arg(uuid));
    if (!policy::fincept_chat_api_enabled()) {
        auto session = ::fincept::ChatRepository::instance().get_session(uuid);
        if (session.is_err()) {
            cb(false, {}, {}, QString::fromStdString(session.error()), 404);
            return;
        }
        auto stored = ::fincept::ChatRepository::instance().get_messages(uuid);
        if (stored.is_err()) {
            cb(false, {}, {}, QString::fromStdString(stored.error()), 0);
            return;
        }
        QVector<ChatMessage> messages;
        messages.reserve(stored.value().size());
        for (const auto& row : stored.value())
            messages.append(chat_svc_local_message(row));
        cb(true, chat_svc_local_session(session.value()), messages, {}, 200);
        return;
    }
    get_status("/chat/sessions/" + QString::fromUtf8(QUrl::toPercentEncoding(uuid)),
               [cb = std::move(cb), uuid](bool ok, QJsonDocument doc, QString err, ReplyInfo info) {
        if (!ok) {
            LOG_WARN("ChatModeService", QString("load_session %1 failed (HTTP %2): %3").arg(uuid).arg(info.status).arg(err));
            cb(false, {}, {}, err, info.status);
            return;
        }
        // {"data": {"session": {...}, "messages": [...]}} (apiback chat.py get_chat_session)
        const QJsonObject data = doc.object().value("data").toObject();
        const QJsonArray arr = data.value("messages").toArray();
        QVector<ChatMessage> messages;
        messages.reserve(arr.size());
        for (const auto& v : arr)
            messages.append(ChatMessage::from_json(v.toObject()));
        cb(true, ChatSession::from_json(data.value("session").toObject()), messages, {}, info.status);
    });
}

void ChatModeService::delete_session(const QString& uuid, VoidCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        auto result = ::fincept::ChatRepository::instance().delete_session(uuid);
        cb(result.is_ok(), result.is_ok() ? QString() : QString::fromStdString(result.error()));
        return;
    }
    del("/chat/sessions/" + uuid, [cb = std::move(cb)](bool ok, QJsonDocument, QString err) { cb(ok, err); });
}

void ChatModeService::rename_session(const QString& uuid, const QString& title, VoidCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        auto result = ::fincept::ChatRepository::instance().update_session_title(uuid, title);
        cb(result.is_ok(), result.is_ok() ? QString() : QString::fromStdString(result.error()));
        return;
    }
    QJsonObject body;
    body["title"] = title;
    put("/chat/sessions/" + uuid + "/title", body,
        [cb = std::move(cb)](bool ok, QJsonDocument, QString err) { cb(ok, err); });
}

void ChatModeService::activate_session(const QString& uuid, VoidCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        Q_UNUSED(uuid)
        cb(true, {});
        return;
    }
    put("/chat/sessions/" + uuid + "/activate", {},
        [cb = std::move(cb)](bool ok, QJsonDocument, QString err) { cb(ok, err); });
}

// ── Session utilities ─────────────────────────────────────────────────────────

void ChatModeService::get_stats(StatsCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        auto result = ::fincept::ChatRepository::instance().list_sessions();
        if (result.is_err()) {
            cb(false, {}, QString::fromStdString(result.error()));
            return;
        }
        ChatStats stats;
        stats.total_sessions = static_cast<int>(result.value().size());
        stats.active_sessions = stats.total_sessions > 0 ? 1 : 0;
        for (const auto& session : result.value())
            stats.total_messages += session.message_count;
        cb(true, stats, {});
        return;
    }
    get("/chat/stats", [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, err);
            return;
        }
        const QJsonObject data = doc.object().value("data").toObject(doc.object());
        cb(true, ChatStats::from_json(data), {});
    });
}

void ChatModeService::search_messages(const QString& query, SearchCallback cb) {
    if (!policy::fincept_chat_api_enabled()) {
        auto sessions = ::fincept::ChatRepository::instance().list_sessions();
        if (sessions.is_err()) {
            cb(false, {}, QString::fromStdString(sessions.error()));
            return;
        }
        QVector<ChatMessage> matches;
        for (const auto& session : sessions.value()) {
            auto messages = ::fincept::ChatRepository::instance().get_messages(session.id);
            if (messages.is_err()) {
                cb(false, {}, QString::fromStdString(messages.error()));
                return;
            }
            for (const auto& row : messages.value()) {
                if (row.content.contains(query, Qt::CaseInsensitive)) {
                    matches.append(chat_svc_local_message(row));
                    if (matches.size() >= 20) {
                        cb(true, matches, {});
                        return;
                    }
                }
            }
        }
        cb(true, matches, {});
        return;
    }
    const QString path =
        QString("/chat/search?query=%1&limit=20").arg(QString::fromUtf8(QUrl::toPercentEncoding(query)));
    get(path, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, err);
            return;
        }
        // Response: { data: { results: [...], total: N, query: "..." } }
        const QJsonArray arr = doc.object().value("data").toObject().value("results").toArray();
        QVector<ChatMessage> results;
        results.reserve(arr.size());
        for (const auto& v : arr) {
            const QJsonObject o = v.toObject();
            ChatMessage m;
            m.uuid = o["message_uuid"].toString();
            m.role = o["role"].toString();
            m.content = o["content"].toString();
            m.created_at = o["created_at"].toString();
            // Which conversation the hit belongs to — the sidebar needs it to mark the
            // matching sessions (accept either spelling the backend uses elsewhere).
            m.session_uuid = o["session_uuid"].toString();
            if (m.session_uuid.isEmpty())
                m.session_uuid = o["session_id"].toString();
            results.append(m);
        }
        cb(true, results, {});
    });
}

void ChatModeService::export_sessions(const QStringList& uuids, std::function<void(bool, QJsonArray, QString)> cb) {
    if (!policy::fincept_chat_api_enabled()) {
        auto sessions = ::fincept::ChatRepository::instance().list_sessions();
        if (sessions.is_err()) {
            cb(false, {}, QString::fromStdString(sessions.error()));
            return;
        }
        const QSet<QString> selected(uuids.cbegin(), uuids.cend());
        QJsonArray payload;
        for (const auto& session : sessions.value()) {
            if (!selected.isEmpty() && !selected.contains(session.id))
                continue;
            auto messages = ::fincept::ChatRepository::instance().get_messages(session.id);
            if (messages.is_err()) {
                cb(false, {}, QString::fromStdString(messages.error()));
                return;
            }
            QJsonObject item{{"session_uuid", session.id},
                             {"title", session.title},
                             {"created_at", session.created_at},
                             {"updated_at", session.updated_at}};
            QJsonArray rows;
            for (const auto& message : messages.value()) {
                rows.append(QJsonObject{{"message_uuid", message.id},
                                        {"role", message.role},
                                        {"content", message.content},
                                        {"provider", message.provider},
                                        {"model", message.model},
                                        {"tokens_used", message.tokens_used},
                                        {"created_at", message.timestamp}});
            }
            item["messages"] = rows;
            payload.append(item);
        }
        cb(true, payload, {});
        return;
    }
    QJsonObject body;
    QJsonArray arr;
    for (const auto& u : uuids)
        arr.append(u);
    body["session_uuids"] = arr;
    body["format"] = "json";
    post("/chat/export", body, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, err);
            return;
        }
        const QJsonArray data = doc.object().value("data").toObject().value("data").toArray();
        cb(true, data, {});
    });
}

// ── Optimize prompt ───────────────────────────────────────────────────────────

void ChatModeService::optimize_prompt(const QString& prompt, const QString& mode, OptimizeCallback cb) {
    LOG_DEBUG("ChatModeService", QString("Prompt optimisation started mode=%1 prompt_chars=%2").arg(mode).arg(prompt.size()));
    if (!policy::fincept_chat_api_enabled()) {
        ai_chat::ChatRoute route;
        route.feature = QString::fromLatin1(ai_chat::kFeatureAgent);
        if (!ai_chat::LlmService::instance().is_configured(route)) {
            cb(false, {}, tr("No BYO model is configured. Add your DeepSeek API key in Settings → LLM Configuration."));
            return;
        }

        const QString request = prompt_optimization_request(prompt, mode);
        QPointer<ChatModeService> self(this);
        (void)QtConcurrent::run([self, prompt, request, route, cb = std::move(cb)]() mutable {
            const ai_chat::LlmResponse response =
                ai_chat::LlmService::instance().chat(request, {}, /*use_tools=*/false, route);
            QMetaObject::invokeMethod(
                QCoreApplication::instance(),
                [self, prompt, response, cb = std::move(cb)]() mutable {
                    if (!self)
                        return;
                    if (!response.success) {
                        cb(false, {}, response.error.trimmed().isEmpty()
                                          ? ChatModeService::tr("The configured BYO model did not return a prompt.")
                                          : response.error.trimmed());
                        return;
                    }
                    const QString optimized = clean_optimized_prompt(response.content);
                    if (optimized.isEmpty()) {
                        cb(false, {}, ChatModeService::tr("The configured BYO model returned an empty prompt."));
                        return;
                    }
                    OptimizedPrompt result;
                    result.original = prompt;
                    result.optimized = optimized;
                    cb(true, result, {});
                },
                Qt::QueuedConnection);
        });
        return;
    }

    QJsonObject body;
    body["prompt"] = prompt;
    if (!mode.isEmpty())
        body["mode"] = mode;

    // 45 s instead of the default 15 s watchdog — one LLM call on the server.
    post_slow("/chat/optimize-prompt", body, 45000, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, err);
            return;
        }
        const QJsonObject res_data = doc.object().value("data").toObject();
        OptimizedPrompt result;
        result.original = res_data["original"].toString();

        // Response can have optimized_prompt as array or optimized as string
        if (res_data.contains("optimized")) {
            result.optimized = res_data["optimized"].toString();
        } else if (res_data.contains("optimized_prompt")) {
            // Array format — extract the "text" type entry
            const QJsonArray arr = res_data["optimized_prompt"].toArray();
            for (const auto& v : arr) {
                const QJsonObject item = v.toObject();
                if (item["type"].toString() == "text") {
                    result.optimized = item["text"].toString();
                    break;
                }
            }
        }

        LOG_INFO("ChatModeService", QString("Prompt optimized: %1 chars -> %2 chars")
                                        .arg(result.original.length())
                                        .arg(result.optimized.length()));
        cb(true, result, {});
    });
}

// ── Agent memory ──────────────────────────────────────────────────────────────

void ChatModeService::list_memory(MemoriesCallback cb) {
    get("/chat/agent/memory", [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, err);
            return;
        }
        QVector<AgentMemory> memories;
        const QJsonArray arr = doc.object().value("memories").toArray();
        for (const auto& v : arr)
            memories.append(AgentMemory::from_json(v.toObject()));
        cb(true, memories, {});
    });
}

void ChatModeService::save_memory(const QString& key, const QString& value, const QString& memory_type,
                                  VoidCallback cb) {
    QJsonObject body;
    body["key"] = key;
    body["value"] = value;
    body["memory_type"] = memory_type;
    post("/chat/agent/memory", body, [cb = std::move(cb)](bool ok, QJsonDocument, QString err) { cb(ok, err); });
}

void ChatModeService::delete_memory(const QString& key, VoidCallback cb) {
    // Memory keys are free text typed by the user ("risk / appetite", "what?"): spliced raw
    // into the path, a '/', '?' or '#' addressed the wrong resource or was cut off.
    del("/chat/agent/memory/" + QString::fromUtf8(QUrl::toPercentEncoding(key)),
        [cb = std::move(cb)](bool ok, QJsonDocument, QString err) { cb(ok, err); });
}

void ChatModeService::clear_all_memory(VoidCallback cb) {
    del("/chat/agent/memory", [cb = std::move(cb)](bool ok, QJsonDocument, QString err) { cb(ok, err); });
}

// ── Agent investment profiles ─────────────────────────────────────────────────

void ChatModeService::list_profiles(ProfilesCallback cb) {
    // {"success": true, "message": "ok", "data": [{id, name, is_default, profile_data, …}]}
    // ordered default first (apiback agent_profile.py list_profiles).
    get("/profile/profiles", [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, err);
            return;
        }
        QVector<AgentProfile> profiles;
        for (const auto& v : doc.object().value("data").toArray()) {
            const AgentProfile p = AgentProfile::from_json(v.toObject());
            if (p.id > 0)
                profiles.append(p);
        }
        cb(true, profiles, {});
    });
}

// ── Credits ──────────────────────────────────────────────────────────────────

void ChatModeService::get_credits(CreditsCallback cb) {
    const QString path = QString("/user/profile?_t=%1").arg(QDateTime::currentMSecsSinceEpoch());
    get(path, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, 0, err);
            return;
        }
        // credit_balance is fractional (an AI call can cost 0.35). toInt()
        // returns 0 for ANY non-integer JSON number, so a 349.65 balance used
        // to show as "0 credits". Read and pass it as a double.
        const double balance = doc.object().value("data").toObject().value("credit_balance").toDouble();
        cb(true, std::max(0.0, balance), {});
    });
}

void ChatModeService::report_credit_balance(double balance) {
    const double b = std::max(0.0, balance);
    auth::AuthManager::instance().report_credit_balance(b); // the header chip
    emit credit_balance_changed(b);
}

void ChatModeService::refresh_credit_balance(int delay_ms) {
    // Chat replies carry no balance header: older servers (and turns whose `finish`
    // was lost) only reach the header chip through /user/profile, read once the
    // backend has settled the charge.
    QTimer::singleShot(std::max(0, delay_ms), this, [this]() {
        get_credits([this](bool ok, double credits, QString) {
            if (ok)
                report_credit_balance(credits);
        });
    });
}

// ── Terminal tool bridge ──────────────────────────────────────────────────────

void ChatModeService::register_terminal_tools(const QJsonArray& tools, const QString& version, int tool_count,
                                              RegisterCallback cb) {
    LOG_INFO("ChatModeService", QString("Registering %1 terminal tools (v%2)").arg(tool_count).arg(version));
    QJsonObject body;
    body["tools"] = tools;
    body["terminal_version"] = version;
    body["tool_count"] = tool_count;
    // {"registered": N, "status": "ok"} — newer servers add "agent_visible",
    // "rejected": [{name, reason}] and "ttl_seconds"; 503 X-Error-Code
    // terminal_bridge_unavailable (Retry-After 15) = the server's bridge is down.
    post_status("/chat/agent/terminal-tools/register", body, kDefaultTimeoutMs,
                [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err, ReplyInfo info) {
        if (!ok) {
            cb(false, {}, err, info.code == QLatin1String("terminal_bridge_unavailable"), info.retry_after_s);
            return;
        }
        cb(true, ToolRegistration::from_json(doc.object()), {}, false, 0);
    });
}

void ChatModeService::poll_pending_calls(PendingCallsCallback cb, int limit) {
    const QString path = QString("/chat/agent/terminal-tools/pending?limit=%1").arg(limit);
    // {"calls": [...]} — newer servers add "registered" (false: register again),
    // "agent_visible" and "available" (false: the bridge is down, do not register).
    get_status(path, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err, ReplyInfo info) {
        if (!ok) {
            cb(false, {}, err, info.retry_after_s);
            return;
        }
        cb(true, PendingToolCalls::from_json(doc.object()), {}, 0);
    });
}

void ChatModeService::submit_tool_result(const QString& call_id, const QJsonObject& result, ToolResultCallback cb) {
    QJsonObject body;
    body["call_id"] = call_id;
    body["result"] = result;
    // 200 stored; 409 call_already_answered; 404 call_not_found (unknown / expired;
    // older servers: no code, also "already resolved"); 503 terminal_bridge_unavailable.
    post_status("/chat/agent/terminal-tools/result", body, kDefaultTimeoutMs,
                [cb = std::move(cb)](bool ok, QJsonDocument, QString err, ReplyInfo info) {
        if (ok)
            cb(ToolResultDelivery::Delivered, {});
        else if (info.code == QLatin1String("call_already_answered") || (info.code.isEmpty() && info.status == 409))
            cb(ToolResultDelivery::AlreadyDelivered, err);
        else if (info.code == QLatin1String("call_not_found") || (info.code.isEmpty() && info.status == 404))
            cb(ToolResultDelivery::Expired, err);
        else
            cb(ToolResultDelivery::Failed, err);
    });
}

// ── Trade proposals ───────────────────────────────────────────────────────────

void ChatModeService::decide_proposal(const QString& proposal_id, bool approved, const QString& session_id,
                                      ProposalCallback cb) {
    LOG_INFO("ChatModeService", QString("Deciding proposal %1: %2").arg(proposal_id, approved ? "approve" : "reject"));
    QJsonObject body;
    body["approved"] = approved;
    if (!session_id.isEmpty())
        body["session_id"] = session_id;
    // The service waits up to 10 s for a concurrent decision before answering 409,
    // then the trade itself runs: allow well over the default 15 s.
    post_status("/chat/agent/proposals/" + QString::fromUtf8(QUrl::toPercentEncoding(proposal_id)), body, 45000,
                [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err, ReplyInfo info) {
        if (ok) {
            const ProposalDecision d = ProposalDecision::from_json(doc.object());
            if (d.status.isEmpty()) {
                // A 200 without a decision is not this route's answer.
                cb(ProposalOutcome::Failed, d, tr("The server sent an answer without a decision."), 0);
                return;
            }
            cb(ProposalOutcome::Decided, d, d.message, 0);
            return;
        }
        if (info.route_missing) { // 404/405 without X-Error-Code and without the API envelope
            LOG_INFO("ChatModeService",
                     QString("Proposals route not available on this server (HTTP %1)").arg(info.status));
            cb(ProposalOutcome::RouteMissing, {}, {}, 0);
            return;
        }
        // X-Error-Code first (proposal_not_found / proposal_expired / proposal_in_progress);
        // the status alone on a server that does not send it.
        const QString& c = info.code;
        if (c == QLatin1String("proposal_not_found") || (c.isEmpty() && info.status == 404))
            cb(ProposalOutcome::Gone, {}, err, 0); // not found, not yours, or no longer pending
        else if (c == QLatin1String("proposal_expired") || (c.isEmpty() && info.status == 410))
            cb(ProposalOutcome::Expired, {}, err, 0);
        else if (c == QLatin1String("proposal_in_progress") || (c.isEmpty() && info.status == 409))
            cb(ProposalOutcome::InProgress, {}, err, info.retry_after_s); // another decision is running
        else
            cb(ProposalOutcome::Failed, {}, err, info.retry_after_s);
    });
}

// ── MCP servers ───────────────────────────────────────────────────────────────

void ChatModeService::list_mcp_servers(McpServersCallback cb) {
    get("/chat/agent/mcp/servers", [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, 0, err);
            return;
        }
        const QJsonObject root = doc.object();
        QVector<McpServer> servers;
        for (const auto& v : root["servers"].toArray())
            servers.append(McpServer::from_json(v.toObject()));
        const int total_tools = root["total_tools"].toInt();
        cb(true, servers, total_tools, {});
    });
}

void ChatModeService::add_mcp_server(const QString& name, const QJsonObject& config, McpServerCallback cb) {
    LOG_INFO("ChatModeService", QString("Adding MCP server: %1").arg(name));
    QJsonObject body;
    body["name"] = name;
    body["config"] = config;
    const QString transport = config.value("transport").toString();
    post("/chat/agent/mcp/servers", body, [cb = std::move(cb), name, transport](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            // `err` is the backend's own explanation of a rejected server (non-https URL,
            // private address, stdio not allowed, name/limit rules…) — see handle_reply().
            cb(false, {}, err);
            return;
        }
        const QJsonObject root = doc.object();
        McpServer srv;
        srv.name = root["name"].toString(name);
        srv.transport = transport;
        srv.tools_count = root["tools_loaded"].toInt();
        srv.error = root["error"].toString();
        srv.status = srv.error.isEmpty() ? "connected" : "error";
        for (const auto& v : root["tools"].toArray())
            srv.tools.append(McpTool::from_json(v.toObject()));
        cb(true, srv, {});
    });
}

void ChatModeService::delete_mcp_server(const QString& server_name, VoidCallback cb) {
    // Server names are user-chosen free text — encode, same as delete_memory().
    del("/chat/agent/mcp/servers/" + QString::fromUtf8(QUrl::toPercentEncoding(server_name)),
        [cb = std::move(cb)](bool ok, QJsonDocument, QString err) { cb(ok, err); });
}

void ChatModeService::refresh_mcp_servers(McpServersCallback cb) {
    post("/chat/agent/mcp/servers/refresh", {}, [cb = std::move(cb)](bool ok, QJsonDocument doc, QString err) {
        if (!ok) {
            cb(false, {}, 0, err);
            return;
        }
        const QJsonObject root = doc.object();
        QVector<McpServer> servers;
        for (const auto& v : root["servers"].toArray())
            servers.append(McpServer::from_json(v.toObject()));
        const int total_tools = root["total_tools"].toInt();
        cb(true, servers, total_tools, {});
    });
}

} // namespace fincept::chat_mode
