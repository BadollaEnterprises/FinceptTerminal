#pragma once

#include <QString>
#include <QVector>

namespace fincept::ai_chat {

struct DeepSeekLiveSmokeCase {
    QString id;
    QString feature;
    QString prompt;
    bool use_tools = false;
};

/// Pure request plan for the explicitly opt-in paid diagnostic. The repeated
/// stable prefix is intentional: subsequent calls exercise provider cache-hit
/// reporting while each answer remains short and deterministic.
inline QVector<DeepSeekLiveSmokeCase> deepseek_live_smoke_plan() {
    const QString stable_prefix =
        QStringLiteral("FINCEPT COMMUNITY LIVE SMOKE. This is a controlled integration check using the user's "
                       "configured DeepSeek API key. Never use Fincept credits. Keep the answer under two sentences. "
                       "Stable cache probe context: portfolio risk, market pulse, options positioning, technical "
                       "ratings, local QuantLib analytics, public market feeds, device-local chat history, provider "
                       "token accounting, and tool-result summaries are all validated by deterministic offline tests. "
                       "This live check only confirms the configured provider route and response wire. ") +
        QString(900, QLatin1Char('S')) + QStringLiteral("\n\n");

    return {
        {QStringLiteral("chat"), QStringLiteral("ai_chat"),
         stable_prefix + QStringLiteral("Reply exactly: DEEPSEEK_CHAT_OK"), false},
        {QStringLiteral("tool"), QStringLiteral("ai_chat"),
         stable_prefix +
             QStringLiteral("Call the tool whose name contains live_deepseek_smoke_probe exactly once, then reply "
                            "exactly: DEEPSEEK_TOOL_OK"),
         true},
        {QStringLiteral("news"), QStringLiteral("news_analysis"),
         stable_prefix +
             QStringLiteral("Analyse this synthetic headline in one sentence: ACME raises guidance after revenue "
                            "grows 12%; identify sentiment and the main risk. End with DEEPSEEK_NEWS_OK."),
         false},
        {QStringLiteral("analytics"), QStringLiteral("pro_analytics_explain"),
         stable_prefix +
             QStringLiteral("Explain these synthetic local analytics in one sentence: volatility 18%, beta 0.9, "
                            "maximum drawdown 7%. End with DEEPSEEK_ANALYTICS_OK."),
         false},
    };
}

/// Runs the paid diagnostic. The caller must enforce the explicit environment
/// opt-in before invoking it. Returns 0 only when every route and the probe tool
/// complete through the configured DeepSeek provider.
int run_deepseek_live_smoke();

} // namespace fincept::ai_chat
