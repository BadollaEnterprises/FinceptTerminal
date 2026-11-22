#pragma once

#include <QByteArray>
#include <QString>
#include <QVector>

#include <optional>

namespace fincept::screens {

struct EquitySegmentMember {
    QString id;
    QString label;
    double value = 0.0;
    double share = 0.0;
};

struct EquitySegmentBreakdown {
    QString axis;
    QString label;
    double reported_sum = 0.0;
    double reconciliation_delta = 0.0;
    QVector<EquitySegmentMember> members;
};

struct EquitySegmentPeriod {
    QString start;
    QString end;
    QString unit;
    QString revenue_concept;
    QString total_revenue_concept;
    double total = 0.0;
    QVector<EquitySegmentBreakdown> breakdowns;
};

struct EquitySegmentSource {
    QString name;
    QString form;
    QString filed;
    QString accession;
    QString url;
};

struct EquitySegmentRevenueData {
    QString symbol;
    QString name;
    QString reason;
    QString as_of;
    EquitySegmentSource source;
    QVector<EquitySegmentPeriod> periods;
};

/// Parse the Python extractor's source-attributed JSON envelope. A successful
/// answer with no periods is valid: `reason` explains why no safe breakdown was
/// available. Failed, malformed, or structurally inconsistent envelopes return
/// std::nullopt and put a user-displayable reason in `error` when supplied.
std::optional<EquitySegmentRevenueData> parse_segment_revenue(const QByteArray& json, QString* error = nullptr);

} // namespace fincept::screens
