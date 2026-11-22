#include "screens/equity_research/EquitySegmentsTab.h"

#include "python/PythonRunner.h"
#include "screens/equity_research/EquityResearchUi.h"
#include "ui/formatting/NumberFormat.h"
#include "ui/theme/Theme.h"

#include <QAbstractItemView>
#include <QComboBox>
#include <QDesktopServices>
#include <QEvent>
#include <QFrame>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QPointer>
#include <QPushButton>
#include <QScrollArea>
#include <QSignalBlocker>
#include <QTableWidget>
#include <QUrl>
#include <QVBoxLayout>

#include <cmath>

namespace fincept::screens {

namespace {

QString segment_currency(const QString& unit) {
    const QString normalized = unit.trimmed().toUpper();
    return normalized == QLatin1String("USD") ? QStringLiteral("USD") : normalized;
}

QString concept_label(const QString& qname) {
    QString local = qname.section(QLatin1Char(':'), -1);
    QString answer;
    answer.reserve(local.size() + 8);
    for (qsizetype i = 0; i < local.size(); ++i) {
        if (i > 0 && local[i].isUpper() && (local[i - 1].isLower() || local[i - 1].isDigit()))
            answer.append(QLatin1Char(' '));
        answer.append(local[i]);
    }
    return answer;
}

QTableWidgetItem* segment_cell(const QString& text, Qt::Alignment alignment) {
    auto* item = new QTableWidgetItem(text);
    item->setTextAlignment(alignment);
    return item;
}

bool trusted_sec_url(const QUrl& url) {
    const QString host = url.host().toLower();
    return url.isValid() && url.scheme() == QLatin1String("https") &&
           (host == QLatin1String("sec.gov") || host.endsWith(QLatin1String(".sec.gov")));
}

} // namespace

EquitySegmentsTab::EquitySegmentsTab(QWidget* parent) : QWidget(parent) {
    build_ui();
}

void EquitySegmentsTab::set_symbol(const QString& symbol) {
    const QString normalized = symbol.trimmed().toUpper();
    if (normalized == current_symbol_)
        return;
    current_symbol_ = normalized;
    data_ = {};
    loaded_ = false;
    ++request_serial_; // invalidate a response for the previous company
    render();
    request(false);
}

void EquitySegmentsTab::request(bool refresh) {
    if (current_symbol_.isEmpty())
        return;
    const quint64 serial = ++request_serial_;
    const QString symbol = current_symbol_;
    status_->show_loading(tr("Reading %1's annual filing from SEC EDGAR…").arg(symbol));
    refresh_button_->setEnabled(false);

    python::PythonRunner::RunOptions options;
    options.timeout_ms = 45'000;
    options.max_output_bytes = 4 * 1024 * 1024;
    options.stream_stdout = false;
    options.credential_allowlist = QStringList{};
    QPointer<EquitySegmentsTab> self(this);
    QStringList arguments{QStringLiteral("segments"), symbol};
    if (refresh)
        arguments.append(QStringLiteral("--refresh"));
    python::PythonRunner::instance().run_with_options(
        QStringLiteral("Analytics/corporateFinance/valuation/sec_segment_revenue.py"),
        arguments, options,
        [self, serial, symbol](python::PythonResult result) {
            if (!self || serial != self->request_serial_ || symbol != self->current_symbol_)
                return;
            self->refresh_button_->setEnabled(true);
            QString parse_error;
            const QByteArray payload = python::extract_json(result.output).toUtf8();
            const auto parsed = parse_segment_revenue(payload, &parse_error);
            if (!parsed) {
                QString message = payload.trimmed().isEmpty() ? result.error.trimmed() : parse_error;
                if (message.isEmpty())
                    message = parse_error;
                if (message.isEmpty())
                    message = self->tr("SEC revenue segments are unavailable for %1.").arg(symbol);
                self->loaded_ = false;
                self->data_ = {};
                self->render();
                self->status_->show_error(message.left(220), message.left(500));
                return;
            }
            self->data_ = *parsed;
            self->loaded_ = true;
            self->render();
            if (self->data_.periods.isEmpty()) {
                const QString reason = self->data_.reason.isEmpty()
                                           ? self->tr("The filing has no safely reconcilable single-axis revenue breakdown.")
                                           : self->data_.reason;
                self->status_->show_empty(reason);
            } else {
                self->status_->clear();
            }
        });
}

void EquitySegmentsTab::build_ui() {
    setObjectName(QStringLiteral("equitySegmentsTab"));
    er_ui::apply_style(this, []() {
        return QStringLiteral("#equitySegmentsTab, QWidget#segmentsContent { background:%1; }"
                              "QScrollArea#segmentsScroll { background:transparent; border:0; }"
                              "QLabel#segmentsSource { color:%2; }"
                              "QLabel#segmentsSource:hover { color:%3; }")
                   .arg(ui::colors::BG_BASE(), ui::colors::TEXT_SECONDARY(), ui::colors::AMBER()) +
               er_ui::stylesheet();
    });

    auto* outer = new QVBoxLayout(this);
    outer->setContentsMargins(8, 8, 8, 8);
    outer->setSpacing(6);
    status_ = new er_ui::StatusStrip(this);
    connect(status_, &er_ui::StatusStrip::retry_requested, this, [this]() { request(true); });
    outer->addWidget(status_);

    auto* toolbar = new QHBoxLayout;
    period_label_ = new QLabel(this);
    toolbar->addWidget(period_label_);
    period_combo_ = new QComboBox(this);
    period_combo_->setMinimumWidth(150);
    toolbar->addWidget(period_combo_);
    source_label_ = new QLabel(this);
    source_label_->setObjectName(QStringLiteral("segmentsSource"));
    source_label_->setTextFormat(Qt::PlainText);
    source_label_->setCursor(Qt::PointingHandCursor);
    source_label_->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Preferred);
    toolbar->addWidget(source_label_, 1);
    refresh_button_ = new QPushButton(this);
    refresh_button_->setObjectName(QStringLiteral("erBtn"));
    toolbar->addWidget(refresh_button_);
    outer->addLayout(toolbar);

    auto* scroll = new QScrollArea(this);
    scroll->setObjectName(QStringLiteral("segmentsScroll"));
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    auto* content = new QWidget(scroll);
    content->setObjectName(QStringLiteral("segmentsContent"));
    breakdowns_layout_ = new QVBoxLayout(content);
    breakdowns_layout_->setContentsMargins(0, 0, 4, 0);
    breakdowns_layout_->setSpacing(8);
    method_note_ = new QLabel(content);
    method_note_->setObjectName(QStringLiteral("erNote"));
    method_note_->setWordWrap(true);
    breakdowns_layout_->addWidget(method_note_);
    breakdowns_layout_->addStretch(1);
    scroll->setWidget(content);
    outer->addWidget(scroll, 1);

    connect(period_combo_, &QComboBox::currentIndexChanged, this, [this]() { render(); });
    connect(refresh_button_, &QPushButton::clicked, this, [this]() { request(true); });
    source_label_->installEventFilter(this);
    retranslateUi();
    render();
}

void EquitySegmentsTab::clear_breakdowns() {
    // Keep the method note (item 0) and bottom stretch; remove panels between them.
    while (breakdowns_layout_->count() > 2) {
        QLayoutItem* item = breakdowns_layout_->takeAt(1);
        if (item->widget())
            item->widget()->deleteLater();
        delete item;
    }
}

void EquitySegmentsTab::render() {
    clear_breakdowns();
    source_label_->clear();
    source_label_->unsetCursor();
    source_label_->setProperty("sourceUrl", QVariant());
    method_note_->setText(tr("Reported values only. A breakdown appears only when its annual, single-axis inline-XBRL "
                             "members reconcile to the company-reported total within 0.1%; missing or nested values "
                             "are never estimated by AI. Source: SEC EDGAR. Fincept credits: 0."));

    if (!loaded_) {
        const QSignalBlocker block(period_combo_);
        period_combo_->clear();
        period_combo_->setEnabled(false);
        return;
    }

    {
        const QSignalBlocker block(period_combo_);
        const int previous = period_combo_->currentIndex();
        period_combo_->clear();
        for (const auto& period : data_.periods)
            period_combo_->addItem(period.end.isEmpty() ? tr("Annual period") : period.end);
        if (!data_.periods.isEmpty())
            period_combo_->setCurrentIndex(qBound(0, previous, static_cast<int>(data_.periods.size()) - 1));
        period_combo_->setEnabled(!data_.periods.isEmpty());
    }

    QStringList source_parts;
    if (!data_.source.name.isEmpty())
        source_parts << data_.source.name;
    if (!data_.source.form.isEmpty())
        source_parts << data_.source.form;
    if (!data_.source.filed.isEmpty())
        source_parts << tr("filed %1").arg(data_.source.filed);
    source_label_->setText(source_parts.join(QStringLiteral(" · ")));
    const QUrl source_url(data_.source.url);
    if (trusted_sec_url(source_url)) {
        source_label_->setCursor(Qt::PointingHandCursor);
        source_label_->setProperty("sourceUrl", source_url);
        source_label_->setToolTip(tr("Open this filing on SEC EDGAR"));
    } else {
        source_label_->unsetCursor();
        source_label_->setToolTip({});
    }

    const int period_index = period_combo_->currentIndex();
    if (period_index < 0 || period_index >= data_.periods.size())
        return;
    const EquitySegmentPeriod& period = data_.periods[period_index];
    const QString currency = segment_currency(period.unit);
    namespace fmt = ui::formatting;

    for (const auto& breakdown : period.breakdowns) {
        QVBoxLayout* body = nullptr;
        auto* panel = er_ui::panel(breakdown.label.toUpper(), &body, nullptr, this);
        auto* table = new QTableWidget(breakdown.members.size(), 3, panel);
        table->setObjectName(QStringLiteral("erTable"));
        table->setHorizontalHeaderLabels({tr("SEGMENT"), tr("REPORTED REVENUE"), tr("SHARE")});
        table->setEditTriggers(QAbstractItemView::NoEditTriggers);
        table->setSelectionBehavior(QAbstractItemView::SelectRows);
        table->setAlternatingRowColors(true);
        table->verticalHeader()->hide();
        table->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
        table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
        table->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
        for (int row = 0; row < breakdown.members.size(); ++row) {
            const auto& member = breakdown.members[row];
            table->setItem(row, 0, segment_cell(member.label, Qt::AlignLeft | Qt::AlignVCenter));
            table->setItem(row, 1,
                           segment_cell(fmt::money(member.value, currency, fmt::Compact::FromSettings, 2),
                                        Qt::AlignRight | Qt::AlignVCenter));
            table->setItem(row, 2,
                           segment_cell(fmt::fraction_pct(member.share, 2, false),
                                        Qt::AlignRight | Qt::AlignVCenter));
        }
        table->setMinimumHeight(qMin(420, 70 + breakdown.members.size() * 26));
        body->addWidget(table);

        auto* note = new QLabel(panel);
        note->setObjectName(QStringLiteral("erNote"));
        note->setWordWrap(true);
        QString reconciliation = tr("Members sum to %1; reported total is %2.")
                                     .arg(fmt::money(breakdown.reported_sum, currency, fmt::Compact::FromSettings, 2),
                                          fmt::money(period.total, currency, fmt::Compact::FromSettings, 2));
        if (std::abs(breakdown.reconciliation_delta) > 0.5) {
            reconciliation += QLatin1Char(' ') +
                              tr("Filing-level difference: %1 (%2 of total).")
                                  .arg(fmt::signed_money(breakdown.reconciliation_delta, currency,
                                                         fmt::Compact::FromSettings, 2),
                                       fmt::fraction_pct(breakdown.reconciliation_delta / period.total, 3, true));
        }
        if (!period.revenue_concept.isEmpty() && !period.total_revenue_concept.isEmpty() &&
            period.revenue_concept != period.total_revenue_concept) {
            reconciliation += QLatin1Char(' ') +
                              tr("Breakdown concept: %1; total concept: %2.")
                                  .arg(concept_label(period.revenue_concept),
                                       concept_label(period.total_revenue_concept));
        }
        note->setText(reconciliation);
        body->addWidget(note);
        breakdowns_layout_->insertWidget(breakdowns_layout_->count() - 1, panel);
    }
}

void EquitySegmentsTab::retranslateUi() {
    period_label_->setText(tr("FISCAL YEAR"));
    refresh_button_->setText(tr("REFRESH"));
    refresh_button_->setToolTip(tr("Re-read the latest annual filing from SEC EDGAR"));
    if (loaded_)
        render();
}

void EquitySegmentsTab::changeEvent(QEvent* event) {
    if (event->type() == QEvent::LanguageChange)
        retranslateUi();
    QWidget::changeEvent(event);
}

bool EquitySegmentsTab::eventFilter(QObject* watched, QEvent* event) {
    if (watched == source_label_ && event->type() == QEvent::MouseButtonRelease) {
        const QUrl url = source_label_->property("sourceUrl").toUrl();
        if (trusted_sec_url(url)) {
            QDesktopServices::openUrl(url);
            return true;
        }
    }
    return QWidget::eventFilter(watched, event);
}

} // namespace fincept::screens
