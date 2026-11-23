#include "ui/widgets/ProAnalytics.h"
#include "ui/widgets/ProAnalyticsAi.h"
#include "ui/widgets/ProAnalyticsAiCache.h"

#include "auth/AuthTypes.h"
#include "core/agent/AgentRiskMarkers.h"
#include "core/config/AppPaths.h"
#include "core/logging/Logger.h"
#include "core/policy/CommunityPolicy.h"
#include "python/PythonRunner.h"
#include "services/llm/LlmService.h"
#include "services/llm/ModelRouter.h"
#include "storage/cache/CacheManager.h"
#include "ui/theme/Theme.h"
#include "ui/theme/ThemeManager.h"
#include "ui/widgets/EnterprisePromo.h"
#include "ui/widgets/ThemeHelpers.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QEvent>
#include <QFrame>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QHash>
#include <QHeaderView>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QPointer>
#include <QPushButton>
#include <QScrollArea>
#include <QStackedWidget>
#include <QTableWidget>
#include <QVBoxLayout>
#include <QtConcurrent>

#include <utility>

namespace fincept::ui {

namespace {

constexpr const char* kProAnalyticsTag = "ProAnalytics";
constexpr int kProAnalyticsStatColumns = 4;
constexpr int kProAnalyticsMaxHoldings = 15;
constexpr int kProAnalyticsReportCacheTtlSec = 5 * 60;

QString pro_analytics_tr(const char* text) {
    return QCoreApplication::translate("fincept::ui::pro", text);
}

/// "positive" / "negative" / "neutral" from the server, anything else neutral.
QString pro_analytics_tone(const QJsonValue& v) {
    const QString t = v.toString();
    return (t == QLatin1String("positive") || t == QLatin1String("negative")) ? t : QStringLiteral("neutral");
}

/// One sheet for the panel, set before any child exists; the central re-style
/// pass keeps it on the active theme and font.
QString pro_analytics_sheet() {
    return QString("QWidget#proAnalytics{background:%1;}"
                   "QLabel#paIntroTitle{color:%2;font-size:" + fonts::px(3) + "px;font-weight:700;background:transparent;}"
                   "QLabel#paIntroText{color:%3;font-size:" + fonts::px(0) + "px;background:transparent;}"
                   "QLabel#paIntroCost{color:%4;font-size:" + fonts::px(-1) + "px;background:transparent;}"
                   "QLabel#paState{color:%3;font-size:" + fonts::px(0) + "px;background:transparent;}"
                   "QLabel#paState[tone=\"error\"]{color:%5;}"
                   "QLabel#paHeadline{color:%2;font-size:" + fonts::px(2) + "px;font-weight:600;background:transparent;}"
                   "QLabel#paHeadline[tone=\"positive\"]{color:%6;}"
                   "QLabel#paHeadline[tone=\"negative\"]{color:%5;}"
                   "QLabel#paMeta{color:%4;font-size:" + fonts::px(-3) + "px;background:transparent;}"
                   "QFrame#paTile{background:%7;border:1px solid %8;}"
                   "QLabel#paTileLabel{color:%4;font-size:" + fonts::px(-3) + "px;font-weight:700;background:transparent;}"
                   "QLabel#paTileValue{color:%2;font-size:" + fonts::px(3) + "px;font-weight:700;background:transparent;}"
                   "QLabel#paTileValue[tone=\"positive\"]{color:%6;}"
                   "QLabel#paTileValue[tone=\"negative\"]{color:%5;}"
                   "QLabel#paTileHint{color:%4;font-size:" + fonts::px(-3) + "px;background:transparent;}"
                   "QLabel#paSection{color:%9;font-size:" + fonts::px(-2) + "px;font-weight:700;background:transparent;}"
                   "QLabel#paNote{color:%4;font-size:" + fonts::px(-2) + "px;background:transparent;}"
                   "QLabel#paAiText{color:%2;font-size:" + fonts::px(-1) + "px;background:%7;border:1px solid %8;"
                   "padding:10px;}"
                   "QTableWidget#paTable{background:%7;color:%2;border:1px solid %8;gridline-color:%8;font-size:" +
                   fonts::px(-1) + "px;}"
                   "QTableWidget#paTable QHeaderView::section{background:%1;color:%4;border:none;"
                   "border-bottom:1px solid %8;padding:3px 6px;font-size:" + fonts::px(-3) + "px;font-weight:700;}"
                   "QScrollArea#paScroll{background:transparent;border:none;}"
                   "QWidget#paResult{background:transparent;}")
        .arg(colors::BG_BASE(), colors::TEXT_PRIMARY(), colors::TEXT_SECONDARY(), colors::TEXT_TERTIARY(),
             colors::DANGER(), colors::SUCCESS(), colors::BG_SURFACE(), colors::BORDER_DIM(), colors::AMBER());
}

QString pro_analytics_button_sheet() {
    return QString("QPushButton#proAnalyticsBtn{color:%1;background:transparent;border:1px solid %2;padding:2px 8px;"
                   "font-size:" + fonts::px(-2) + "px;font-weight:700;}"
                   "QPushButton#proAnalyticsBtn:hover{background:%3;border-color:%1;}")
        .arg(colors::AMBER(), colors::AMBER_DIM(), colors::ACCENT_BG());
}

/// These reports use local computation and public market data. Mark the route
/// explicitly so automation never mistakes the button for a Fincept-credit run.
void pro_analytics_mark_spend(QPushButton* btn) {
    if (!btn)
        return;
    btn->setProperty(fincept::core::agent::kAgentRiskProperty, QVariant());
    btn->setProperty("fincept_cost_route", QStringLiteral("local_public_data"));
}

/// AI explanation is explicit and may consume the user's provider quota, but
/// never Fincept credits. Keep that distinction visible to agent automation.
void pro_analytics_mark_byo(QPushButton* btn) {
    if (!btn)
        return;
    btn->setProperty(fincept::core::agent::kAgentRiskProperty, QStringLiteral("spend"));
    btn->setProperty("fincept_cost_route", QStringLiteral("byo_api"));
}

QString pro_analytics_kind_id(pro::Kind kind) {
    switch (kind) {
        case pro::Kind::OptionsPositioning:
            return QStringLiteral("options_positioning");
        case pro::Kind::TechnicalRating:
            return QStringLiteral("technical_rating");
        case pro::Kind::PortfolioRisk:
            return QStringLiteral("portfolio_risk");
        case pro::Kind::MarketPulse:
            break;
    }
    return QStringLiteral("market_pulse");
}

QJsonObject pro_analytics_payload(const pro::Request& request) {
    QJsonObject payload{{QStringLiteral("kind"), pro_analytics_kind_id(request.kind)}};
    if (!request.symbol.trimmed().isEmpty())
        payload.insert(QStringLiteral("symbol"), request.symbol.trimmed().toUpper());
    if (!request.symbols.isEmpty()) {
        QJsonArray symbols;
        for (const QString& symbol : request.symbols)
            symbols.append(symbol.trimmed().toUpper());
        payload.insert(QStringLiteral("symbols"), symbols);
    }
    if (!request.weights.isEmpty()) {
        QJsonArray weights;
        for (double weight : request.weights)
            weights.append(weight);
        payload.insert(QStringLiteral("weights"), weights);
    }
    return payload;
}

QString pro_analytics_report_cache_key(const QByteArray& payload) {
    const QByteArray digest = QCryptographicHash::hash(payload, QCryptographicHash::Sha256).toHex();
    return QStringLiteral("pro_analytics:report:v1:") + QString::fromLatin1(digest);
}

/// One report window per kind, process-wide.
QHash<int, QPointer<ProAnalyticsDialog>>& pro_analytics_windows() {
    static QHash<int, QPointer<ProAnalyticsDialog>> windows;
    return windows;
}

} // namespace

// ── pro:: ────────────────────────────────────────────────────────────────────

namespace pro {

QString title(Kind kind) {
    switch (kind) {
        case Kind::OptionsPositioning:
            return pro_analytics_tr("Options positioning");
        case Kind::TechnicalRating:
            return pro_analytics_tr("Technical rating");
        case Kind::PortfolioRisk:
            return pro_analytics_tr("Portfolio risk");
        case Kind::MarketPulse:
            break;
    }
    return pro_analytics_tr("Market pulse");
}

QString description(Kind kind) {
    switch (kind) {
        case Kind::OptionsPositioning:
            return pro_analytics_tr("How the options market is positioned in a symbol: put/call ratios, max pain, the "
                                    "call and put walls, the expected move, implied volatility and skew, dealer gamma "
                                    "by strike and unusual activity, for the nearest expirations.");
        case Kind::TechnicalRating:
            return pro_analytics_tr("A buy / sell rating from 10 moving averages and 6 oscillators, with the trend "
                                    "state, performance, 52-week range, volatility, volume and relative strength "
                                    "against the S&P 500.");
        case Kind::PortfolioRisk:
            return pro_analytics_tr("The risk of a set of holdings: volatility, drawdown, value at risk, beta and "
                                    "alpha against the S&P 500, the correlation matrix, how well the holdings "
                                    "diversify each other and which one carries the most risk.");
        case Kind::MarketPulse:
            break;
    }
    return pro_analytics_tr("The market regime in one view: seven risk-appetite signals, sector breadth and "
                            "leadership, and performance of equities, sectors, bonds, commodities, the dollar and "
                            "bitcoin from one day to one year.");
}

bool available() {
    return true;
}

void open(const Request& request, QWidget* origin) {
    auto& slot = pro_analytics_windows()[static_cast<int>(request.kind)];
    if (!slot) {
        slot = new ProAnalyticsDialog(request.kind, origin ? origin->window() : nullptr);
        slot->setAttribute(Qt::WA_DeleteOnClose);
    }
    slot->set_request(request);
    slot->show();
    slot->raise();
    slot->activateWindow();
}

QPushButton* make_button(Kind kind, std::function<Request()> provider, QWidget* parent) {
    auto* btn = new QPushButton(parent);
    btn->setObjectName(QStringLiteral("proAnalyticsBtn"));
    btn->setCursor(Qt::PointingHandCursor);
    btn->setFocusPolicy(Qt::TabFocus);
    btn->setStyleSheet(pro_analytics_button_sheet());
    btn->setText(QStringLiteral("LOCAL  ") + title(kind).toUpper());
    btn->setAccessibleName(pro_analytics_tr("Open the local analytics report: %1").arg(title(kind)));
    btn->setToolTip(pro_analytics_tr("%1 — local calculation with public market data; 0 Fincept credits.\n%2")
                        .arg(title(kind), description(kind)));
    pro_analytics_mark_spend(btn);
    QObject::connect(btn, &QPushButton::clicked, btn, [btn, kind, provider = std::move(provider)]() {
        Request request = provider ? provider() : Request{};
        request.kind = kind;
        open(request, btn);
    });
    return btn;
}

} // namespace pro

// ── ProAnalyticsPanel ────────────────────────────────────────────────────────

ProAnalyticsPanel::ProAnalyticsPanel(QWidget* parent) : QWidget(parent) {
    setObjectName(QStringLiteral("proAnalytics"));
    setAttribute(Qt::WA_StyledBackground, true);
    setStyleSheet(pro_analytics_sheet());

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);
    pages_ = new QStackedWidget(this);
    root->addWidget(pages_, 1);

    // Intro: what the report is, its zero-credit route, RUN.
    auto* intro = new QWidget(pages_);
    auto* il = new QVBoxLayout(intro);
    il->setContentsMargins(24, 24, 24, 24);
    il->setSpacing(10);
    intro_title_ = new QLabel(intro);
    intro_title_->setObjectName(QStringLiteral("paIntroTitle"));
    il->addWidget(intro_title_);
    intro_text_ = new QLabel(intro);
    intro_text_->setObjectName(QStringLiteral("paIntroText"));
    intro_text_->setWordWrap(true);
    il->addWidget(intro_text_);
    intro_cost_ = new QLabel(intro);
    intro_cost_->setObjectName(QStringLiteral("paIntroCost"));
    intro_cost_->setWordWrap(true);
    il->addWidget(intro_cost_);
    auto* irow = new QHBoxLayout;
    irow->setContentsMargins(0, 6, 0, 0);
    run_btn_ = enterprise::make_cta_button(QString(), QString(), enterprise::CtaStyle::Primary, intro);
    run_btn_->setObjectName(QStringLiteral("proAnalyticsRun"));
    connect(run_btn_, &QPushButton::clicked, this, &ProAnalyticsPanel::run);
    irow->addWidget(run_btn_);
    irow->addStretch();
    il->addLayout(irow);
    il->addStretch();
    pages_->addWidget(intro);

    // Loading.
    auto* loading = new QWidget(pages_);
    auto* ll = new QVBoxLayout(loading);
    ll->setContentsMargins(24, 24, 24, 24);
    loading_label_ = new QLabel(loading);
    loading_label_->setObjectName(QStringLiteral("paState"));
    ll->addWidget(loading_label_);
    ll->addStretch();
    pages_->addWidget(loading);

    // Failed.
    auto* failed = new QWidget(pages_);
    auto* fl = new QVBoxLayout(failed);
    fl->setContentsMargins(24, 24, 24, 24);
    fl->setSpacing(10);
    error_label_ = new QLabel(failed);
    error_label_->setObjectName(QStringLiteral("paState"));
    error_label_->setProperty("tone", "error");
    error_label_->setWordWrap(true);
    error_label_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    fl->addWidget(error_label_);
    auto* frow = new QHBoxLayout;
    retry_btn_ = enterprise::make_cta_button(QString(), QString(), enterprise::CtaStyle::Outline, failed);
    retry_btn_->setObjectName(QStringLiteral("proAnalyticsRetry"));
    connect(retry_btn_, &QPushButton::clicked, this, &ProAnalyticsPanel::run);
    frow->addWidget(retry_btn_);
    plans_btn_ = enterprise::make_cta_button(QString(), QString(), enterprise::CtaStyle::Primary, failed);
    plans_btn_->setObjectName(QStringLiteral("proAnalyticsPlans"));
    plans_btn_->setVisible(policy::commercial_promotions_enabled());
    connect(plans_btn_, &QPushButton::clicked, this, [this]() { billing::open_plans(this); });
    frow->addWidget(plans_btn_);
    frow->addStretch();
    fl->addLayout(frow);
    fl->addStretch();
    pages_->addWidget(failed);

    // Result.
    result_scroll_ = new QScrollArea(pages_);
    result_scroll_->setObjectName(QStringLiteral("paScroll"));
    result_scroll_->setWidgetResizable(true);
    result_scroll_->setFrameShape(QFrame::NoFrame);
    pages_->addWidget(result_scroll_);

    sync_risk_markers();
    retranslate();
}

void ProAnalyticsPanel::sync_risk_markers() {
    pro_analytics_mark_spend(run_btn_);
    pro_analytics_mark_spend(retry_btn_);
}

void ProAnalyticsPanel::changeEvent(QEvent* event) {
    QWidget::changeEvent(event);
    if (event->type() == QEvent::LanguageChange)
        retranslate();
}

void ProAnalyticsPanel::retranslate() {
    loading_label_->setText(tr("Computing locally from public market data…"));
    retry_btn_->setText(tr("RETRY"));
    retry_btn_->setAccessibleName(tr("Run the report again"));
    plans_btn_->setText(tr("VIEW PLANS"));
    plans_btn_->setAccessibleName(tr("Open Plans and Pricing"));
    run_btn_->setAccessibleName(tr("Run the report"));
    if (pages_->currentIndex() == Intro)
        show_intro();
}

bool ProAnalyticsPanel::request_ready(QString* why) const {
    switch (request_.kind) {
        case pro::Kind::OptionsPositioning:
        case pro::Kind::TechnicalRating:
            if (request_.symbol.trimmed().isEmpty()) {
                if (why)
                    *why = tr("Enter a symbol above, then run the report.");
                return false;
            }
            return true;
        case pro::Kind::PortfolioRisk:
            if (request_.symbols.isEmpty()) {
                if (why)
                    *why = tr("Enter the holdings above (comma-separated symbols), then run the report.");
                return false;
            }
            if (request_.symbols.size() > kProAnalyticsMaxHoldings) {
                if (why)
                    *why = tr("The report covers up to %1 holdings — remove some, then run it.")
                               .arg(kProAnalyticsMaxHoldings);
                return false;
            }
            return true;
        case pro::Kind::MarketPulse:
            break;
    }
    return true;
}

void ProAnalyticsPanel::set_request(const pro::Request& request, bool autorun) {
    request_ = request;
    ++generation_; // an answer still on its way belongs to the previous subject
    // Opening the feature is an explicit request and this route uses no
    // Fincept credits, so a ready request can run immediately on every plan.
    if (autorun && request_ready(nullptr))
        run();
    else
        show_intro();
}

void ProAnalyticsPanel::show_intro() {
    intro_title_->setText(pro::title(request_.kind));
    QString why;
    const bool ready = request_ready(&why);
    intro_text_->setText(ready ? pro::description(request_.kind) : pro::description(request_.kind) + QLatin1Char('\n') + why);
    intro_cost_->setText(tr("LOCAL PUBLIC DATA · 0 Fincept credits · no AI model call. "
                            "Quotes can be delayed and depend on public-source availability."));
    run_btn_->setText(tr("RUN REPORT"));
    run_btn_->setEnabled(ready);
    pages_->setCurrentIndex(Intro);
}

void ProAnalyticsPanel::show_error(const QString& message, bool out_of_credits) {
    error_label_->setText(message);
    plans_btn_->setVisible(policy::commercial_promotions_enabled() && out_of_credits);
    retry_btn_->setVisible(!out_of_credits);
    pages_->setCurrentIndex(Failed);
}

void ProAnalyticsPanel::run() {
    QString why;
    if (!request_ready(&why)) {
        show_intro();
        return;
    }
    const quint64 generation = ++generation_;
    pages_->setCurrentIndex(Loading);
    LOG_DEBUG(kProAnalyticsTag, QString("Local report requested (kind %1)").arg(static_cast<int>(request_.kind)));
    const QByteArray payload_bytes = QJsonDocument(pro_analytics_payload(request_)).toJson(QJsonDocument::Compact);
    const QString payload = QString::fromUtf8(payload_bytes);
    const QString cache_key = pro_analytics_report_cache_key(payload_bytes);
    if (const auto cached = CacheManager::instance().try_get(cache_key)) {
        QJsonParseError cached_error;
        const QJsonDocument cached_document = QJsonDocument::fromJson(cached->toUtf8(), &cached_error);
        const QJsonObject cached_body = cached_document.object();
        const QJsonObject cached_data = cached_body.value(QStringLiteral("data")).toObject();
        if (cached_error.error == QJsonParseError::NoError && cached_document.isObject() &&
            cached_data.value(QStringLiteral("report")).isObject()) {
            QJsonObject cached_meta = cached_body.value(QStringLiteral("meta")).toObject();
            cached_meta.insert(QStringLiteral("cache_hit"), true);
            show_report(cached_data, cached_meta);
            return;
        }
        CacheManager::instance().remove(cache_key);
    }
    python::PythonRunner::RunOptions options;
    options.timeout_ms = 120000;
    options.credential_allowlist = QStringList{}; // deterministic analytics never receive user secrets
    options.max_output_bytes = 2 * 1024 * 1024;
    QPointer<ProAnalyticsPanel> self(this);
    python::PythonRunner::instance().run_with_options(
        QStringLiteral("byo_pro_analytics.py"), {QStringLiteral("report"), payload}, options,
        [self, generation, cache_key](python::PythonResult result) {
            if (!self || generation != self->generation_)
                return; // superseded by a newer subject or run
            if (!result.success) {
                self->show_error(result.error.isEmpty() ? self->tr("The local report could not be computed.")
                                                        : result.error,
                                 false);
                return;
            }
            QJsonParseError parse_error;
            const QJsonDocument document = QJsonDocument::fromJson(result.output.toUtf8(), &parse_error);
            if (parse_error.error != QJsonParseError::NoError || !document.isObject()) {
                self->show_error(self->tr("The local analytics adapter returned an invalid report."), false);
                return;
            }
            const QJsonObject body = document.object();
            const QJsonObject data = body.value(QStringLiteral("data")).toObject();
            if (!data.value(QStringLiteral("report")).isObject()) {
                self->show_error(self->tr("The local analytics adapter answered without a report."), false);
                return;
            }
            CacheManager::instance().put(
                cache_key, QString::fromUtf8(document.toJson(QJsonDocument::Compact)),
                kProAnalyticsReportCacheTtlSec, QStringLiteral("pro_analytics"));
            self->show_report(data, body.value(QStringLiteral("meta")).toObject());
        },
        {});
}

void ProAnalyticsPanel::show_report(const QJsonObject& data, const QJsonObject& meta) {
    const QJsonObject report = data.value(QStringLiteral("report")).toObject();
    auto* host = new QWidget(result_scroll_);
    host->setObjectName(QStringLiteral("paResult"));
    auto* vl = new QVBoxLayout(host);
    vl->setContentsMargins(20, 16, 20, 20);
    vl->setSpacing(10);

    auto* headline = new QLabel(report.value(QStringLiteral("headline")).toString(), host);
    headline->setObjectName(QStringLiteral("paHeadline"));
    headline->setProperty("tone", pro_analytics_tone(report.value(QStringLiteral("tone"))));
    headline->setWordWrap(true);
    headline->setTextInteractionFlags(Qt::TextSelectableByMouse);
    vl->addWidget(headline);

    // "as of" from the response meta: when the underlying data was fetched.
    QStringList meta_bits;
    const QString as_of = meta.value(QStringLiteral("as_of")).toString();
    if (!as_of.isEmpty())
        meta_bits << tr("Data as of %1").arg(auth::format_local_stamp(as_of, true));
    if (meta.value(QStringLiteral("delayed_minutes")).toInt() > 0)
        meta_bits << tr("%1-minute delayed").arg(meta.value(QStringLiteral("delayed_minutes")).toInt());
    if (meta.value(QStringLiteral("stale")).toBool())
        meta_bits << tr("source unavailable — showing the last good data");
    if (meta.value(QStringLiteral("cache_hit")).toBool())
        meta_bits << tr("cached local copy");
    if (!meta_bits.isEmpty()) {
        auto* meta_label = new QLabel(meta_bits.join(QStringLiteral("  ·  ")), host);
        meta_label->setObjectName(QStringLiteral("paMeta"));
        vl->addWidget(meta_label);
    }

    // Stat tiles.
    const QJsonArray stats = report.value(QStringLiteral("stats")).toArray();
    if (!stats.isEmpty()) {
        auto* grid = new QGridLayout;
        grid->setContentsMargins(0, 4, 0, 4);
        grid->setSpacing(8);
        int i = 0;
        for (const auto& v : stats) {
            const QJsonObject s = v.toObject();
            auto* tile = new QFrame(host);
            tile->setObjectName(QStringLiteral("paTile"));
            auto* tl = new QVBoxLayout(tile);
            tl->setContentsMargins(10, 8, 10, 8);
            tl->setSpacing(2);
            auto* label = new QLabel(s.value(QStringLiteral("label")).toString().toUpper(), tile);
            label->setObjectName(QStringLiteral("paTileLabel"));
            label->setWordWrap(true);
            tl->addWidget(label);
            auto* value = new QLabel(s.value(QStringLiteral("value")).toString(), tile);
            value->setObjectName(QStringLiteral("paTileValue"));
            value->setProperty("tone", pro_analytics_tone(s.value(QStringLiteral("tone"))));
            value->setTextInteractionFlags(Qt::TextSelectableByMouse);
            tl->addWidget(value);
            const QString hint_text = s.value(QStringLiteral("hint")).toString();
            if (!hint_text.isEmpty()) {
                auto* hint = new QLabel(hint_text, tile);
                hint->setObjectName(QStringLiteral("paTileHint"));
                hint->setWordWrap(true);
                tl->addWidget(hint);
            }
            tl->addStretch();
            grid->addWidget(tile, i / kProAnalyticsStatColumns, i % kProAnalyticsStatColumns);
            ++i;
        }
        for (int c = 0; c < kProAnalyticsStatColumns; ++c)
            grid->setColumnStretch(c, 1);
        vl->addLayout(grid);
    }

    // Tables.
    const int row_height = metrics::row_height(-1, 22);
    for (const auto& v : report.value(QStringLiteral("tables")).toArray()) {
        const QJsonObject t = v.toObject();
        const QJsonArray columns = t.value(QStringLiteral("columns")).toArray();
        const QJsonArray rows = t.value(QStringLiteral("rows")).toArray();
        if (columns.isEmpty() || rows.isEmpty())
            continue;
        auto* section = new QLabel(t.value(QStringLiteral("title")).toString().toUpper(), host);
        section->setObjectName(QStringLiteral("paSection"));
        vl->addSpacing(4);
        vl->addWidget(section);
        auto* table = new QTableWidget(static_cast<int>(rows.size()), static_cast<int>(columns.size()), host);
        table->setObjectName(QStringLiteral("paTable"));
        table->setEditTriggers(QAbstractItemView::NoEditTriggers);
        table->setSelectionMode(QAbstractItemView::NoSelection);
        table->setFocusPolicy(Qt::NoFocus);
        table->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
        table->setHorizontalScrollBarPolicy(Qt::ScrollBarAsNeeded);
        table->verticalHeader()->setVisible(false);
        table->verticalHeader()->setDefaultSectionSize(row_height);
        table->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
        table->horizontalHeader()->setHighlightSections(false);
        QStringList heads;
        for (const auto& c : columns)
            heads << c.toString();
        table->setHorizontalHeaderLabels(heads);
        for (int r = 0; r < rows.size(); ++r) {
            const QJsonArray cells = rows.at(r).toArray();
            for (int c = 0; c < columns.size() && c < cells.size(); ++c) {
                auto* item = new QTableWidgetItem(cells.at(c).toString());
                item->setTextAlignment((c == 0 ? Qt::AlignLeft : Qt::AlignRight) | Qt::AlignVCenter);
                table->setItem(r, c, item);
            }
        }
        // The whole table is shown (the page scrolls, not the table).
        table->setFixedHeight(table->horizontalHeader()->sizeHint().height() + row_height * static_cast<int>(rows.size()) +
                              2 * table->frameWidth() + 2);
        vl->addWidget(table);
    }

    for (const auto& v : report.value(QStringLiteral("notes")).toArray()) {
        auto* note = new QLabel(v.toString(), host);
        note->setObjectName(QStringLiteral("paNote"));
        note->setWordWrap(true);
        vl->addWidget(note);
    }

    // The deterministic report above is complete without AI. Explanation is a
    // separate, explicit BYO request so opening or refreshing a report never
    // consumes the user's provider quota.
    vl->addSpacing(8);
    auto* ai_title = new QLabel(tr("BYO AI EXPLANATION"), host);
    ai_title->setObjectName(QStringLiteral("paSection"));
    vl->addWidget(ai_title);
    const ai_chat::ModelChoice ai_choice =
        ai_chat::ModelRouter::instance().resolve(QString::fromLatin1(ai_chat::kFeatureProAnalyticsExplain));
    auto* ai_status = new QLabel(
        tr("Optional · sends this completed report to %1 only when clicked · 0 Fincept credits · your API provider "
           "may bill usage")
            .arg(ai_choice.label.isEmpty() ? tr("your active BYO provider") : ai_choice.label),
        host);
    ai_status->setObjectName(QStringLiteral("paMeta"));
    ai_status->setWordWrap(true);
    vl->addWidget(ai_status);
    auto* ai_button = enterprise::make_cta_button(tr("EXPLAIN WITH BYO AI"), QString(),
                                                   enterprise::CtaStyle::Outline, host);
    ai_button->setObjectName(QStringLiteral("proAnalyticsExplain"));
    ai_button->setAccessibleName(tr("Explain this report with the configured BYO AI provider"));
    ai_button->setToolTip(
        tr("Sends this report to your configured BYO provider. Uses 0 Fincept credits; provider charges may apply."));
    pro_analytics_mark_byo(ai_button);
    auto* ai_output = new QLabel(host);
    ai_output->setObjectName(QStringLiteral("paAiText"));
    ai_output->setTextFormat(Qt::PlainText);
    ai_output->setWordWrap(true);
    ai_output->setTextInteractionFlags(Qt::TextSelectableByMouse);
    ai_output->hide();
    connect(ai_button, &QPushButton::clicked, this,
            [this, report, meta, ai_button, ai_status, ai_output]() {
                run_ai_explanation(report, meta, ai_button, ai_status, ai_output);
            });
    vl->addWidget(ai_button, 0, Qt::AlignLeft);
    vl->addWidget(ai_output);
    vl->addStretch();

    result_scroll_->setWidget(host); // deletes the previous report
    pages_->setCurrentIndex(Result);
}

void ProAnalyticsPanel::run_ai_explanation(const QJsonObject& report, const QJsonObject& meta, QPushButton* button,
                                           QLabel* status, QLabel* output) {
    if (!button || !status || !output)
        return;
    const quint64 report_generation = generation_;
    const QString prompt = pro_ai::explanation_prompt(pro::title(request_.kind), report, meta);
    const ai_chat::ModelChoice choice =
        ai_chat::ModelRouter::instance().resolve(QString::fromLatin1(ai_chat::kFeatureProAnalyticsExplain));
    const QString cache_key =
        pro_ai::explanation_cache_key(prompt, choice.provider_id(), choice.model, 1, choice.profile_id);
    const QString cache_dir = AppPaths::cache() + QStringLiteral("/pro_analytics_ai");
    const bool bypass_cache = button->property("proAnalyticsBypassCache").toBool();
    if (!bypass_cache) {
        const auto cached = pro_ai::load_explanation_cache(cache_dir, cache_key, QDateTime::currentMSecsSinceEpoch());
        if (cached) {
            QStringList bits;
            bits << tr("Saved explanation reused")
                 << (cached->route_label.isEmpty() ? tr("BYO AI") : cached->route_label)
                 << tr("0 Fincept credits") << tr("no API call");
            status->setText(bits.join(QStringLiteral("  ·  ")));
            output->setText(cached->text);
            output->show();
            button->setText(tr("REFRESH WITH BYO AI"));
            button->setProperty("proAnalyticsBypassCache", true);
            return;
        }
    }
    button->setEnabled(false);
    button->setText(tr("ANALYZING…"));
    status->setText(tr("Your configured BYO model is explaining the supplied report…"));
    output->hide();
    button->setProperty("proAnalyticsBypassCache", false);

    QPointer<ProAnalyticsPanel> self(this);
    QPointer<QPushButton> guarded_button(button);
    QPointer<QLabel> guarded_status(status);
    QPointer<QLabel> guarded_output(output);
    (void)QtConcurrent::run([self, guarded_button, guarded_status, guarded_output, report_generation, prompt,
                             cache_dir, cache_key]() {
        ai_chat::ChatRoute route;
        route.feature = QString::fromLatin1(ai_chat::kFeatureProAnalyticsExplain);
        const ai_chat::LlmResponse response = ai_chat::LlmService::instance().chat(prompt, {}, false, route);
        const auto explanation = response.success ? pro_ai::clean_explanation(response.content) : std::nullopt;
        if (explanation && pro_ai::explanation_response_is_cacheable(
                               response.success, response.cancelled, *explanation, response.fallback_notice)) {
            pro_ai::CachedExplanation entry;
            entry.text = *explanation;
            entry.route_label = response.route_label;
            entry.billing_note = response.billing_note;
            entry.created_at_ms = QDateTime::currentMSecsSinceEpoch();
            if (!pro_ai::store_explanation_cache(cache_dir, cache_key, entry))
                LOG_WARN(kProAnalyticsTag, QStringLiteral("Could not save the local BYO explanation cache"));
        }
        if (!self)
            return;
        QMetaObject::invokeMethod(
            self,
            [self, guarded_button, guarded_status, guarded_output, report_generation, response, explanation]() {
                if (!self || report_generation != self->generation_ || !guarded_button || !guarded_status ||
                    !guarded_output)
                    return;
                guarded_button->setEnabled(true);
                guarded_button->setText(self->tr("EXPLAIN AGAIN WITH BYO AI"));
                if (!response.success || !explanation) {
                    const QString reason = response.error.trimmed().isEmpty()
                                               ? self->tr("The configured provider returned no explanation.")
                                               : response.error.trimmed();
                    guarded_status->setText(
                        self->tr("Could not explain this report: %1 Check Settings → LLM Configuration.").arg(reason));
                    guarded_output->hide();
                    return;
                }
                guarded_button->setProperty("proAnalyticsBypassCache", true);
                QStringList route_bits;
                route_bits << (response.route_label.isEmpty() ? self->tr("BYO AI") : response.route_label)
                           << self->tr("0 Fincept credits");
                if (!response.billing_note.isEmpty())
                    route_bits << response.billing_note;
                else
                    route_bits << self->tr("your API provider may bill usage");
                guarded_status->setText(route_bits.join(QStringLiteral("  ·  ")));
                guarded_output->setText(*explanation);
                guarded_output->show();
            },
            Qt::QueuedConnection);
    });
}

// ── ProAnalyticsDialog ───────────────────────────────────────────────────────

ProAnalyticsDialog::ProAnalyticsDialog(pro::Kind kind, QWidget* parent) : QDialog(parent), kind_(kind) {
    setObjectName(QStringLiteral("proAnalyticsDialog"));
    setWindowTitle(tr("Local analytics — %1").arg(pro::title(kind)));
    setModal(false);
    resize(metrics::width(940), metrics::height(700));
    setStyleSheet(QString("QDialog#proAnalyticsDialog{background:%1;}"
                          "QLabel#padLabel{color:%2;font-size:" + fonts::px(-2) + "px;font-weight:700;"
                          "background:transparent;}"
                          "QDialog#proAnalyticsDialog QLineEdit{background:%3;color:%4;border:1px solid %5;"
                          "padding:3px 6px;font-size:" + fonts::px(0) + "px;}"
                          "QDialog#proAnalyticsDialog QLineEdit:focus{border-color:%6;}"
                          "QFrame#padBar{background:%3;border:none;border-bottom:1px solid %5;}")
                      .arg(colors::BG_BASE(), colors::TEXT_TERTIARY(), colors::BG_SURFACE(), colors::TEXT_PRIMARY(),
                           colors::BORDER_MED(), colors::AMBER()));

    auto* root = new QVBoxLayout(this);
    root->setContentsMargins(0, 0, 0, 0);
    root->setSpacing(0);

    auto* bar = new QFrame(this);
    bar->setObjectName(QStringLiteral("padBar"));
    auto* bl = new QHBoxLayout(bar);
    bl->setContentsMargins(12, 8, 12, 8);
    bl->setSpacing(8);
    auto add_label = [&](const QString& text) {
        auto* l = new QLabel(text, bar);
        l->setObjectName(QStringLiteral("padLabel"));
        bl->addWidget(l);
    };
    if (kind_ == pro::Kind::OptionsPositioning || kind_ == pro::Kind::TechnicalRating) {
        add_label(tr("SYMBOL"));
        symbol_edit_ = new QLineEdit(bar);
        symbol_edit_->setObjectName(QStringLiteral("proAnalyticsSymbol"));
        symbol_edit_->setPlaceholderText(kind_ == pro::Kind::OptionsPositioning ? tr("US stock, ETF or index root — SPY")
                                                                                 : tr("Any symbol — MSFT"));
        symbol_edit_->setMaximumWidth(metrics::width(260));
        symbol_edit_->setAccessibleName(tr("Symbol for the report"));
        bl->addWidget(symbol_edit_);
    } else if (kind_ == pro::Kind::PortfolioRisk) {
        add_label(tr("HOLDINGS"));
        symbol_edit_ = new QLineEdit(bar);
        symbol_edit_->setObjectName(QStringLiteral("proAnalyticsHoldings"));
        symbol_edit_->setPlaceholderText(tr("Comma-separated symbols, up to %1 — AAPL, MSFT, KO").arg(kProAnalyticsMaxHoldings));
        symbol_edit_->setAccessibleName(tr("Holdings for the report, comma-separated"));
        bl->addWidget(symbol_edit_, 3);
        add_label(tr("WEIGHTS"));
        weights_edit_ = new QLineEdit(bar);
        weights_edit_->setObjectName(QStringLiteral("proAnalyticsWeights"));
        weights_edit_->setPlaceholderText(tr("Optional, same order — 50, 30, 20 (blank = equal)"));
        weights_edit_->setAccessibleName(tr("Weights of the holdings, comma-separated, optional"));
        bl->addWidget(weights_edit_, 2);
    } else {
        add_label(tr("MARKET PULSE — ALL ASSET CLASSES"));
    }
    auto* refresh = enterprise::make_cta_button(tr("RUN / REFRESH"), QString(), enterprise::CtaStyle::Outline, bar);
    refresh->setObjectName(QStringLiteral("proAnalyticsRefresh"));
    refresh->setAccessibleName(tr("Run or refresh the report for the inputs above"));
    refresh_btn_ = refresh;
    pro_analytics_mark_spend(refresh_btn_);
    connect(refresh, &QPushButton::clicked, this, [this]() {
        apply_inputs();
        panel_->run();
    });
    bl->addWidget(refresh);
    bl->addStretch(1);
    root->addWidget(bar);

    panel_ = new ProAnalyticsPanel(this);
    root->addWidget(panel_, 1);

    if (symbol_edit_)
        connect(symbol_edit_, &QLineEdit::returnPressed, refresh, &QPushButton::click);
    if (weights_edit_)
        connect(weights_edit_, &QLineEdit::returnPressed, refresh, &QPushButton::click);
}

void ProAnalyticsDialog::set_request(const pro::Request& request) {
    pro::Request r = request;
    r.kind = kind_;
    if (symbol_edit_) {
        if (kind_ == pro::Kind::PortfolioRisk)
            symbol_edit_->setText(r.symbols.join(QStringLiteral(", ")));
        else
            symbol_edit_->setText(r.symbol.trimmed().toUpper());
    }
    if (weights_edit_) {
        QStringList weights;
        if (r.weights.size() == r.symbols.size())
            for (double w : r.weights)
                weights << QString::number(w, 'g', 6);
        weights_edit_->setText(weights.join(QStringLiteral(", ")));
    }
    panel_->set_request(r);
}

void ProAnalyticsDialog::apply_inputs() {
    pro::Request r = panel_->request();
    r.kind = kind_;
    if (kind_ == pro::Kind::PortfolioRisk) {
        r.symbols.clear();
        r.weights.clear();
        for (const QString& part : symbol_edit_->text().split(QLatin1Char(','), Qt::SkipEmptyParts)) {
            const QString s = part.trimmed().toUpper();
            if (!s.isEmpty() && !r.symbols.contains(s))
                r.symbols << s;
        }
        bool ok = true;
        QVector<double> weights;
        for (const QString& part : weights_edit_->text().split(QLatin1Char(','), Qt::SkipEmptyParts)) {
            bool one = false;
            const double w = part.trimmed().toDouble(&one);
            ok = ok && one && w >= 0;
            weights << w;
        }
        if (ok && weights.size() == r.symbols.size())
            r.weights = weights; // otherwise equal weights (the server normalises)
    } else if (symbol_edit_) {
        r.symbol = symbol_edit_->text().trimmed().toUpper();
    }
    panel_->set_request(r, /*autorun=*/false); // RUN / REFRESH runs it right after
}

} // namespace fincept::ui
