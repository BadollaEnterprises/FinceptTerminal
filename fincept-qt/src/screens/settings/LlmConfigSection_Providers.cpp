// src/screens/settings/LlmConfigSection_Providers.cpp
//
// Provider-tab implementation: build_providers_tab, the list/form/global
// panels, load_providers, populate_form, and all on_* slots for save / delete
// / test_connection / fetch_models.
//
// Part of the partial-class split of LlmConfigSection.cpp.

#include "auth/AuthManager.h"
#include "core/config/AppConfig.h"
#include "core/logging/Logger.h"
#include "core/policy/CommunityPolicy.h"
#include "core/privacy/LogRedact.h"
#include "screens/settings/LlmConfigSection.h"
#include "services/llm/LlmBudgetSettings.h"
#include "services/llm/LlmService.h"
#include "services/llm/ModelCatalog.h"
#include "storage/repositories/LlmConfigRepository.h"
#include "storage/repositories/LlmProfileRepository.h"
#include "storage/repositories/SettingsRepository.h"
#include "ui/theme/Theme.h"
#include "ui/theme/ThemeManager.h"
#include "ui/widgets/MnemonicText.h"

#include <QHash>

// Unity-build hygiene: this split TU shares an unstable unity bucket with
// LlmConfigSection.cpp (static TAG) and LlmConfigSection_Profiles.cpp
// (another anonymous-namespace TAG). Unique identifier prevents ambiguous
// lookup if all three land in one TU. Same convention applied across
// AiChatScreen_Sessions.cpp after a real-world collision surfaced.
namespace {
constexpr const char* TAG_PROVIDERS = "LlmConfigSection";
// Global Max Tokens ceiling for providers that publish a higher cap. Fincept's
// own API ceiling is lower (ModelCatalog::kFinceptMaxOutputTokens).
constexpr int kLlmProvidersTokensMax = 32000;

bool provider_is_visible_in_community(const QString& provider) {
    if (!fincept::policy::is_ai_backend_selectable(provider))
        return false;
    return provider.compare(QLatin1String("fincept"), Qt::CaseInsensitive) != 0 ||
           fincept::policy::fincept_metered_services_enabled();
}
} // namespace

#include <QFormLayout>
#include <QFrame>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QMessageBox>
#include <QScrollArea>
#include <QSplitter>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>

#include <memory>

namespace fincept::screens {

QWidget* LlmConfigSection::build_providers_tab() {
    auto* w = new QWidget(this);
    auto* vl = new QVBoxLayout(w);
    vl->setContentsMargins(0, 0, 0, 0);
    vl->setSpacing(0);

    auto* splitter = new QSplitter(Qt::Horizontal);
    splitter->setHandleWidth(1);
    splitter->addWidget(build_provider_list_panel());
    splitter->addWidget(build_form_panel());
    splitter->setStretchFactor(0, 0);
    splitter->setStretchFactor(1, 1);
    splitter->setSizes({220, 600});
    vl->addWidget(splitter, 1);

    auto* sep = new QFrame;
    sep->setFixedHeight(1);
    sep->setStyleSheet("background:" + QString(ui::colors::BORDER_MED()) + ";");
    vl->addWidget(sep);
    vl->addWidget(build_global_panel());
    return w;
}

QWidget* LlmConfigSection::build_provider_list_panel() {
    auto* panel = new QWidget(this);
    panel->setFixedWidth(220);
    panel->setStyleSheet("background:" + QString(ui::colors::BG_BASE()) + ";border-right:1px solid " +
                         QString(ui::colors::BORDER_DIM()) + ";");

    auto* vl = new QVBoxLayout(panel);
    vl->setContentsMargins(8, 8, 8, 8);
    vl->setSpacing(6);

    provider_list_title_ = new QLabel(tr("Providers"));
    provider_list_title_->setStyleSheet("color:" + QString(ui::colors::TEXT_SECONDARY()) +
                                        ";font-weight:700;letter-spacing:1px;");
    vl->addWidget(provider_list_title_);

    provider_list_ = new QListWidget;
    provider_list_->setStyleSheet(
        "QListWidget{background:transparent;border:none;color:" + QString(ui::colors::TEXT_PRIMARY()) +
        ";}"
        "QListWidget::item{padding:8px 6px;border-radius:3px;}"
        "QListWidget::item:selected{background:" +
        QString(ui::colors::BG_RAISED()) + ";color:" + QString(ui::colors::AMBER()) +
        ";}"
        "QListWidget::item:hover{background:" +
        QString(ui::colors::BG_RAISED()) + ";}");
    connect(provider_list_, &QListWidget::currentRowChanged, this, &LlmConfigSection::on_provider_selected);
    vl->addWidget(provider_list_, 1);

    auto* btn_row = new QHBoxLayout;
    add_btn_ = new QPushButton(tr("+ Add"));
    add_btn_->setStyleSheet("QPushButton{background:" + QString(ui::colors::BG_RAISED()) + ";color:" +
                            QString(ui::colors::AMBER()) + ";border:1px solid " + QString(ui::colors::AMBER()) +
                            ";"
                            "border-radius:3px;padding:5px 10px;font-weight:600;}"
                            "QPushButton:hover{background:" +
                            QString(ui::colors::BG_RAISED()) + ";}");
    connect(add_btn_, &QPushButton::clicked, this, [this]() {
        // Show input dialog to pick provider — display formatted names in
        // alphabetical order, then map the choice back to its provider id.
        QStringList choices;
        QHash<QString, QString> display_to_id;
        for (const auto& id : providers_sorted()) {
            const QString disp = provider_display_name(id);
            choices << disp;
            display_to_id.insert(disp, id);
        }
        bool ok;
        QString chosen =
            QInputDialog::getItem(this, tr("Add Provider"), tr("Select provider:"), choices, 0, false, &ok);
        if (!ok || chosen.isEmpty())
            return;
        QString provider = display_to_id.value(chosen, chosen);

        // Check if already exists
        auto existing = LlmConfigRepository::instance().list_providers();
        if (existing.is_err()) {
            const QString error = QString::fromStdString(existing.error());
            show_status(tr("Could not read provider settings: ") + error, true);
            LOG_ERROR(TAG_PROVIDERS, "list_providers failed while adding: " + privacy::redact_text(error));
            return;
        }
        for (const auto& p : existing.value()) {
            if (p.provider.toLower() == provider.toLower()) {
                show_status(tr("Provider already configured"), true);
                return;
            }
        }

        // Create new empty config
        LlmConfig cfg;
        cfg.provider = provider.toLower();
        cfg.model = "";
        cfg.is_active = false;
        auto saved = LlmConfigRepository::instance().save_provider(cfg);
        if (saved.is_err()) {
            const QString error = QString::fromStdString(saved.error());
            show_status(tr("Could not add provider: ") + error, true);
            LOG_ERROR(TAG_PROVIDERS,
                      "save_provider failed while adding " + provider + ": " + privacy::redact_text(error));
            return;
        }
        load_providers();

        // Select newly added provider
        for (int i = 0; i < provider_list_->count(); ++i) {
            if (provider_list_->item(i)->data(Qt::UserRole).toString() == cfg.provider) {
                provider_list_->setCurrentRow(i);
                break;
            }
        }
    });

    delete_btn_ = new QPushButton(tr("Remove"));
    delete_btn_->setProperty("fincept_agent_risk", "account"); // agents: removes a provider and its stored API key
    delete_btn_->setEnabled(false);
    delete_btn_->setStyleSheet(
        "QPushButton{background:" + QString(ui::colors::BG_RAISED()) + ";color:" + QString(ui::colors::NEGATIVE()) +
        ";border:1px solid " + QString(ui::colors::BG_RAISED()) +
        ";"
        "border-radius:3px;padding:5px 10px;}"
        "QPushButton:hover{background:" +
        QString(ui::colors::BG_RAISED()) +
        ";}"
        "QPushButton:disabled{color:" +
        QString(ui::colors::TEXT_TERTIARY()) + ";border-color:" + QString(ui::colors::BORDER_MED()) + ";}");
    connect(delete_btn_, &QPushButton::clicked, this, &LlmConfigSection::on_delete_provider);

    btn_row->addWidget(add_btn_);
    btn_row->addWidget(delete_btn_);
    vl->addLayout(btn_row);

    return panel;
}

QWidget* LlmConfigSection::build_form_panel() {
    auto* scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    scroll->setFrameShape(QFrame::NoFrame);
    scroll->setStyleSheet("QScrollArea{background:" + QString(ui::colors::BG_BASE()) +
                          ";border:none;}"
                          "QScrollBar:vertical{background:" +
                          QString(ui::colors::BG_RAISED()) +
                          ";width:6px;}"
                          "QScrollBar::handle:vertical{background:" +
                          QString(ui::colors::BORDER_BRIGHT()) +
                          ";border-radius:3px;}"
                          "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}");

    auto* page = new QWidget(this);
    page->setStyleSheet("background:" + QString(ui::colors::BG_BASE()) + ";");
    auto* vl = new QVBoxLayout(page);
    vl->setContentsMargins(24, 20, 24, 20);
    vl->setSpacing(14);

    // Section title
    form_title_ = new QLabel(tr("Provider Configuration"));
    form_title_->setStyleSheet("color:" + QString(ui::colors::AMBER()) + ";font-weight:700;");
    vl->addWidget(form_title_);

    auto* sep = new QFrame;
    sep->setFixedHeight(1);
    sep->setStyleSheet("background:" + QString(ui::colors::BORDER_DIM()) + ";");
    vl->addWidget(sep);

    auto* form = new QFormLayout;
    form->setSpacing(10);
    form->setLabelAlignment(Qt::AlignRight | Qt::AlignVCenter);

    auto field_style = [](QLineEdit* le) {
        le->setStyleSheet("QLineEdit{background:" + QString(ui::colors::BG_RAISED()) +
                          ";color:" + QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " +
                          QString(ui::colors::BORDER_MED()) +
                          ";"
                          "border-radius:3px;padding:6px;}"
                          "QLineEdit:focus{border:1px solid " +
                          QString(ui::colors::AMBER()) + ";}");
        le->setMinimumWidth(320);
    };
    auto lbl_style = [](QLabel* l) { l->setStyleSheet("color:" + QString(ui::colors::TEXT_SECONDARY()) + ";"); };

    provider_field_lbl_ = new QLabel(tr("Provider"));
    lbl_style(provider_field_lbl_);
    provider_edit_ = new QLineEdit;
    provider_edit_->setPlaceholderText(tr("e.g. openai"));
    provider_edit_->setReadOnly(true); // set by selection
    field_style(provider_edit_);
    provider_edit_->setStyleSheet(provider_edit_->styleSheet() +
                                  "QLineEdit{background:" + QString(ui::colors::BG_SURFACE()) +
                                  ";color:" + QString(ui::colors::TEXT_TERTIARY()) + ";}");
    form->addRow(provider_field_lbl_, provider_edit_);

    api_key_field_lbl_ = new QLabel(tr("API Key"));
    lbl_style(api_key_field_lbl_);
    api_key_edit_ = new QLineEdit;
    api_key_edit_->setPlaceholderText("sk-...");
    api_key_edit_->setEchoMode(QLineEdit::Password);
    field_style(api_key_edit_);
    form->addRow(api_key_field_lbl_, api_key_edit_);

    model_field_lbl_ = new QLabel(tr("Model"));
    lbl_style(model_field_lbl_);
    auto* model_row = new QHBoxLayout;
    model_combo_ = new QComboBox;
    model_combo_->setEditable(true);
    model_combo_->setMinimumWidth(260);
    model_combo_->lineEdit()->setPlaceholderText(tr("Select or type model..."));
    model_combo_->setStyleSheet("QComboBox{background:" + QString(ui::colors::BG_RAISED()) +
                                ";color:" + QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " +
                                QString(ui::colors::BORDER_MED()) +
                                ";"
                                "border-radius:3px;padding:6px;}"
                                "QComboBox:focus{border:1px solid " +
                                QString(ui::colors::AMBER()) +
                                ";}"
                                "QComboBox::drop-down{border:none;width:20px;}"
                                "QComboBox::down-arrow{image:none;border-left:4px solid transparent;"
                                "border-right:4px solid transparent;border-top:5px solid " +
                                QString(ui::colors::TEXT_SECONDARY()) +
                                ";}"
                                "QComboBox QAbstractItemView{background:" +
                                QString(ui::colors::BG_RAISED()) + ";color:" + QString(ui::colors::TEXT_PRIMARY()) +
                                ";"
                                "selection-background-color:" +
                                QString(ui::colors::BG_RAISED()) + ";selection-color:" + QString(ui::colors::AMBER()) +
                                ";"
                                "border:1px solid " +
                                QString(ui::colors::BORDER_MED()) + ";}");
    model_row->addWidget(model_combo_, 1);

    fetch_btn_ = new QPushButton(tr("Fetch"));
    fetch_btn_->setMinimumSize(60, 30);
    fetch_btn_->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
    fetch_btn_->setStyleSheet(
        "QPushButton{background:" + QString(ui::colors::BG_RAISED()) + ";color:" + QString(ui::colors::AMBER()) +
        ";border:1px solid " + QString(ui::colors::BORDER_BRIGHT()) +
        ";"
        "border-radius:3px;font-weight:600;}"
        "QPushButton:hover{background:" +
        QString(ui::colors::BG_RAISED()) +
        ";}"
        "QPushButton:disabled{color:" +
        QString(ui::colors::TEXT_TERTIARY()) + ";border-color:" + QString(ui::colors::BORDER_MED()) + ";}");
    connect(fetch_btn_, &QPushButton::clicked, this, &LlmConfigSection::on_fetch_models);
    model_row->addWidget(fetch_btn_);

    form->addRow(model_field_lbl_, model_row);

    base_url_field_lbl_ = new QLabel(tr("Base URL"));
    lbl_style(base_url_field_lbl_);
    base_url_edit_ = new QLineEdit;
    base_url_edit_->setPlaceholderText(tr("Optional — leave empty for default"));
    field_style(base_url_edit_);
    form->addRow(base_url_field_lbl_, base_url_edit_);

    // Tools toggle
    tools_check_ = new QCheckBox(tr("Enable MCP Tools (navigation, market data, portfolio, etc.)"));
    tools_check_->setChecked(true);
    tools_check_->setStyleSheet("QCheckBox{color:" + QString(ui::colors::TEXT_PRIMARY()) +
                                ";spacing:8px;}"
                                "QCheckBox::indicator{width:16px;height:16px;border:1px solid " +
                                QString(ui::colors::BORDER_MED()) +
                                ";border-radius:3px;background:" + QString(ui::colors::BG_RAISED()) +
                                ";}"
                                "QCheckBox::indicator:checked{background:" +
                                QString(ui::colors::AMBER()) + ";border-color:" + QString(ui::colors::AMBER()) + ";}");
    tools_check_->setToolTip(tr("When enabled, the AI can interact with the terminal: navigate screens, fetch market "
                                "data, manage watchlists, etc."));
    form->addRow(new QLabel(""), tools_check_);

    vl->addLayout(form);

    // Buttons
    auto* btn_row = new QHBoxLayout;
    save_btn_ = new QPushButton(fincept::ui::literal_ampersands(tr("Save & Set Active")));
    save_btn_->setProperty("fincept_agent_risk", "account"); // agents: stores the API key and switches the active model
    save_btn_->setMinimumHeight(34);
    save_btn_->setStyleSheet(
        "QPushButton{background:" + QString(ui::colors::AMBER()) + ";color:" + QString(ui::colors::TEXT_ON_ACCENT()) +
        ";border:none;border-radius:4px;"
        "font-weight:700;padding:0 16px;}"
        "QPushButton:hover{background:" +
        QString(ui::colors::AMBER_DIM()) +
        ";}"
        "QPushButton:disabled{background:" +
        QString(ui::colors::BG_RAISED()) + ";color:" + QString(ui::colors::TEXT_TERTIARY()) + ";}");
    connect(save_btn_, &QPushButton::clicked, this, &LlmConfigSection::on_save_provider);

    test_btn_ = new QPushButton(tr("Test Connection"));
    test_btn_->setProperty("fincept_agent_risk", "spend"); // agents: sends a test request to a paid model API
    test_btn_->setMinimumHeight(34);
    test_btn_->setStyleSheet("QPushButton{background:" + QString(ui::colors::BG_RAISED()) +
                             ";color:" + QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " +
                             QString(ui::colors::BORDER_BRIGHT()) +
                             ";"
                             "border-radius:4px;padding:0 16px;}"
                             "QPushButton:hover{background:" +
                             QString(ui::colors::BG_HOVER()) + ";}");
    connect(test_btn_, &QPushButton::clicked, this, &LlmConfigSection::on_test_connection);

    btn_row->addWidget(save_btn_);
    btn_row->addWidget(test_btn_);
    btn_row->addStretch();
    vl->addLayout(btn_row);

    status_lbl_ = new QLabel;
    status_lbl_->setStyleSheet("");
    status_lbl_->hide();
    vl->addWidget(status_lbl_);

    vl->addStretch();
    scroll->setWidget(page);
    return scroll;
}

QWidget* LlmConfigSection::build_global_panel() {
    auto* panel = new QWidget(this);
    panel->setStyleSheet("background:" + QString(ui::colors::BG_SURFACE()) + ";");

    auto* vl = new QVBoxLayout(panel);
    vl->setContentsMargins(24, 12, 24, 12);
    vl->setSpacing(10);

    global_title_ = new QLabel(tr("GLOBAL SETTINGS"));
    global_title_->setStyleSheet("color:" + QString(ui::colors::AMBER()) + ";font-weight:700;letter-spacing:1px;");
    vl->addWidget(global_title_);

    auto* row = new QHBoxLayout;
    row->setSpacing(24);

    // Temperature
    auto* temp_grp = new QVBoxLayout;
    temp_lbl_ = new QLabel(tr("Temperature"));
    temp_lbl_->setStyleSheet("color:" + QString(ui::colors::TEXT_SECONDARY()) + ";");
    temp_spin_ = new QDoubleSpinBox;
    temp_spin_->setRange(0.0, 2.0);
    temp_spin_->setSingleStep(0.05);
    temp_spin_->setValue(0.7);
    temp_spin_->setDecimals(2);
    temp_spin_->setFixedWidth(100);
    temp_spin_->setStyleSheet("QDoubleSpinBox{background:" + QString(ui::colors::BG_RAISED()) +
                              ";color:" + QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " +
                              QString(ui::colors::BORDER_MED()) +
                              ";"
                              "border-radius:3px;padding:4px;}");
    temp_grp->addWidget(temp_lbl_);
    temp_grp->addWidget(temp_spin_);
    row->addLayout(temp_grp);

    // Max tokens
    auto* tok_grp = new QVBoxLayout;
    tokens_lbl_ = new QLabel(tr("Max Tokens"));
    tokens_lbl_->setStyleSheet("color:" + QString(ui::colors::TEXT_SECONDARY()) + ";");
    tokens_spin_ = new QSpinBox;
    // 0 = Auto (the model's own output cap — the stored default since v021).
    // The floor used to be 100, so merely saving this panel turned Auto into a
    // 100-token limit. The ceiling follows the active provider (load_providers).
    tokens_spin_->setRange(0, kLlmProvidersTokensMax);
    tokens_spin_->setSpecialValueText(tr("Auto"));
    tokens_spin_->setSingleStep(100);
    tokens_spin_->setValue(4096);
    tokens_spin_->setFixedWidth(100);
    tokens_spin_->setStyleSheet("QSpinBox{background:" + QString(ui::colors::BG_RAISED()) +
                                ";color:" + QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " +
                                QString(ui::colors::BORDER_MED()) +
                                ";"
                                "border-radius:3px;padding:4px;}");
    tok_grp->addWidget(tokens_lbl_);
    tok_grp->addWidget(tokens_spin_);
    row->addLayout(tok_grp);

    // Max tool rounds — ceiling on the LlmService tool-call loop. Higher
    // values let long workflows (multi-section report fills) finish; lower
    // values cap the blast radius of a runaway model.
    auto* rounds_grp = new QVBoxLayout;
    tool_rounds_lbl_ = new QLabel(tr("Max Tool Rounds"));
    tool_rounds_lbl_->setStyleSheet("color:" + QString(ui::colors::TEXT_SECONDARY()) + ";");
    tool_rounds_lbl_->setToolTip(tr("Ceiling on tool-call rounds per chat turn. Default 40.\n"
                                    "Range 1-200. Raise for long workflows (e.g. populating multi-section reports)."));
    tool_rounds_spin_ = new QSpinBox;
    tool_rounds_spin_->setRange(1, 200);
    tool_rounds_spin_->setSingleStep(5);
    tool_rounds_spin_->setValue(40);
    tool_rounds_spin_->setFixedWidth(100);
    tool_rounds_spin_->setStyleSheet("QSpinBox{background:" + QString(ui::colors::BG_RAISED()) +
                                     ";color:" + QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " +
                                     QString(ui::colors::BORDER_MED()) +
                                     ";"
                                     "border-radius:3px;padding:4px;}");
    rounds_grp->addWidget(tool_rounds_lbl_);
    rounds_grp->addWidget(tool_rounds_spin_);
    row->addLayout(rounds_grp);

    // System prompt
    auto* sp_grp = new QVBoxLayout;
    system_prompt_lbl_ = new QLabel(tr("System Prompt"));
    system_prompt_lbl_->setStyleSheet("color:" + QString(ui::colors::TEXT_SECONDARY()) + ";");
    system_prompt_ = new QPlainTextEdit;
    system_prompt_->setPlaceholderText(tr("Optional system prompt for the LLM..."));
    system_prompt_->setFixedHeight(60);
    system_prompt_->setStyleSheet("QPlainTextEdit{background:" + QString(ui::colors::BG_RAISED()) +
                                  ";color:" + QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " +
                                  QString(ui::colors::BORDER_MED()) +
                                  ";"
                                  "border-radius:3px;padding:4px;}"
                                  "QPlainTextEdit:focus{border:1px solid " +
                                  QString(ui::colors::AMBER()) + ";}");
    sp_grp->addWidget(system_prompt_lbl_);
    sp_grp->addWidget(system_prompt_);
    row->addLayout(sp_grp, 1);

    vl->addLayout(row);

    cost_guardrails_title_ = new QLabel(tr("BYO AI COST GUARDRAILS"));
    cost_guardrails_title_->setStyleSheet("color:" + QString(ui::colors::AMBER()) +
                                          ";font-weight:700;letter-spacing:1px;");
    vl->addWidget(cost_guardrails_title_);

    auto* budget_row = new QHBoxLayout;
    budget_row->setSpacing(16);
    const QString budget_spin_style =
        "QSpinBox{background:" + QString(ui::colors::BG_RAISED()) + ";color:" +
        QString(ui::colors::TEXT_PRIMARY()) + ";border:1px solid " + QString(ui::colors::BORDER_MED()) +
        ";border-radius:3px;padding:4px;}";
    const QString budget_label_style = "color:" + QString(ui::colors::TEXT_SECONDARY()) + ";";

    auto add_budget_control = [&](QLabel*& label, QSpinBox*& spin, const QString& text, int minimum, int maximum,
                                  int step, const QString& object_name) {
        auto* group = new QVBoxLayout;
        label = new QLabel(text);
        label->setStyleSheet(budget_label_style);
        spin = new QSpinBox;
        spin->setObjectName(object_name);
        spin->setRange(minimum, maximum);
        spin->setSingleStep(step);
        spin->setFixedWidth(142);
        spin->setStyleSheet(budget_spin_style);
        group->addWidget(label);
        group->addWidget(spin);
        budget_row->addLayout(group);
    };

    add_budget_control(history_budget_lbl_, history_budget_spin_, tr("History Chars"), 0,
                       ai_chat::kMaxLlmTurnTokens, 4000, QStringLiteral("llmHistoryBudgetSpin"));
    history_budget_spin_->setSpecialValueText(tr("Unlimited"));
    add_budget_control(prompt_budget_lbl_, prompt_budget_spin_, tr("Turn Input Tokens"), 0,
                       ai_chat::kMaxLlmTurnTokens, 1000, QStringLiteral("llmPromptBudgetSpin"));
    prompt_budget_spin_->setSpecialValueText(tr("Unlimited"));
    add_budget_control(completion_budget_lbl_, completion_budget_spin_, tr("Turn Output Tokens"), 0,
                       ai_chat::kMaxLlmTurnTokens, 1000, QStringLiteral("llmCompletionBudgetSpin"));
    completion_budget_spin_->setSpecialValueText(tr("Unlimited"));
    add_budget_control(summary_context_budget_lbl_, summary_context_budget_spin_, tr("Summary Context Bytes"),
                       ai_chat::kMinLlmSummaryContextBytes, ai_chat::kMaxLlmSummaryContextBytes, 1024,
                       QStringLiteral("llmSummaryContextBudgetSpin"));
    add_budget_control(summary_output_budget_lbl_, summary_output_budget_spin_, tr("Summary Output Tokens"),
                       ai_chat::kMinLlmSummaryOutputTokens, ai_chat::kMaxLlmSummaryOutputTokens, 128,
                       QStringLiteral("llmSummaryOutputBudgetSpin"));
    budget_row->addStretch();
    vl->addLayout(budget_row);

    save_global_btn_ = new QPushButton(tr("Save Global Settings"));
    save_global_btn_->setMinimumSize(180, 30);
    save_global_btn_->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
    save_global_btn_->setStyleSheet("QPushButton{background:" + QString(ui::colors::BG_RAISED()) + ";color:" +
                                    QString(ui::colors::AMBER()) + ";border:1px solid " + QString(ui::colors::AMBER()) +
                                    ";"
                                    "border-radius:3px;font-weight:600;}"
                                    "QPushButton:hover{background:" +
                                    QString(ui::colors::BG_RAISED()) + ";}");
    connect(save_global_btn_, &QPushButton::clicked, this, &LlmConfigSection::on_save_global);
    vl->addWidget(save_global_btn_);

    return panel;
}

// ============================================================================
// Data Loading
// ============================================================================

void LlmConfigSection::load_providers() {
    // Remember the currently-selected provider so a reload (e.g. after Save)
    // doesn't snap the user back to the first row. Fall back to the active
    // provider on first load, then row 0 if nothing else applies.
    QString prior_selection;
    if (provider_list_->currentItem())
        prior_selection = provider_list_->currentItem()->data(Qt::UserRole).toString();

    provider_list_->blockSignals(true);
    provider_list_->clear();

    QString active_provider;
    QString first_visible_provider;
    auto result = LlmConfigRepository::instance().list_providers();
    if (result.is_err()) {
        const QString error = QString::fromStdString(result.error());
        show_status(tr("Could not read provider settings: ") + error, true);
        LOG_ERROR(TAG_PROVIDERS, "load_providers failed: " + privacy::redact_text(error));
    } else {
        for (const auto& p : result.value()) {
            // Rows created by older builds stay in storage for explicit user
            // recovery, but cannot reappear or become effective in this UI.
            if (!provider_is_visible_in_community(p.provider))
                continue;
            if (first_visible_provider.isEmpty())
                first_visible_provider = p.provider;
            bool is_fincept = (p.provider.toLower() == "fincept");
            QString display = provider_display_name(p.provider);
            if (p.is_active) {
                display += "  ✓";
                active_provider = p.provider;
            }
            // Show model tag only for non-fincept providers
            const QString displayed_model = policy::normalize_llm_model(p.provider, p.model);
            if (!is_fincept && !displayed_model.isEmpty())
                display += "  [" + displayed_model + "]";
            auto* item = new QListWidgetItem(display);
            item->setData(Qt::UserRole, p.provider);
            item->setData(Qt::UserRole + 1, p.is_active); // read by the theme_changed recolour
            // Only the active provider gets amber color
            if (p.is_active)
                item->setForeground(QColor(ui::colors::AMBER()));
            else
                item->setForeground(QColor(ui::colors::TEXT_SECONDARY()));
            provider_list_->addItem(item);
        }
    }

    provider_list_->blockSignals(false);

    // Max Tokens applies to the provider LlmService actually uses: the active
    // visible row, else the first visible row, else the BYO default.
    QString effective_provider = active_provider;
    if (effective_provider.isEmpty())
        effective_provider = first_visible_provider.isEmpty() ? policy::default_llm_provider() : first_visible_provider;
    const bool fincept_active = effective_provider.compare(QLatin1String("fincept"), Qt::CaseInsensitive) == 0;
    const int tokens_max = fincept_active ? ai_chat::ModelCatalog::kFinceptMaxOutputTokens : kLlmProvidersTokensMax;
    tokens_spin_->setMaximum(tokens_max);
    tokens_spin_->setToolTip(fincept_active ? tr("Maximum output tokens per reply. Auto = %1. The Fincept AI "
                                                 "service accepts at most %1.")
                                                  .arg(tokens_max)
                                            : tr("Maximum output tokens per reply. Auto = the model's own "
                                                 "limit; larger values are capped to it."));

    // Load global settings
    auto gs = LlmConfigRepository::instance().get_global_settings();
    if (gs.is_err()) {
        const QString error = QString::fromStdString(gs.error());
        show_status(tr("Could not read global LLM settings: ") + error, true);
        LOG_ERROR(TAG_PROVIDERS, "get_global_settings failed: " + privacy::redact_text(error));
    } else {
        LlmGlobalSettings displayed = gs.value();
        if (fincept_active && displayed.max_tokens > tokens_max) {
            // Saved before this ceiling existed (the control allowed 32000):
            // the Fincept API answered every chat with a 422. Store the
            // ceiling so the panel shows what is actually sent.
            LlmGlobalSettings fixed = displayed;
            LOG_INFO(TAG_PROVIDERS, QString("Max Tokens %1 exceeds the Fincept limit — lowered to %2")
                                        .arg(fixed.max_tokens)
                                        .arg(tokens_max));
            fixed.max_tokens = tokens_max;
            displayed = fixed;
            auto fixed_saved = LlmConfigRepository::instance().save_global_settings(fixed);
            if (fixed_saved.is_err()) {
                const QString error = QString::fromStdString(fixed_saved.error());
                show_status(tr("Could not persist Max Tokens correction: ") + error, true);
                LOG_ERROR(TAG_PROVIDERS,
                          "save corrected global settings failed: " + privacy::redact_text(error));
            } else {
                emit config_changed();
            }
        }
        temp_spin_->setValue(displayed.temperature);
        tokens_spin_->setValue(displayed.max_tokens);
        system_prompt_->setPlainText(displayed.system_prompt);
        if (tool_rounds_spin_)
            tool_rounds_spin_->setValue(displayed.max_tool_rounds);
    }

    QVariantMap budget_values;
    auto& app_config = AppConfig::instance();
    budget_values.insert(QString::fromLatin1(ai_chat::kLlmHistoryCharsKey),
                         app_config.get(ai_chat::kLlmHistoryCharsKey, ai_chat::kDefaultLlmHistoryChars));
    budget_values.insert(QString::fromLatin1(ai_chat::kLlmPromptTokensKey),
                         app_config.get(ai_chat::kLlmPromptTokensKey, ai_chat::kDefaultLlmPromptTokens));
    budget_values.insert(QString::fromLatin1(ai_chat::kLlmCompletionTokensKey),
                         app_config.get(ai_chat::kLlmCompletionTokensKey, ai_chat::kDefaultLlmCompletionTokens));
    budget_values.insert(
        QString::fromLatin1(ai_chat::kLlmSummaryContextBytesKey),
        app_config.get(ai_chat::kLlmSummaryContextBytesKey, ai_chat::kDefaultLlmSummaryContextBytes));
    budget_values.insert(
        QString::fromLatin1(ai_chat::kLlmSummaryOutputTokensKey),
        app_config.get(ai_chat::kLlmSummaryOutputTokensKey, ai_chat::kDefaultLlmSummaryOutputTokens));
    const ai_chat::LlmBudgetSettings budgets = ai_chat::llm_budget_settings_from_config_map(budget_values);
    history_budget_spin_->setValue(budgets.history_chars);
    prompt_budget_spin_->setValue(budgets.prompt_tokens);
    completion_budget_spin_->setValue(budgets.completion_tokens);
    summary_context_budget_spin_->setValue(budgets.summary_context_bytes);
    summary_output_budget_spin_->setValue(budgets.summary_output_tokens);

    delete_btn_->setEnabled(false);

    // Selection precedence: prior selection (preserves UX across Save reloads)
    // > active provider (so first load lands on what the user is actually using)
    // > row 0 (fallback).
    int target_row = -1;
    const QString wanted = !prior_selection.isEmpty() ? prior_selection : active_provider;
    if (!wanted.isEmpty()) {
        for (int i = 0; i < provider_list_->count(); ++i) {
            if (provider_list_->item(i)->data(Qt::UserRole).toString() == wanted) {
                target_row = i;
                break;
            }
        }
    }
    if (target_row < 0 && provider_list_->count() > 0)
        target_row = 0;
    if (target_row >= 0)
        provider_list_->setCurrentRow(target_row);
}

void LlmConfigSection::populate_form(const QString& provider) {
    provider_edit_->setText(provider);
    // Never carry a previous provider's secret into a form whose backing row
    // has not been read successfully. Keep all spending/mutating actions
    // disabled until the selected row (or its definitive absence) is known.
    api_key_edit_->clear();
    save_btn_->setEnabled(false);
    test_btn_->setEnabled(false);
    fetch_btn_->setEnabled(false);

    bool is_fincept = (provider.toLower() == "fincept");

    // Populate model combo with provider-specific fallback suggestions.
    model_combo_->blockSignals(true);
    model_combo_->clear();
    model_combo_->addItems(fallback_models(provider));
    model_combo_->blockSignals(false);

    // Auto-fill base URL for known providers
    QString def_url = default_base_url(provider);

    auto result = LlmConfigRepository::instance().list_providers();
    if (result.is_err()) {
        const QString error = QString::fromStdString(result.error());
        show_status(tr("Could not load provider settings: ") + error, true);
        LOG_ERROR(TAG_PROVIDERS, "populate_form failed: " + privacy::redact_text(error));
        return;
    }
    save_btn_->setEnabled(true);
    test_btn_->setEnabled(true);

    for (const auto& p : result.value()) {
        if (p.provider.toLower() == provider.toLower()) {
            // Set model — try to select in combo, or set as editable text
            const QString normalized_model = policy::normalize_llm_model(p.provider, p.model);
            if (!normalized_model.isEmpty()) {
                int idx = model_combo_->findText(normalized_model);
                if (idx >= 0)
                    model_combo_->setCurrentIndex(idx);
                else
                    model_combo_->setCurrentText(normalized_model);
            }
            base_url_edit_->setText(p.base_url.isEmpty() ? def_url : p.base_url);

            // Tools toggle — always visible
            tools_check_->setChecked(p.tools_enabled);
            tools_check_->setVisible(true);

            if (is_fincept) {
                api_key_edit_->clear();
                const QString stored = fincept::auth::AuthManager::instance().fincept_api_key();
                if (!stored.isEmpty()) {
                    // Show only the trailing 4 characters. The previous preview
                    // printed the first 8, which for a prefixed key format gives
                    // away a meaningful slice of the secret in a field that is
                    // always visible (placeholders ignore Password echo mode).
                    api_key_edit_->setPlaceholderText(
                        tr("Linked to your Fincept account: ••••%1").arg(stored.right(4)));
                } else {
                    api_key_edit_->setPlaceholderText(tr("Login to your Fincept account to enable"));
                }
                api_key_edit_->setEnabled(false);
                // Fincept is a managed service — hide model/base_url/fetch
                model_combo_->setVisible(false);
                fetch_btn_->setVisible(false);
                base_url_edit_->setVisible(false);
            } else {
                api_key_edit_->setText(p.api_key);
                api_key_edit_->setEnabled(true);
                api_key_edit_->setPlaceholderText("sk-...");
                model_combo_->setVisible(true);
                model_combo_->setEnabled(true);
                fetch_btn_->setVisible(true);
                fetch_btn_->setEnabled(true);
                base_url_edit_->setVisible(true);
                base_url_edit_->setEnabled(true);
            }
            return;
        }
    }

    // New provider — clear form
    api_key_edit_->clear();
    api_key_edit_->setEnabled(!is_fincept);
    if (is_fincept) {
        const QString stored = fincept::auth::AuthManager::instance().fincept_api_key();
        if (!stored.isEmpty())
            api_key_edit_->setPlaceholderText(tr("Linked to your Fincept account: ••••%1").arg(stored.right(4)));
        else
            api_key_edit_->setPlaceholderText(tr("Login to your Fincept account to enable"));
        model_combo_->setVisible(false);
        fetch_btn_->setVisible(false);
        base_url_edit_->setVisible(false);
    } else {
        api_key_edit_->setPlaceholderText("sk-...");
        model_combo_->setVisible(true);
        model_combo_->setEnabled(true);
        fetch_btn_->setVisible(true);
        fetch_btn_->setEnabled(true);
        base_url_edit_->setVisible(true);
        base_url_edit_->setEnabled(true);
        base_url_edit_->setText(def_url);
    }
}

// ============================================================================
// Slots
// ============================================================================

void LlmConfigSection::on_provider_selected(int row) {
    if (row < 0 || row >= provider_list_->count()) {
        delete_btn_->setEnabled(false);
        return;
    }

    QString provider = provider_list_->item(row)->data(Qt::UserRole).toString();
    delete_btn_->setEnabled(provider.toLower() != "fincept");
    populate_form(provider);
}

void LlmConfigSection::on_save_provider() {
    QString provider = provider_edit_->text().trimmed().toLower();
    if (provider.isEmpty()) {
        show_status(tr("No provider selected"), true);
        return;
    }
    if (!policy::is_ai_backend_selectable(provider)) {
        show_status(tr("This AI backend is no longer available. Choose DeepSeek or another BYO provider."), true);
        return;
    }

    bool is_fincept = (provider == "fincept");

    // Keep activation unchanged until set_active() performs the single atomic
    // swap. If the row save or secure-key migration fails, the previously
    // active provider must remain the only active provider.
    bool was_active = false;
    auto configured = LlmConfigRepository::instance().list_providers();
    if (configured.is_err()) {
        const QString error = QString::fromStdString(configured.error());
        show_status(tr("Could not read current provider state: ") + error, true);
        LOG_ERROR(TAG_PROVIDERS, "list_providers failed before save: " + privacy::redact_text(error));
        return;
    }
    for (const auto& current : configured.value()) {
        if (current.provider.compare(provider, Qt::CaseInsensitive) == 0) {
            was_active = current.is_active;
            break;
        }
    }

    LlmConfig cfg;
    cfg.provider = provider;
    cfg.api_key = is_fincept ? QString() : api_key_edit_->text().trimmed();
    cfg.model = policy::normalize_llm_model(cfg.provider, model_combo_->currentText());
    cfg.base_url = base_url_edit_->text().trimmed();
    cfg.is_active = was_active;
    cfg.tools_enabled = tools_check_->isChecked();

    // Fincept defaults — endpoints are hardcoded in LlmService, base_url not needed
    if (is_fincept) {
        if (cfg.model.isEmpty())
            cfg.model = "MiniMax-M2.7";
        cfg.base_url = {}; // not used for fincept
    }

    // Basic validation
    if (!is_fincept && cfg.api_key.isEmpty()) {
        show_status(tr("API key is required for %1").arg(provider), true);
        return;
    }
    if (!is_fincept && cfg.model.isEmpty()) {
        show_status(tr("Model name is required"), true);
        return;
    }
    // The base URL feeds straight into the request endpoint — reject typos here
    // rather than as an opaque connection error on the first chat message.
    if (!cfg.base_url.isEmpty()) {
        const QUrl u(cfg.base_url, QUrl::StrictMode);
        const QString scheme = u.scheme().toLower();
        if (!u.isValid() || u.host().isEmpty() || (scheme != QLatin1String("http") && scheme != QLatin1String("https"))) {
            show_status(tr("Base URL must be a full http:// or https:// address"), true);
            return;
        }
    }

    // Save without changing activation, THEN atomically deactivate the old row
    // and activate this one.
    auto r2 = LlmConfigRepository::instance().save_provider(cfg);
    if (r2.is_err()) {
        show_status(tr("Failed to save: ") + QString::fromStdString(r2.error()), true);
        LOG_ERROR(TAG_PROVIDERS,
                  "save_provider failed for " + provider + ": " +
                      privacy::redact_text(QString::fromStdString(r2.error())));
        return;
    }
    auto r3 = LlmConfigRepository::instance().set_active(provider);
    if (r3.is_err()) {
        show_status(tr("Failed to activate: ") + QString::fromStdString(r3.error()), true);
        LOG_ERROR(TAG_PROVIDERS,
                  "set_active failed for " + provider + ": " +
                      privacy::redact_text(QString::fromStdString(r3.error())));
        return;
    }

    // Read-after-write verification — catches silent failures where the INSERT
    // reports success but the row never lands (e.g. autocommit off, FK rollback,
    // wrong DB path). Without this the old code would say "Saved" and the user
    // only finds out on restart.
    auto verify = LlmConfigRepository::instance().get_active_provider();
    if (!verify.is_ok() || verify.value().provider.toLower() != provider) {
        QString detail = verify.is_ok() ? tr("active is '%1' not '%2'").arg(verify.value().provider, provider)
                                        : QString::fromStdString(verify.error());
        show_status(tr("Save verification failed: ") + detail, true);
        LOG_ERROR(TAG_PROVIDERS, "Save verification failed — " + privacy::redact_text(detail));
        return;
    }

    show_status(tr("Saved and set as active provider"), false);
    load_providers();
    ai_chat::LlmService::instance().reload_config();
    emit config_changed();

    LOG_INFO(TAG_PROVIDERS, "LLM provider saved and verified: " + provider + " / " + cfg.model);
}

void LlmConfigSection::on_delete_provider() {
    int row = provider_list_->currentRow();
    if (row < 0)
        return;

    auto* selected_item = provider_list_->item(row);
    QString provider = selected_item->data(Qt::UserRole).toString();
    const bool was_active = selected_item->data(Qt::UserRole + 1).toBool();

    if (provider.toLower() == "fincept") {
        show_status(tr("Cannot remove built-in Fincept provider"), true);
        return;
    }

    auto reply = QMessageBox::question(this, tr("Delete Provider"), tr("Remove '%1' configuration?").arg(provider),
                                       QMessageBox::Yes | QMessageBox::No);

    if (reply != QMessageBox::Yes)
        return;

    auto removed = LlmConfigRepository::instance().delete_provider(provider);
    if (removed.is_err()) {
        const QString error = QString::fromStdString(removed.error());
        show_status(tr("Could not remove provider: ") + error, true);
        LOG_ERROR(TAG_PROVIDERS,
                  "delete_provider failed for " + provider + ": " + privacy::redact_text(error));
        return;
    }

    if (was_active) {
        const auto remaining = LlmConfigRepository::instance().list_providers();
        if (remaining.is_err()) {
            const QString error = QString::fromStdString(remaining.error());
            show_status(tr("Provider removed, but remaining providers could not be read: ") + error, true);
            LOG_ERROR(TAG_PROVIDERS,
                      "list_providers failed after delete: " + privacy::redact_text(error));
        } else {
            QString fallback;
            const QString preferred = policy::default_llm_provider();
            for (const auto& candidate : remaining.value()) {
                if (provider_is_visible_in_community(candidate.provider) &&
                    candidate.provider.compare(preferred, Qt::CaseInsensitive) == 0) {
                    fallback = candidate.provider;
                    break;
                }
            }
            if (fallback.isEmpty()) {
                for (const auto& candidate : remaining.value()) {
                    if (provider_is_visible_in_community(candidate.provider)) {
                        fallback = candidate.provider;
                        break;
                    }
                }
            }
            if (!fallback.isEmpty()) {
                const auto activated = LlmConfigRepository::instance().set_active(fallback);
                if (activated.is_err()) {
                    const QString error = QString::fromStdString(activated.error());
                    show_status(tr("Provider removed, but fallback activation failed: ") + error, true);
                    LOG_ERROR(TAG_PROVIDERS,
                              "Fallback activation failed after delete: " + privacy::redact_text(error));
                }
            }
        }
    }
    load_providers();
    emit config_changed();
}

void LlmConfigSection::on_save_global() {
    LlmGlobalSettings gs;
    gs.temperature = temp_spin_->value();
    gs.max_tokens = tokens_spin_->value();
    gs.system_prompt = system_prompt_->toPlainText().trimmed();
    if (tool_rounds_spin_)
        gs.max_tool_rounds = tool_rounds_spin_->value();

    auto r = LlmConfigRepository::instance().save_global_settings(gs);
    if (r.is_err()) {
        const QString error = QString::fromStdString(r.error());
        show_status(tr("Failed to save global settings: ") + error, true);
        LOG_ERROR(TAG_PROVIDERS, "save_global_settings failed: " + privacy::redact_text(error));
        return;
    }

    const ai_chat::LlmBudgetSettings budgets = ai_chat::validate_llm_budget_settings(
        {history_budget_spin_->value(), prompt_budget_spin_->value(), completion_budget_spin_->value(),
         summary_context_budget_spin_->value(), summary_output_budget_spin_->value()});
    const QVariantMap budget_values = ai_chat::llm_budget_settings_to_config_map(budgets);
    auto& app_config = AppConfig::instance();
    for (auto it = budget_values.cbegin(); it != budget_values.cend(); ++it)
        app_config.set(it.key(), it.value());

    show_status(tr("Global settings saved"), false);
    emit config_changed();
}

void LlmConfigSection::on_test_connection() {
    QString provider = provider_edit_->text().trimmed().toLower();
    if (provider.isEmpty()) {
        show_status(tr("Select a provider first"), true);
        return;
    }
    if (!policy::is_ai_backend_selectable(provider)) {
        show_status(tr("This AI backend is no longer available. Choose DeepSeek or another BYO provider."), true);
        return;
    }

    if (provider == "fincept") {
        // Fincept is a managed service — verify API key exists (session → SecureStorage)
        const QString stored = fincept::auth::AuthManager::instance().fincept_api_key();
        if (!stored.isEmpty())
            show_status(tr("Fincept connected — API key active"), false);
        else
            show_status(tr("Not connected — login to your Fincept account first"), true);
        return;
    }

    if (api_key_edit_->text().trimmed().isEmpty()) {
        show_status(tr("API key required for test"), true);
        return;
    }

    show_status(tr("Testing connection..."), false);
    test_btn_->setEnabled(false);

    // Real test: fetch models list — if it succeeds, the connection works.
    // on_models_fetched will populate the combo; we just re-enable the button here.
    // Use a one-shot connection for the test status feedback.
    auto conn = std::make_shared<QMetaObject::Connection>();
    *conn = connect(&ai_chat::LlmService::instance(), &ai_chat::LlmService::models_fetched, this,
                    [this, provider, conn](const QString& p, const QStringList& models, const QString& err) {
                        if (p.toLower() != provider.toLower())
                            return;
                        test_btn_->setEnabled(true);
                        if (err.isEmpty())
                            show_status(tr("Connected — %1 models available").arg(models.size()), false);
                        else
                            show_status(tr("Connection failed: ") + err, true);
                        disconnect(*conn);
                    });

    ai_chat::LlmService::instance().fetch_models(provider, api_key_edit_->text().trimmed(),
                                                 base_url_edit_->text().trimmed());
}

void LlmConfigSection::on_fetch_models() {
    QString provider = provider_edit_->text().trimmed().toLower();
    if (provider.isEmpty()) {
        show_status(tr("Select a provider first"), true);
        return;
    }
    if (!policy::is_ai_backend_selectable(provider)) {
        show_status(tr("This AI backend is no longer available. Choose DeepSeek or another BYO provider."), true);
        return;
    }

    if (provider == "fincept") {
        show_status(tr("Fincept manages models automatically"), false);
        return;
    }

    if (api_key_edit_->text().trimmed().isEmpty()) {
        show_status(tr("Enter API key first, then fetch models"), true);
        return;
    }

    show_status(tr("Fetching models..."), false);
    fetch_btn_->setEnabled(false);

    ai_chat::LlmService::instance().fetch_models(provider, api_key_edit_->text().trimmed(),
                                                 base_url_edit_->text().trimmed());
}

void LlmConfigSection::on_models_fetched(const QString& provider, const QStringList& models, const QString& error) {
    fetch_btn_->setEnabled(true);

    // Only apply if still viewing this provider
    if (provider_edit_->text().trimmed().toLower() != provider.toLower())
        return;

    if (!error.isEmpty()) {
        // Show the actual error so users can act on it (timeout, 401, host
        // not found, etc.) instead of guessing.
        show_status(tr("Fetch failed: ") + error, true);
        return;
    }

    // Save current selection
    QString current = model_combo_->currentText();

    model_combo_->blockSignals(true);
    model_combo_->clear();
    model_combo_->addItems(models);
    model_combo_->blockSignals(false);

    // Restore selection if still valid
    int idx = model_combo_->findText(current);
    if (idx >= 0)
        model_combo_->setCurrentIndex(idx);
    else if (!current.isEmpty())
        model_combo_->setCurrentText(current);
    else if (model_combo_->count() > 0)
        model_combo_->setCurrentIndex(0);

    show_status(tr("%1 models loaded for %2").arg(models.size()).arg(provider), false);
    LOG_DEBUG(TAG_PROVIDERS, QString("Fetched %1 models for %2").arg(models.size()).arg(provider));
}

} // namespace fincept::screens
