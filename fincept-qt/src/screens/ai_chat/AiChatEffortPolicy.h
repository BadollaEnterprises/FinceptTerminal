#pragma once

#include <QString>

namespace fincept::screens::aichat {

/// How the selected reasoning effort should be described to the user.
/// ProviderUsage means the user's own API provider may bill more tokens, but
/// Fincept credits are not involved.
enum class EffortUsage { Normal, ProviderUsage, FinceptCredits };

inline EffortUsage effort_usage(const QString& effort, const QString& provider, bool fincept_metered_enabled) {
    const QString level = effort.trimmed().toLower();
    const bool elevated = level == QLatin1String("high") || level == QLatin1String("xhigh") ||
                          level == QLatin1String("max");
    if (!elevated)
        return EffortUsage::Normal;
    if (fincept_metered_enabled && provider.trimmed().compare(QLatin1String("fincept"), Qt::CaseInsensitive) == 0)
        return EffortUsage::FinceptCredits;
    return EffortUsage::ProviderUsage;
}

} // namespace fincept::screens::aichat
