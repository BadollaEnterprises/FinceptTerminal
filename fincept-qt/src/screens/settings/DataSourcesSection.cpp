// DataSourcesSection.cpp — connection list, stats, and bulk actions panel.

#include "screens/settings/DataSourcesSection.h"

#include "core/events/EventBus.h"
#include "core/logging/Logger.h"
#include "core/metrics/LocalUsageMetrics.h"
#include "screens/settings/SettingsRowHelpers.h"
#include "screens/settings/SettingsStyles.h"
#include "storage/repositories/DataSourceRepository.h"
#include "storage/repositories/LlmConfigRepository.h"
#include "ui/theme/Theme.h"
#include "ui/widgets/EmptyState.h"

#include <QCheckBox>
#include <QFrame>
#include <QHBoxLayout>
#include <QLabel>
#include <QLocale>
#include <QMap>
#include <QMessageBox>
#include <QPushButton>
#include <QScrollArea>
#include <QSet>
#include <QSignalBlocker>
#include <QString>
#include <QVBoxLayout>
#include <QVector>

#include <iterator>

namespace fincept::screens {

namespace {

QString ds_transport_badge(const QString& type) {
    if (type == "websocket")
        return "WS";
    if (type == "rest_api")
        return "REST";
    if (type == "sql")
        return "SQL";
    return type.left(4).toUpper();
}

// Bulk actions used to ignore every per-row failure and report success in the log.
void ds_warn_failures(QWidget* parent, int failed) {
    if (failed > 0)
        QMessageBox::warning(parent, DataSourcesSection::tr("Data Sources"),
                             DataSourcesSection::tr("%1 connection(s) could not be updated — see the log.")
                                 .arg(failed));
}

// Local copy — keeps StorageSection's helper private to that translation unit.
QFrame* make_panel(const QString& title) {
    auto* panel = new QFrame;
    panel->setStyleSheet(
        QString("QFrame{background:%1;border:1px solid %2;}").arg(ui::colors::BG_SURFACE(), ui::colors::BORDER_DIM()));

    auto* pvl = new QVBoxLayout(panel);
    pvl->setContentsMargins(0, 0, 0, 0);
    pvl->setSpacing(0);

    auto* hdr = new QWidget(nullptr);
    hdr->setMinimumHeight(34);
    hdr->setStyleSheet(
        QString("background:%1;border-bottom:1px solid %2;").arg(ui::colors::BG_RAISED(), ui::colors::BORDER_DIM()));
    auto* hhl = new QHBoxLayout(hdr);
    hhl->setContentsMargins(12, 0, 12, 0);
    auto* lbl = new QLabel(title);
    lbl->setStyleSheet(
        QString("color:%1;font-weight:700;letter-spacing:0.5px;background:transparent;").arg(ui::colors::AMBER()));
    hhl->addWidget(lbl);
    hhl->addStretch();
    pvl->addWidget(hdr);

    return panel;
}

} // namespace

DataSourcesSection::DataSourcesSection(QWidget* parent) : QWidget(parent) {
    host_layout_ = new QVBoxLayout(this);
    host_layout_->setContentsMargins(0, 0, 0, 0);
    host_layout_->setSpacing(0);
    // Content is built on first show (showEvent), not here: SettingsScreen
    // constructs every section up front, and this one reads the data-source table.
}

void DataSourcesSection::showEvent(QShowEvent* e) {
    QWidget::showEvent(e);
    rebuild();
}

void DataSourcesSection::rebuild() {
    if (content_) {
        host_layout_->removeWidget(content_);
        content_->deleteLater();
    }
    content_ = build_content();
    host_layout_->addWidget(content_);
}

void DataSourcesSection::changeEvent(QEvent* event) {
    if (event->type() == QEvent::LanguageChange && content_)
        rebuild(); // re-runs every tr() lookup in build_content()
    QWidget::changeEvent(event);
}

QWidget* DataSourcesSection::build_content() {
    using namespace settings_styles;

    auto* scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setStyleSheet(QString("QScrollArea{border:none;background:transparent;}"
                                  "QScrollBar:vertical{width:6px;background:transparent;}"
                                  "QScrollBar::handle:vertical{background:%1;border-radius:3px;}"
                                  "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}")
                              .arg(ui::colors::BORDER_MED()));

    auto* page = new QWidget(this);
    auto* vl = new QVBoxLayout(page);
    vl->setContentsMargins(14, 14, 14, 14);
    vl->setSpacing(10);

    // Title + Open Full Screen button
    auto* title_row = new QWidget(this);
    title_row->setStyleSheet("background:transparent;");
    auto* trl = new QHBoxLayout(title_row);
    trl->setContentsMargins(0, 0, 0, 0);
    auto* t = new QLabel(tr("DATA SOURCES"));
    t->setStyleSheet(section_title_ss());
    trl->addWidget(t);
    trl->addStretch();

    auto* open_full = new QPushButton(tr("OPEN FULL SCREEN"));
    open_full->setMinimumHeight(24);
    open_full->setCursor(Qt::PointingHandCursor);
    open_full->setStyleSheet(
        QString(
            "QPushButton{background:%4;color:%1;border:1px solid %2;font-weight:700;padding:0 10px;}"
            "QPushButton:hover{background:%1;color:%3;}")
            .arg(ui::colors::AMBER(), ui::colors::AMBER_DIM(), ui::colors::TEXT_ON_ACCENT(), ui::colors::ACCENT_BG()));
    connect(open_full, &QPushButton::clicked, this,
            []() { EventBus::instance().publish("nav.switch_screen", {{"screen_id", "data_sources"}}); });
    trl->addWidget(open_full);
    vl->addWidget(title_row);

    auto* info = new QLabel(tr("Quick management of configured connections. "
                               "For full browsing, adding, testing, and import/export use the full screen."));
    info->setWordWrap(true);
    info->setStyleSheet(QString("color:%1;background:transparent;").arg(ui::colors::TEXT_SECONDARY()));
    vl->addWidget(info);
    vl->addSpacing(4);

    // Stats strip
    auto result = DataSourceRepository::instance().list_all();
    auto connections = result.is_ok() ? result.value() : QVector<DataSource>{};

    int total = connections.size();
    int active = 0;
    QSet<QString> providers;
    QMap<QString, int> cat_counts;
    for (const auto& ds : connections) {
        if (ds.enabled)
            ++active;
        providers.insert(ds.provider);
        QString cat = ds.category.isEmpty() ? "other" : ds.category;
        cat_counts[cat]++;
    }

    {
        auto* stat_row = new QWidget(this);
        stat_row->setStyleSheet("background:transparent;");
        auto* shl = new QHBoxLayout(stat_row);
        shl->setContentsMargins(0, 0, 0, 0);
        shl->setSpacing(8);

        auto make_stat = [&](const QString& label, int value, const QString& color) {
            auto* box = new QFrame;
            box->setStyleSheet(QString("QFrame{background:%1;border:1px solid %2;}")
                                   .arg(ui::colors::BG_RAISED(), ui::colors::BORDER_DIM()));
            auto* bx = new QVBoxLayout(box);
            bx->setContentsMargins(10, 8, 10, 10);
            bx->setSpacing(2);
            bx->setAlignment(Qt::AlignCenter);

            auto* val = new QLabel(QString::number(value));
            val->setAlignment(Qt::AlignCenter);
            val->setStyleSheet(QString("color:%1;font-size:" + fincept::ui::fonts::px(4) + "px;font-weight:700;background:transparent;").arg(color));
            bx->addWidget(val);

            auto* ll = new QLabel(label);
            ll->setAlignment(Qt::AlignCenter);
            ll->setStyleSheet(
                QString("color:%1;font-weight:700;background:transparent;").arg(ui::colors::TEXT_SECONDARY()));
            bx->addWidget(ll);

            shl->addWidget(box, 1);
        };

        make_stat(tr("TOTAL"), total, ui::colors::CYAN());
        make_stat(tr("ACTIVE"), active, ui::colors::POSITIVE());
        make_stat(tr("INACTIVE"), total - active, ui::colors::TEXT_SECONDARY());
        make_stat(tr("PROVIDERS"), providers.size(), ui::colors::AMBER());
        vl->addWidget(stat_row);
    }

    vl->addSpacing(6);

    // One visible source-of-truth for what can spend Fincept credits. These are
    // built-in routes, so they remain visible even when the user has not added
    // a custom connection in the table below.
    {
        auto* panel = make_panel(tr("ZERO-CREDIT ROUTES"));
        auto* body = new QWidget(this);
        body->setStyleSheet("background:transparent;");
        auto* routes = new QVBoxLayout(body);
        routes->setContentsMargins(12, 8, 12, 8);
        routes->setSpacing(0);

        struct RouteRow {
            const char* feature;
            const char* source;
            const char* route;
            const char* status;
            bool blocked;
        };
        const RouteRow rows[] = {
            {"AI CHAT · NEWS · REPORTS · ARENA", "DeepSeek (your key)", "BYO AI", "0 FINCEPT CR", false},
            {"QUOTES · HISTORY · OPTIONS", "Yahoo Finance · Stooq · CBOE", "PUBLIC DATA", "0 FINCEPT CR", false},
            {"MACRO · ECONOMICS", "FRED · World Bank · IMF", "PUBLIC DATA", "0 FINCEPT CR", false},
            {"NEWS · GEOPOLITICAL DISCOVERY", "RSS / Atom · GDELT", "PUBLIC DATA", "0 FINCEPT CR", false},
            {"RISK · PRICING · QUANT", "Local analytics · QuantLib (590 routes)", "LOCAL CPU", "0 FINCEPT CR", false},
            {"METERED FINCEPT CALLS", "Market · maritime · macro · geopolitics · cloud AI", "CREDIT FIREWALL", "BLOCKED", true},
        };

        for (int index = 0; index < int(std::size(rows)); ++index) {
            const auto& row = rows[index];
            auto* line = new QWidget(body);
            line->setMinimumHeight(34);
            line->setStyleSheet(QString("background:%1;border-bottom:1px solid %2;")
                                    .arg(index % 2 ? ui::colors::ROW_ALT() : ui::colors::BG_BASE(),
                                         ui::colors::BORDER_DIM()));
            auto* hl = new QHBoxLayout(line);
            hl->setContentsMargins(8, 0, 8, 0);
            hl->setSpacing(10);
            auto label = [&](const QString& text, int stretch, const QString& color, bool bold = false) {
                auto* value = new QLabel(text, line);
                value->setWordWrap(true);
                value->setStyleSheet(QString("color:%1;background:transparent;%2")
                                         .arg(color, bold ? QStringLiteral("font-weight:700;") : QString()));
                hl->addWidget(value, stretch);
            };
            label(tr(row.feature), 3, ui::colors::TEXT_PRIMARY(), true);
            label(tr(row.source), 4, ui::colors::TEXT_SECONDARY());
            label(tr(row.route), 2, ui::colors::CYAN(), true);
            label(tr(row.status), 2, row.blocked ? ui::colors::NEGATIVE() : ui::colors::POSITIVE(), true);
            routes->addWidget(line);
        }

        auto* note = new QLabel(
            tr("Provider charges may still apply to your own DeepSeek account. The community credit firewall only "
               "guarantees that these routes cannot consume Fincept credits."),
            body);
        note->setWordWrap(true);
        note->setStyleSheet(QString("color:%1;background:transparent;padding-top:6px;")
                                .arg(ui::colors::TEXT_TERTIARY()));
        routes->addWidget(note);
        static_cast<QVBoxLayout*>(panel->layout())->addWidget(body);
        vl->addWidget(panel);
        vl->addSpacing(6);
    }

    {
        const auto health_usage = metrics::LocalUsageMetrics::snapshot();
        auto* panel = make_panel(tr("PROVIDER HEALTH"));
        auto* body = new QWidget(this);
        auto* rows = new QVBoxLayout(body);
        rows->setContentsMargins(12, 8, 12, 8);
        rows->setSpacing(4);

        QString ai_status = tr("AVAILABLE");
        const auto active_provider = LlmConfigRepository::instance().get_active_provider();
        if (active_provider.is_ok() && active_provider.value().provider.compare(
                                           QStringLiteral("deepseek"), Qt::CaseInsensitive) == 0)
            ai_status = active_provider.value().api_key.isEmpty() ? tr("ACTIVE · KEY REQUIRED") : tr("ACTIVE");

        auto add_health = [&](const QString& source, const QString& path, const QString& status,
                              const QString& color) {
            auto* row = new QWidget(body);
            auto* layout = new QHBoxLayout(row);
            layout->setContentsMargins(8, 3, 8, 3);
            auto* name = new QLabel(source, row);
            name->setStyleSheet(QString("color:%1;font-weight:700;").arg(ui::colors::TEXT_PRIMARY()));
            layout->addWidget(name, 2);
            auto* route = new QLabel(path, row);
            route->setStyleSheet(QString("color:%1;").arg(ui::colors::TEXT_SECONDARY()));
            layout->addWidget(route, 4);
            auto* badge = new QLabel(status, row);
            badge->setAlignment(Qt::AlignRight | Qt::AlignVCenter);
            badge->setStyleSheet(QString("color:%1;font-weight:700;").arg(color));
            layout->addWidget(badge, 2);
            rows->addWidget(row);
        };

        add_health(tr("DeepSeek"), tr("Own API key · no Fincept fallback"), ai_status, ui::colors::POSITIVE());
        add_health(tr("Market data"), tr("Stooq → Yahoo Finance"), tr("AUTO FALLBACK"), ui::colors::CYAN());
        add_health(tr("Macro data"), tr("World Bank · IMF · FRED"), tr("ADAPTER READY"), ui::colors::POSITIVE());
        add_health(tr("News discovery"), tr("GDELT → RSS / Atom"), tr("AUTO FALLBACK"), ui::colors::CYAN());
        add_health(tr("Fincept metered services"), tr("All paid routes"), tr("FIREWALL ON"),
                   ui::colors::NEGATIVE());

        const QString deepseek_last = health_usage.provider_last_success.value(QStringLiteral("deepseek"));
        const QString public_last = health_usage.source_last_success.isEmpty()
                                        ? QString()
                                        : health_usage.source_last_success.constBegin().value();
        auto* last = new QLabel(
            tr("LAST SUCCESS · DeepSeek: %1 · Public/local: %2")
                .arg(deepseek_last.isEmpty() ? tr("not recorded") : deepseek_last,
                     public_last.isEmpty() ? tr("not recorded") : public_last),
            body);
        last->setWordWrap(true);
        last->setStyleSheet(QString("color:%1;padding:4px 8px;").arg(ui::colors::TEXT_TERTIARY()));
        rows->addWidget(last);

        static_cast<QVBoxLayout*>(panel->layout())->addWidget(body);
        vl->addWidget(panel);
        vl->addSpacing(6);
    }

    {
        const auto usage = metrics::LocalUsageMetrics::snapshot();
        auto* panel = make_panel(tr("PERSISTENT USAGE"));
        auto* body = new QWidget(this);
        auto* layout = new QHBoxLayout(body);
        layout->setContentsMargins(12, 10, 12, 10);
        layout->setSpacing(18);
        auto add_metric = [&](const QString& label, qint64 value, const QString& color) {
            auto* text = new QLabel(tr("%1\n%L2").arg(label).arg(value), body);
            text->setAlignment(Qt::AlignCenter);
            text->setStyleSheet(QString("color:%1;font-weight:700;").arg(color));
            layout->addWidget(text, 1);
        };
        add_metric(tr("BYO AI CALLS"), usage.llm_requests, ui::colors::CYAN());
        add_metric(tr("DEEPSEEK TOKENS"), usage.provider_tokens.value(QStringLiteral("deepseek")),
                   ui::colors::AMBER());
        add_metric(tr("CACHED INPUT"), usage.cached_prompt_tokens, ui::colors::CYAN());
        add_metric(tr("LOCAL QUANTLIB"), usage.local_quantlib_calls, ui::colors::POSITIVE());
        add_metric(tr("PUBLIC DATA"), usage.public_data_calls, ui::colors::POSITIVE());
        add_metric(tr("FINCEPT CREDITS"), usage.fincept_credits_used, ui::colors::POSITIVE());
        static_cast<QVBoxLayout*>(panel->layout())->addWidget(body);
        vl->addWidget(panel);
        vl->addSpacing(6);
    }

    {
        const auto usage = metrics::LocalUsageMetrics::snapshot();
        const qint64 avoided = usage.monthly_successful_llm_requests + usage.monthly_local_quantlib_calls +
                               usage.monthly_public_data_calls;
        auto* panel = make_panel(tr("MONTHLY SAVINGS"));
        auto* body = new QWidget(this);
        auto* layout = new QHBoxLayout(body);
        layout->setContentsMargins(12, 10, 12, 10);
        auto add = [&](const QString& title, const QString& value, const QString& color) {
            auto* label = new QLabel(title + QLatin1Char('\n') + value, body);
            label->setAlignment(Qt::AlignCenter);
            label->setStyleSheet(QString("color:%1;font-weight:700;").arg(color));
            layout->addWidget(label, 1);
        };
        add(tr("PERIOD"), usage.monthly_period, ui::colors::TEXT_SECONDARY());
        add(tr("METERED CALLS AVOIDED"), QLocale().toString(avoided), ui::colors::POSITIVE());
        add(tr("DEEPSEEK TOKENS"),
            QLocale().toString(usage.monthly_provider_tokens.value(QStringLiteral("deepseek"))), ui::colors::AMBER());
        add(tr("CACHED INPUT"), QLocale().toString(usage.monthly_cached_prompt_tokens), ui::colors::CYAN());
        add(tr("FINCEPT CREDITS USED"), QStringLiteral("0"), ui::colors::POSITIVE());
        static_cast<QVBoxLayout*>(panel->layout())->addWidget(body);
        vl->addWidget(panel);
        vl->addSpacing(6);
    }

    // Connections table
    if (connections.isEmpty()) {
        // Genuinely nothing here: the shared designed state, with the action that fills it.
        auto* empty_panel = make_panel(tr("CONNECTIONS"));
        auto* es = new ui::EmptyState(empty_panel);
        es->set_compact(true);
        es->set_content(QStringLiteral("⇄"), tr("No data sources connected yet"),
                        tr("Connect databases, brokers, market-data APIs and files; they are listed here with their "
                           "status and can be switched on and off in bulk."),
                        tr("BROWSE CONNECTORS"));
        connect(es, &ui::EmptyState::primary_clicked, this,
                []() { EventBus::instance().publish("nav.switch_screen", {{"screen_id", "data_sources"}}); });
        static_cast<QVBoxLayout*>(empty_panel->layout())->addWidget(es);
        vl->addWidget(empty_panel);
    } else {
        QMap<QString, QVector<DataSource>> grouped;
        for (const auto& ds : connections) {
            QString cat = ds.category.isEmpty() ? "other" : ds.category;
            grouped[cat].append(ds);
        }

        auto* panel = make_panel(tr("CONNECTIONS"));
        auto* body = new QWidget(this);
        body->setStyleSheet("background:transparent;");
        auto* bvl = new QVBoxLayout(body);
        bvl->setContentsMargins(0, 0, 0, 0);
        bvl->setSpacing(0);

        // Table header
        // Column widths are for the default font size and grow with the user's.
        const auto col_w = [](int w) {
            return qMax(w, w * ui::fonts::font_px(0) / ui::ThemeManager::kDefaultFontPx);
        };

        auto* th = new QWidget(this);
        th->setMinimumHeight(26);
        th->setStyleSheet(QString("background:%1;border-bottom:1px solid %2;")
                              .arg(ui::colors::BG_RAISED(), ui::colors::BORDER_DIM()));
        auto* thl = new QHBoxLayout(th);
        thl->setContentsMargins(12, 0, 12, 0);
        thl->setSpacing(6);

        auto add_th = [&](const QString& text, int width = 0) {
            auto* lbl = new QLabel(text);
            lbl->setStyleSheet(
                QString("color:%1;font-weight:700;background:transparent;").arg(ui::colors::TEXT_SECONDARY()));
            if (width > 0) {
                lbl->setFixedWidth(col_w(width));
                lbl->setAlignment(Qt::AlignCenter);
            }
            thl->addWidget(lbl, width > 0 ? 0 : 1);
        };
        add_th(tr("SOURCE"));
        add_th(tr("TYPE"), 44);
        add_th(tr("CATEGORY"), 80);
        add_th(tr("ON"), 30);
        add_th(tr("DEL"), 30);
        bvl->addWidget(th);

        int row_idx = 0;
        for (auto it = grouped.constBegin(); it != grouped.constEnd(); ++it) {
            auto* grp = new QWidget(this);
            grp->setMinimumHeight(24);
            grp->setStyleSheet(QString("background:%1;border-bottom:1px solid %2;")
                                   .arg(ui::colors::BG_RAISED(), ui::colors::BORDER_DIM()));
            auto* ghl = new QHBoxLayout(grp);
            ghl->setContentsMargins(12, 0, 12, 0);
            auto* glbl = new QLabel(it.key().toUpper() + QString("  (%1)").arg(it.value().size()));
            glbl->setStyleSheet(QString("color:%1;font-weight:700;letter-spacing:0.5px;background:transparent;")
                                    .arg(ui::colors::AMBER_DIM()));
            ghl->addWidget(glbl);
            bvl->addWidget(grp);

            for (const auto& ds : it.value()) {
                auto* row = new QWidget(this);
                row->setMinimumHeight(26);
                row->setStyleSheet(
                    QString("background:%1;border-bottom:1px solid %2;")
                        .arg((row_idx % 2) ? ui::colors::ROW_ALT() : ui::colors::BG_BASE(), ui::colors::BORDER_DIM()));

                auto* rhl = new QHBoxLayout(row);
                rhl->setContentsMargins(12, 0, 12, 0);
                rhl->setSpacing(6);

                QString name = ds.display_name.isEmpty() ? ds.alias : ds.display_name;
                auto* name_lbl = new QLabel(name);
                name_lbl->setStyleSheet(
                    QString("color:%1;background:transparent;")
                        .arg(ds.enabled ? ui::colors::TEXT_PRIMARY() : ui::colors::TEXT_TERTIARY()));
                rhl->addWidget(name_lbl, 1);

                auto* badge = new QLabel(ds_transport_badge(ds.type));
                badge->setFixedWidth(col_w(44));
                badge->setAlignment(Qt::AlignCenter);
                badge->setStyleSheet(
                    QString("color:%1;font-weight:700;background:%2;border:1px solid %3;")
                        .arg(ui::colors::TEXT_SECONDARY(), ui::colors::BG_RAISED(), ui::colors::BORDER_DIM()));
                rhl->addWidget(badge);

                auto* cat_lbl = new QLabel(ds.category.isEmpty() ? "—" : ds.category);
                cat_lbl->setFixedWidth(col_w(80));
                cat_lbl->setAlignment(Qt::AlignCenter);
                cat_lbl->setStyleSheet(QString("color:%1;background:transparent;").arg(ui::colors::TEXT_TERTIARY()));
                rhl->addWidget(cat_lbl);

                auto* toggle = new QCheckBox;
                toggle->setChecked(ds.enabled);
                toggle->setFixedWidth(col_w(30));
                toggle->setStyleSheet(check_ss());
                QString source_id = ds.id;
                connect(toggle, &QCheckBox::toggled, this, [source_id, name_lbl, toggle](bool checked) {
                    const auto saved = DataSourceRepository::instance().set_enabled(source_id, checked);
                    if (saved.is_err()) {
                        // Don't leave the checkbox claiming a state that was not stored.
                        LOG_ERROR("DataSources", "Could not update " + source_id + ": " +
                                                     QString::fromStdString(saved.error()));
                        const QSignalBlocker revert(toggle);
                        toggle->setChecked(!checked);
                        return;
                    }
                    name_lbl->setStyleSheet(
                        QString("color:%1;background:transparent;")
                            .arg(checked ? ui::colors::TEXT_PRIMARY() : ui::colors::TEXT_TERTIARY()));
                    LOG_INFO("DataSources", (checked ? "Enabled: " : "Disabled: ") + source_id);
                });
                rhl->addWidget(toggle);

                auto* del_btn = new QPushButton("X");
                del_btn->setFixedSize(col_w(30) - 8, qMax(18, ui::fonts::font_px(0) + 4));
                del_btn->setCursor(Qt::PointingHandCursor);
                del_btn->setStyleSheet(
                    QString("QPushButton{background:transparent;color:%1;border:none;font-weight:700;}"
                            "QPushButton:hover{color:%2;}")
                        .arg(ui::colors::ICON_DIM(), ui::colors::NEGATIVE()));
                connect(del_btn, &QPushButton::clicked, this, [this, source_id, name]() {
                    auto answer =
                        QMessageBox::warning(this, tr("Delete Connection"),
                                             tr("Delete connection \"%1\"?\n\nThis cannot be undone.").arg(name),
                                             QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel);
                    if (answer != QMessageBox::Yes)
                        return;

                    const auto removed = DataSourceRepository::instance().remove(source_id);
                    if (removed.is_err()) {
                        // The row used to be hidden whether or not the delete worked.
                        LOG_ERROR("DataSources", "Could not delete " + source_id + ": " +
                                                     QString::fromStdString(removed.error()));
                        QMessageBox::critical(this, tr("Delete Connection"),
                                              tr("Could not delete \"%1\":\n%2")
                                                  .arg(name, QString::fromStdString(removed.error())));
                        return;
                    }
                    LOG_INFO("DataSources", "Deleted: " + source_id);
                    // Rebuild instead of hiding the row so the stat strip and the
                    // per-category counts reflect the deletion too.
                    rebuild();
                });
                rhl->addWidget(del_btn);

                bvl->addWidget(row);
                ++row_idx;
            }
        }

        static_cast<QVBoxLayout*>(panel->layout())->addWidget(body);
        vl->addWidget(panel);
    }

    vl->addSpacing(10);

    // Bulk actions
    {
        auto* panel = make_panel(tr("BULK ACTIONS"));
        auto* body = new QWidget(this);
        body->setStyleSheet("background:transparent;");
        auto* bvl = new QVBoxLayout(body);
        bvl->setContentsMargins(10, 8, 10, 8);
        bvl->setSpacing(6);

        auto* btn_row = new QWidget(this);
        btn_row->setStyleSheet("background:transparent;");
        auto* brhl = new QHBoxLayout(btn_row);
        brhl->setContentsMargins(0, 0, 0, 0);
        brhl->setSpacing(8);

        auto* enable_all = new QPushButton(tr("ENABLE ALL"));
        enable_all->setMinimumHeight(24);
        enable_all->setStyleSheet(QString("QPushButton{background:%3;color:%1;border:1px solid "
                                          "%1;font-weight:700;padding:0 10px;}"
                                          "QPushButton:hover{background:%1;color:%2;}")
                                      .arg(ui::colors::POSITIVE(), ui::colors::TEXT_ON_ACCENT(), ui::colors::POSITIVE_BG()));
        connect(enable_all, &QPushButton::clicked, this, [this]() {
            auto r = DataSourceRepository::instance().list_all();
            if (!r.is_ok())
                return;
            int failed = 0;
            for (const auto& ds : r.value()) {
                if (DataSourceRepository::instance().set_enabled(ds.id, true).is_err())
                    ++failed;
            }
            LOG_INFO("DataSources", "All sources enabled");
            rebuild();
            ds_warn_failures(this, failed);
        });
        brhl->addWidget(enable_all);

        auto* disable_all = new QPushButton(tr("DISABLE ALL"));
        disable_all->setMinimumHeight(24);
        disable_all->setStyleSheet(
            QString("QPushButton{background:%4;color:%1;border:1px solid %3;font-weight:700;padding:0 "
                    "10px;}"
                    "QPushButton:hover{background:%1;color:%2;}")
                .arg(ui::colors::NEGATIVE(), ui::colors::TEXT_ON_ACCENT(), ui::colors::NEGATIVE_DIM(),
                     ui::colors::NEGATIVE_BG()));
        connect(disable_all, &QPushButton::clicked, this, [this]() {
            auto answer = QMessageBox::warning(this, tr("Disable All"), tr("Disable all data source connections?"),
                                               QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel);
            if (answer != QMessageBox::Yes)
                return;
            auto r = DataSourceRepository::instance().list_all();
            if (!r.is_ok())
                return;
            int failed = 0;
            for (const auto& ds : r.value()) {
                if (DataSourceRepository::instance().set_enabled(ds.id, false).is_err())
                    ++failed;
            }
            LOG_INFO("DataSources", "All sources disabled");
            rebuild();
            ds_warn_failures(this, failed);
        });
        brhl->addWidget(disable_all);

        auto* delete_all = new QPushButton(tr("DELETE ALL"));
        delete_all->setProperty("fincept_agent_risk", "delete"); // agents: deletes every data source
        delete_all->setMinimumHeight(24);
        delete_all->setStyleSheet(
            QString("QPushButton{background:%1;color:%2;border:2px solid %2;font-weight:700;padding:0 10px;}"
                    "QPushButton:hover{background:%2;color:%3;}")
                .arg(ui::colors::BG_RAISED(), ui::colors::NEGATIVE(), ui::colors::TEXT_ON_ACCENT()));
        connect(delete_all, &QPushButton::clicked, this, [this]() {
            auto answer = QMessageBox::critical(
                this, tr("Delete All Connections"),
                tr("Permanently delete ALL data source connections?\n\n"
                   "This cannot be undone. You can re-add them from the full Data Sources screen."),
                QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Cancel);
            if (answer != QMessageBox::Yes)
                return;

            auto r = DataSourceRepository::instance().list_all();
            if (!r.is_ok())
                return;
            int failed = 0;
            for (const auto& ds : r.value()) {
                if (DataSourceRepository::instance().remove(ds.id).is_err())
                    ++failed;
            }
            LOG_INFO("DataSources", "All connections deleted");
            rebuild();
            ds_warn_failures(this, failed);
        });
        brhl->addWidget(delete_all);

        brhl->addStretch();
        bvl->addWidget(btn_row);

        auto* note = new QLabel(tr("For adding new connections, testing connectivity, and import/export, "
                                   "use the full Data Sources screen."));
        note->setWordWrap(true);
        note->setStyleSheet(QString("color:%1;background:transparent;").arg(ui::colors::TEXT_DIM()));
        bvl->addWidget(note);

        static_cast<QVBoxLayout*>(panel->layout())->addWidget(body);
        vl->addWidget(panel);
    }

    vl->addStretch();
    scroll->setWidget(page);
    return scroll;
}

} // namespace fincept::screens
