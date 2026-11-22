#pragma once

#include "screens/equity_research/EquitySegmentsData.h"

#include <QWidget>

class QComboBox;
class QLabel;
class QPushButton;
class QVBoxLayout;

namespace fincept::screens {

namespace er_ui {
class StatusStrip;
}

/// Auditable business/geographic revenue breakdowns read from a company's
/// latest SEC annual inline-XBRL filing. The Python extractor deliberately
/// returns no rows unless the members reconcile to the reported total.
class EquitySegmentsTab : public QWidget {
    Q_OBJECT
  public:
    explicit EquitySegmentsTab(QWidget* parent = nullptr);
    void set_symbol(const QString& symbol);

  protected:
    void changeEvent(QEvent* event) override;
    bool eventFilter(QObject* watched, QEvent* event) override;

  private:
    void build_ui();
    void retranslateUi();
    void request(bool refresh = false);
    void render();
    void clear_breakdowns();

    QString current_symbol_;
    EquitySegmentRevenueData data_;
    quint64 request_serial_ = 0;
    bool loaded_ = false;

    er_ui::StatusStrip* status_ = nullptr;
    QLabel* period_label_ = nullptr;
    QComboBox* period_combo_ = nullptr;
    QPushButton* refresh_button_ = nullptr;
    QLabel* source_label_ = nullptr;
    QLabel* method_note_ = nullptr;
    QVBoxLayout* breakdowns_layout_ = nullptr;
};

} // namespace fincept::screens
