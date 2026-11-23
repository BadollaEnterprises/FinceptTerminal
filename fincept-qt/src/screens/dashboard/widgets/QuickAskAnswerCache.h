#pragma once
// Route-bound key and acceptance policy for Dashboard Quick Ask's local
// answer cache. The cache stores answer text only; API keys and raw questions
// never appear in SQLite keys.

#include <QString>

namespace fincept::screens::widgets {

inline constexpr int kQuickAskAnswerCacheTtlSeconds = 30 * 24 * 60 * 60;
inline constexpr const char* kQuickAskAnswerCacheCategory = "ai_answers";

QString quick_ask_answer_cache_key(const QString& question, const QString& provider,
                                   const QString& model, int prompt_version = 1,
                                   const QString& route_identity = {});

bool quick_ask_response_is_cacheable(bool success, bool cancelled, const QString& answer,
                                     const QString& fallback_notice);

} // namespace fincept::screens::widgets
