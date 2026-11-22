// src/screens/equity_research/EquityResearchLinkPolicy.h
//
// Pure policy for Equity Research cross-links. Keeping the destination list and
// accepted symbol form in one place prevents a visible menu entry from silently
// routing the wrong ticker representation to another screen.
#pragma once

#include <array>

namespace fincept::screens::equity_links {

enum class OpenInSymbolForm {
    Trade,    ///< broker symbol + exchange (RELIANCE, NSE)
    Bare,     ///< suffix-free search symbol (RELIANCE)
    Research, ///< source/research form (RELIANCE.NS)
};

struct OpenInDestination {
    const char* screen_id;
    OpenInSymbolForm symbol_form;
};

/// High-frequency destinations shown directly on the Overview tab. These are
/// deliberately existing Equity Research sections: opening one only changes
/// tabs and lets the destination's normal lazy-loading policy decide whether it
/// needs data. No extra request is made merely to render the shortcuts.
struct OverviewShortcut {
    const char* section;
    const char* label;
};

inline constexpr std::array<OpenInDestination, 4> open_in_destinations() {
    return {{{"equity_trading", OpenInSymbolForm::Trade},
             {"relationship_map", OpenInSymbolForm::Research},
             {"news", OpenInSymbolForm::Bare},
             {"backtesting", OpenInSymbolForm::Research}}};
}

inline constexpr std::array<OverviewShortcut, 8> overview_shortcuts() {
    return {{{"financials", "FINANCIALS"},
             {"segments", "SEGMENTS"},
             {"filings", "FILINGS"},
             {"news", "NEWS"},
             {"earnings", "EARNINGS"},
             {"options", "OPTIONS"},
             {"ownership", "OWNERSHIP"},
             {"peers", "PEERS"}}};
}

} // namespace fincept::screens::equity_links
