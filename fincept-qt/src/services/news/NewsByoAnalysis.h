#pragma once

#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonParseError>
#include <QString>

#include <optional>

namespace fincept::services::news_byo {

inline QString analysis_prompt(const QString& url, const QString& headline, const QString& text) {
    const QJsonObject input{{QStringLiteral("url"), url},
                            {QStringLiteral("headline"), headline.trimmed()},
                            {QStringLiteral("text"), text.trimmed()}};
    const QString input_json = QString::fromUtf8(QJsonDocument(input).toJson(QJsonDocument::Compact));
    return QStringLiteral(
               "Analyze only the supplied news metadata below. Do not fetch the URL. Do not invent facts that "
               "are absent from the headline or text. Return exactly one JSON object, with no Markdown fences or "
               "commentary, using this shape:\n"
               "{\"analysis\":{\"sentiment\":{\"score\":0,\"intensity\":0,\"confidence\":0},"
               "\"market_impact\":{\"urgency\":\"LOW\",\"prediction\":\"\"},\"summary\":\"\","
               "\"keywords\":[],\"topics\":[],\"key_points\":[],\"risk_signals\":{"
               "\"regulatory\":{\"level\":\"LOW\",\"details\":\"\"},"
               "\"geopolitical\":{\"level\":\"LOW\",\"details\":\"\"},"
               "\"operational\":{\"level\":\"LOW\",\"details\":\"\"},"
               "\"market\":{\"level\":\"LOW\",\"details\":\"\"}},\"entities\":{"
               "\"organizations\":[],\"people\":[],\"locations\":[]}},"
               "\"content\":{\"headline\":\"\",\"word_count\":0,"
               "\"fetch_note\":\"Analyzed from supplied feed text with the user's API provider.\"}}\n"
               "Keep sentiment score in [-1,1], intensity/confidence in [0,1], and risk levels LOW, MEDIUM, or "
               "HIGH. INPUT_JSON: %1")
        .arg(input_json);
}

inline std::optional<QJsonObject> parse_model_data(const QString& response, QString* error = nullptr) {
    if (error)
        error->clear();
    QString json = response.trimmed();
    if (json.startsWith(QStringLiteral("```"))) {
        const int first_newline = json.indexOf(QLatin1Char('\n'));
        if (first_newline >= 0)
            json = json.mid(first_newline + 1);
        if (json.endsWith(QStringLiteral("```")))
            json.chop(3);
        json = json.trimmed();
    }
    const int first = json.indexOf(QLatin1Char('{'));
    const int last = json.lastIndexOf(QLatin1Char('}'));
    if (first < 0 || last < first) {
        if (error)
            *error = QStringLiteral("The configured AI provider did not return JSON.");
        return std::nullopt;
    }
    QJsonParseError parse_error;
    const QJsonDocument doc = QJsonDocument::fromJson(json.mid(first, last - first + 1).toUtf8(), &parse_error);
    if (!doc.isObject()) {
        if (error)
            *error = QStringLiteral("The configured AI provider returned invalid JSON: %1").arg(parse_error.errorString());
        return std::nullopt;
    }
    QJsonObject data = doc.object();
    if (data.value(QStringLiteral("data")).isObject())
        data = data.value(QStringLiteral("data")).toObject();
    if (!data.value(QStringLiteral("analysis")).isObject()) {
        if (error)
            *error = QStringLiteral("The configured AI provider omitted the analysis object.");
        return std::nullopt;
    }
    data.insert(QStringLiteral("credits_used"), 0.0);
    data.insert(QStringLiteral("credits_remaining"), -1.0);
    data.insert(QStringLiteral("analysis_basis"), QStringLiteral("supplied_text"));
    return data;
}

} // namespace fincept::services::news_byo
