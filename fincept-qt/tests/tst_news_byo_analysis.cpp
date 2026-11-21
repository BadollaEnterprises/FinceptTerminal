#include "services/news/NewsByoAnalysis.h"

#include <QCoreApplication>
#include <QJsonObject>

#include <cstdlib>
#include <iostream>

namespace {

void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

} // namespace

int main(int argc, char** argv) {
    QCoreApplication app(argc, argv);
    using namespace fincept::services::news_byo;

    const QString prompt = analysis_prompt(QStringLiteral("https://example.com/story"),
                                           QStringLiteral("Acme raises guidance"),
                                           QStringLiteral("Revenue grew 20 percent."));
    require(prompt.contains(QStringLiteral("Return exactly one JSON object")), "prompt must demand strict JSON");
    require(prompt.contains(QStringLiteral("Acme raises guidance")), "prompt must include the headline");
    require(prompt.contains(QStringLiteral("Do not invent")), "prompt must forbid invented article facts");

    QString error;
    const auto data = parse_model_data(
        QStringLiteral("```json\n{\"analysis\":{\"summary\":\"Guidance increased\"},"
                       "\"content\":{\"headline\":\"Acme raises guidance\"}}\n```"),
        &error);
    require(data.has_value(), "fenced model JSON must parse");
    require(error.isEmpty(), "valid model JSON must not report an error");
    require(data->value(QStringLiteral("credits_used")).toDouble(-1) == 0.0,
            "BYO analysis must record zero Fincept credits");
    require(data->value(QStringLiteral("analysis_basis")).toString() == QStringLiteral("supplied_text"),
            "BYO analysis must disclose its input basis");

    const auto invalid = parse_model_data(QStringLiteral("not json"), &error);
    require(!invalid.has_value() && !error.isEmpty(), "invalid model output must fail visibly");
    return 0;
}
