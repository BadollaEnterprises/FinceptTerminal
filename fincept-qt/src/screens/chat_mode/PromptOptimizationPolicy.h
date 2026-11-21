#pragma once

#include <QRegularExpression>
#include <QString>

namespace fincept::chat_mode {

/// Build the tool-free BYO-model request behind Chat Mode's OPTIMIZE action.
/// The original prompt is delimited as data so its instructions cannot replace
/// this operation's output contract.
inline QString prompt_optimization_request(const QString& original, const QString& mode) {
    QString guidance = QStringLiteral(
        "Improve clarity, scope, constraints, and the requested output format. Preserve the user's intent and "
        "do not invent facts, tickers, holdings, dates, or preferences.");
    if (mode.trimmed().compare(QLatin1String("deep"), Qt::CaseInsensitive) == 0) {
        guidance += QStringLiteral(
            " Make the research plan rigorous: request evidence, explicit assumptions, material risks, "
            "counterarguments, and sources with dates where available.");
    } else {
        guidance += QStringLiteral(" Keep it concise and immediately actionable.");
    }
    return QStringLiteral(
               "You are a prompt editor for a financial research terminal.\n"
               "%1\n"
               "Do not answer the prompt. Return only the rewritten prompt, with no heading, commentary, quotation "
               "marks, or Markdown fence.\n\n"
               "<original_prompt>\n%2\n</original_prompt>")
        .arg(guidance, original.trimmed());
}

/// Remove harmless wrappers models sometimes add despite the output contract.
inline QString clean_optimized_prompt(QString text) {
    text = text.trimmed();
    if (text.startsWith(QLatin1String("```")) && text.endsWith(QLatin1String("```"))) {
        const qsizetype first_newline = text.indexOf(QLatin1Char('\n'));
        if (first_newline >= 0)
            text = text.mid(first_newline + 1, text.size() - first_newline - 4).trimmed();
    }

    static const QRegularExpression kHeading(
        QStringLiteral("^(?:optimized|rewritten|improved)\\s+prompt\\s*:\\s*"),
        QRegularExpression::CaseInsensitiveOption);
    text.remove(kHeading);
    text = text.trimmed();

    if (text.size() >= 2) {
        const QChar first = text.front();
        const QChar last = text.back();
        const bool paired = (first == QLatin1Char('"') && last == QLatin1Char('"')) ||
                            (first == QChar(0x201c) && last == QChar(0x201d));
        if (paired)
            text = text.mid(1, text.size() - 2).trimmed();
    }
    return text;
}

} // namespace fincept::chat_mode
