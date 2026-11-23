// LlmToolLoop.cpp — tool-execution layer for LlmService.
//
// Two entry points:
//   - do_tool_loop: structured OpenAI-compatible tool-call follow-up loop
//     (bounded by detail::ToolLoopBudget — rounds AND wall-clock — with
//     parallel tool execution; emits a completion record per call).
//   - try_extract_and_execute_text_tool_calls: detects XML/text tool calls
//     emitted by models that don't support structured tool_calls (minimax,
//     some OpenRouter models). Executes them and asks the LLM to summarise.

#include "core/config/AppConfig.h"
#include "core/logging/Logger.h"
#include "core/privacy/LogRedact.h"
#include "mcp/McpProvider.h"
#include "mcp/McpService.h"
#include "mcp/ResultStore.h"
#include "services/llm/LlmContentExtractors.h"
#include "services/llm/DeepSeekRequestPolicy.h"
#include "services/llm/LlmRequestPolicy.h"
#include "services/llm/LlmService.h"
#include "services/llm/LlmSummaryPolicy.h"
#include "services/llm/LlmToolActivity.h"
#include "services/llm/LlmTransportPolicy.h"
#include "services/llm/LlmUsage.h"

#include <QElapsedTimer>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QString>

#include <algorithm>
#include <vector>

namespace fincept::ai_chat {

namespace {
constexpr const char* kLlmToolLoopTag = "LlmService";

// Every round of a tool loop is billed: the whole transcript is re-posted each
// time. Reporting only the final round's usage (the old behaviour) under-counted
// a 10-round turn by roughly 10x in the chat's token counter.
void llm_tool_loop_add_usage(LlmResponse& total, const LlmResponse& round) {
    add_provider_usage(total, round);
}
} // namespace

void emit_tool_done(const QString& display, const QJsonObject& args, const mcp::ToolResult& tr, qint64 elapsed_ms,
                    bool parallel) {
    // Every provider path reports a finished call here, so this is the one INFO line per
    // tool call (policy B.4): the tool by name, never its arguments or its result.
    if (Logger::instance().should_log(LogLevel::Info, kLlmToolLoopTag)) {
        QList<LogField> fields{{"tool", display}, {"ok", tr.success}, {"duration_ms", elapsed_ms}};
        if (parallel)
            fields.append(LogField("parallel", true));
        if (!tr.success)
            fields.append(LogField("error", privacy::redact_text(tr.error).left(120)));
        LOG_INFO(kLlmToolLoopTag, logf("LLM tool call finished", fields));
    }
    detail::emit_progress(tool_stream_prefix() +
                          encode_tool_done(make_tool_done(detail::tool_progress_label(display, args), tr, elapsed_ms,
                                                          parallel)) +
                          QStringLiteral("\n"));
}

LlmResponse LlmService::do_tool_loop(QJsonArray loop_messages, const QString& url,
                                     const QMap<QString, QString>& headers, int initial_prompt_tokens,
                                     int initial_completion_tokens, qint64 initial_prompt_bytes,
                                     qint64 initial_response_bytes) {
    LlmResponse resp;
    LlmResponse usage; // summed over every round of this loop
    ProviderUsageScope preserve_usage(resp, usage);
    // Operating the terminal takes a few rounds per task (look, open, act,
    // read), and ui_act batches up to ten steps per round.
    const int MAX_ROUNDS = active_max_tool_rounds();
    // Rounds bound the token spend; the deadline bounds the wait. A round that
    // long-polls a background job can take 30 s, so MAX_ROUNDS alone leaves the
    // turn unbounded in wall-clock terms.
    detail::ToolLoopBudget budget(MAX_ROUNDS);
    budget.note_prompt_tokens(budgeted_llm_tokens(initial_prompt_tokens, initial_prompt_bytes));
    budget.note_completion_tokens(budgeted_llm_tokens(initial_completion_tokens, initial_response_bytes));
    LOG_DEBUG(kLlmToolLoopTag, QString("TOOL LOOP: starting (max %1 rounds, %2 s deadline, %3 input-token budget, "
                                      "%4 output-token budget, model=%5)")
                                   .arg(MAX_ROUNDS)
                                   .arg(detail::tool_loop_deadline_ms() / 1000)
                                   .arg(detail::tool_loop_prompt_token_budget())
                                   .arg(detail::tool_loop_completion_token_budget())
                                   .arg(model_));

    // Replies that only announced a next step ("I'll look for…") and were sent back.
    int intent_nudges = 0;
    constexpr int kMaxIntentNudges = 3;

    for (int round = 0; !budget.exhausted(); ++round) {
        // Stop pressed: leave before issuing another request or running more tools.
        if (detail::cancel_requested()) {
            LOG_DEBUG(kLlmToolLoopTag, QString("TOOL LOOP: cancelled by user before round %1").arg(round));
            resp.cancelled = true;
            resp.error = "Request cancelled";
            return resp;
        }
        budget.note_round();
        QJsonObject fu;
        fu["model"] = model_;
        fu["messages"] = loop_messages;
        // Temperature intentionally omitted — provider default.
        apply_openai_token_limit(fu);
        const int round_limit = budget.clamp_completion_allowance(resolved_max_tokens());
        if (fu.contains(QStringLiteral("max_completion_tokens")))
            fu[QStringLiteral("max_completion_tokens")] = round_limit;
        else
            fu[QStringLiteral("max_tokens")] = round_limit;

        QJsonArray tools =
            mcp::McpService::instance().format_tools_for_openai(detail::apply_request_policy(tool_filter_));
        if (!tools.isEmpty())
            fu["tools"] = tools;
        if (provider_ == QLatin1String("deepseek"))
            apply_deepseek_thinking(fu, detail::t_reasoning_effort);

        auto http = blocking_post(url, fu, headers);
        if (!http.success) {
            note_provider_request(usage);
            resp.error = "Tool follow-up failed: " + http.error;
            return resp;
        }

        auto doc = QJsonDocument::fromJson(http.body);
        if (doc.isNull()) {
            note_provider_request(usage);
            resp.error = "Parse error in tool follow-up";
            LOG_ERROR(kLlmToolLoopTag, logf("Tool follow-up response is not valid JSON",
                                            {{"provider", provider_},
                                             {"model", model_},
                                             {"round", round},
                                             {"bytes", http.body.size()}}));
            return resp;
        }
        QJsonObject rj = doc.object();
        {
            LlmResponse round_usage;
            parse_usage(round_usage, rj, provider_);
            llm_tool_loop_add_usage(usage, round_usage);
            budget.note_prompt_tokens(budgeted_llm_tokens(
                round_usage.prompt_tokens, QJsonDocument(fu).toJson(QJsonDocument::Compact).size()));
            budget.note_completion_tokens(budgeted_llm_tokens(round_usage.completion_tokens, http.body.size()));
        }

        QJsonArray choices = rj["choices"].toArray();
        if (choices.isEmpty()) {
            resp.error = "No choices in tool follow-up";
            return resp;
        }

        QJsonObject msg = choices[0].toObject()["message"].toObject();
        QJsonArray tcs = msg["tool_calls"].toArray();

        if (!tcs.isEmpty()) {
            // Another round
            loop_messages.append(msg);
            // Collect the whole round before executing any of it: the calls in
            // one assistant turn are independent by construction, so they are
            // dispatched together (read-only ones concurrently, writes as
            // barriers — see detail::execute_tool_calls).
            std::vector<detail::PendingToolCall> calls;
            calls.reserve(static_cast<std::size_t>(tcs.size()));
            for (const auto& tc_val : tcs) {
                const QJsonObject tc = tc_val.toObject();
                detail::PendingToolCall c;
                c.call_id = tc["id"].toString();
                c.wire_name = tc["function"].toObject()["name"].toString();
                c.args =
                    QJsonDocument::fromJson(tc["function"].toObject()["arguments"].toString("{}").toUtf8()).object();
                // Strip "<server>__" prefix for user-visible label; decode any wire encoding.
                c.display = c.wire_name;
                const int sep = c.display.indexOf(QStringLiteral("__"));
                if (sep > 0)
                    c.display = c.display.mid(sep + 2);
                c.display.replace(QStringLiteral("-dot-"), QStringLiteral("."));

                LOG_DEBUG(kLlmToolLoopTag,
                          logf("LLM tool call started",
                               {{"tool", c.display},
                                {"round", round},
                                {"args_bytes", QJsonDocument(c.args).toJson(QJsonDocument::Compact).size()}}));
                // Progress lines are emitted here, on the loop thread, in the
                // model's order — not from the workers, whose completion order
                // is arbitrary and whose thread has no business touching the UI.
                detail::emit_tool_progress(c.display, c.args);
                calls.push_back(std::move(c));
            }

            QElapsedTimer round_clock;
            round_clock.start();
            const auto results = detail::execute_tool_calls(calls);
            std::vector<QString> result_names;
            result_names.reserve(calls.size());
            for (const auto& call : calls)
                result_names.push_back(call.display);
            const auto encoded_results = detail::encode_tool_results_for_llm(result_names, results);
            const qint64 round_ms = round_clock.elapsed();
            for (std::size_t k = 0; k < calls.size(); ++k) {
                const auto& c = calls[k];
                const auto& tr = results[k];
                // Completion record, in the model's order like the start lines.
                // A multi-call round reports its wall time (calls overlap).
                // emit_tool_done also writes the "LLM tool call finished" line.
                emit_tool_done(c.display, c.args, tr, round_ms, calls.size() > 1);
                loop_messages.append(QJsonObject{{"role", "tool"},
                                                 {"tool_call_id", c.call_id},
                                                 {"content", encoded_results[k]}});
            }
            continue;
        }

        // Final text response
        resp.content = strip_think_blocks(extract_openai_message_text(msg));

        // An announcement is not an answer. "I'll look for a script that…" with no tool
        // call ends the turn on a promise; send it back (bounded) so the model either
        // takes the step or writes the answer it has.
        if (intent_nudges < kMaxIntentNudges && looks_like_unfinished_intent(resp.content)) {
            ++intent_nudges;
            LOG_INFO(kLlmToolLoopTag, logf("Model announced a step without taking it, asking it to act",
                                           {{"provider", provider_}, {"round", round}, {"attempt", intent_nudges}}));
            loop_messages.append(QJsonObject{{"role", "assistant"}, {"content", resp.content}});
            loop_messages.append(QJsonObject{
                {"role", "user"},
                {"content", "That was an announcement, not an answer, and nothing was done. Take that step now by "
                            "calling the tool(s), or — if you already have what you need — write the complete final "
                            "answer with the actual figures. Do not describe what you are going to do."}});
            continue;
        }

        resp.success = !resp.content.isEmpty();
        // An empty final turn used to come back as a failure with NO message, which
        // the Quick Chat bubble rendered as a bare "Error: ".
        if (!resp.success)
            resp.error = "The model returned an empty reply after using tools";
        LOG_DEBUG(kLlmToolLoopTag, QString("TOOL LOOP: finished after %1 round(s) — %2 chars of text")
                                       .arg(round + 1)
                                       .arg(resp.content.length()));

        // The "final" text may actually contain text-based tool markup
        // (<minimax:tool_call>, <invoke name=…>, etc.) that the model emitted
        // in place of structured tool_calls. If so, route through the
        // text-extraction path so the markup gets executed instead of leaking
        // into the chat bubble as raw XML.
        static const QRegularExpression rx_has_text_markup(
            QStringLiteral("<\\s*/?\\s*(?:\\w+\\s*:\\s*)?tool_call\\b|<\\s*invoke\\s+name="),
            QRegularExpression::CaseInsensitiveOption);
        if (resp.success && rx_has_text_markup.match(resp.content).hasMatch()) {
            LOG_DEBUG(kLlmToolLoopTag,
                      "TOOL LOOP: final content has text-tool markup — re-routing through text-extraction");
            QString user_msg;
            for (const auto& v : loop_messages) {
                QJsonObject o = v.toObject();
                if (o["role"].toString() == "user") {
                    user_msg = o["content"].toString();
                    break; // first user msg is the original prompt
                }
            }
            auto text_result = try_extract_and_execute_text_tool_calls(resp.content, user_msg, url, headers);
            if (text_result.has_value() && (text_result->success || text_result->cancelled)) {
                resp = text_result.value();
                return resp;
            }
        }
        return resp;
    }

    // Budget exhausted. Force one final turn so the chat doesn't go silent, and
    // tell the model WHICH budget ran out — "you are out of rounds" and "you are
    // out of time" call for different summaries, and a model told only "budget"
    // tends to relitigate the plan instead of reporting what it has.
    const QString exhaustion = budget.exhaustion_note();
    LOG_WARN(kLlmToolLoopTag, "TOOL LOOP: " + exhaustion + " — forcing summary turn");
    {
        const QString summary_instruction =
            "You have used your tool budget (" + exhaustion +
            "). Reply now with a final summary of what you accomplished and what (if anything) is incomplete. "
            "Do not call or emit tools; report the result you already have.";
        // Every exhaustion reason is final. Re-sending the full transcript and
        // tool catalogue after a round/time stop can be the most expensive call
        // of the turn, and a structured tool call here has no following loop to
        // execute it. Keep the evidence bounded and the request tool-free.
        const int before = QJsonDocument(loop_messages).toJson(QJsonDocument::Compact).size();
        loop_messages = bounded_tool_summary_messages(loop_messages, summary_instruction,
                                                      detail::tool_loop_summary_context_max_bytes());
        const int after = QJsonDocument(loop_messages).toJson(QJsonDocument::Compact).size();
        LOG_INFO(kLlmToolLoopTag,
                 QString("TOOL LOOP: compacted exhausted-summary context from %1 to %2 bytes")
                     .arg(before)
                     .arg(after));

        QJsonObject fu;
        fu["model"] = model_;
        fu["messages"] = loop_messages;
        apply_openai_token_limit(fu);
        const int summary_limit = std::min(resolved_max_tokens(), detail::tool_loop_summary_max_tokens());
        if (fu.contains(QStringLiteral("max_completion_tokens")))
            fu[QStringLiteral("max_completion_tokens")] = summary_limit;
        else
            fu[QStringLiteral("max_tokens")] = summary_limit;
        if (provider_ == QLatin1String("deepseek"))
            apply_deepseek_thinking(fu, detail::t_reasoning_effort);

        auto http = blocking_post(url, fu, headers);
        LlmResponse round_usage;
        note_provider_request(round_usage);
        if (http.success) {
            auto doc = QJsonDocument::fromJson(http.body);
            if (!doc.isNull()) {
                QJsonObject rj = doc.object();
                parse_usage(round_usage, rj, provider_);
                QJsonArray choices = rj["choices"].toArray();
                if (!choices.isEmpty()) {
                    QJsonObject msg = choices[0].toObject()["message"].toObject();
                    resp.content = strip_think_blocks(extract_openai_message_text(msg));
                    resp.success = !resp.content.isEmpty();
                    LOG_DEBUG(kLlmToolLoopTag,
                              QString("TOOL LOOP: summary fallback produced %1 chars").arg(resp.content.length()));
                }
            }
        }
        llm_tool_loop_add_usage(usage, round_usage);
        if (resp.success)
            return resp;
    }
    resp.error = "Tool call loop ended without an answer — " + exhaustion;
    return resp;
}

// ============================================================================
// Text-based tool call extraction
// ============================================================================
// Models that don't support structured tool_calls (e.g. minimax, some
// OpenRouter models) may emit tool invocations as XML/text markup in their
// response. This method detects common patterns and executes the tools.

std::optional<LlmResponse> LlmService::try_extract_and_execute_text_tool_calls(const QString& content,
                                                                               const QString& user_message,
                                                                               const QString& url,
                                                                               const QMap<QString, QString>& headers) {
    if (!should_execute_text_tool_calls(detail::effective_tools_enabled(tools_enabled_)))
        return std::nullopt;

    // ── Pattern 1: <tool_call>{"name":"...", "arguments":{...}}</tool_call>
    // ── Pattern 2: <invoke name="..." args='{"key":"val"}'>
    // ── Pattern 3: minimax:tool_call  CONTENT /minimax:tool_call
    // ── Pattern 4: ```tool_call\n{"name":"...", "arguments":{...}}\n```
    // ── Pattern 5: function_name(arg1, arg2) style calls embedded in text

    LOG_DEBUG(kLlmToolLoopTag,
              "Checking for text-based tool calls in response (" + QString::number(content.length()) + " chars)");

    // Pattern matching lives in LlmContentExtractors (shared with the Fincept
    // async path, which is prompt-injected and therefore always text-mode).
    QStringList raw_blocks;
    std::vector<TextToolCall> calls = extract_text_tool_calls(content, &raw_blocks);

    // A `[ns:]tool_call` block whose body isn't JSON is a raw command (the case
    // that motivated this: the model emitting bare SQL inside minimax tags).
    // Map it onto the first external tool with a query/sql/statement parameter.
    if (calls.empty() && !raw_blocks.isEmpty()) {
        auto all_tools = mcp::McpService::instance().get_all_tools();
        for (const auto& body : raw_blocks) {
            for (const auto& tool : all_tools) {
                if (tool.is_internal)
                    continue;
                const QJsonObject props = tool.input_schema["properties"].toObject();
                bool matched = false;
                for (auto pit = props.constBegin(); pit != props.constEnd(); ++pit) {
                    const QString key = pit.key().toLower();
                    if (key == "query" || key == "sql" || key == "statement") {
                        QJsonObject args;
                        args[pit.key()] = body;
                        calls.push_back(
                            {tool.server_id + "__" + mcp::McpProvider::encode_tool_name_for_wire(tool.name), args});
                        matched = true;
                        break;
                    }
                }
                if (matched)
                    break;
            }
            if (!calls.empty())
                break;
        }
    }

    if (calls.empty())
        return std::nullopt;

    LOG_DEBUG(kLlmToolLoopTag, QString("Detected %1 text-based tool call(s) in response").arg(calls.size()));

    // Execute each detected tool call. Results are encoded only after the
    // whole batch is known so the shared per-round transcript ceiling applies;
    // a per-result cap would multiply with the number of calls.
    QString tool_results;
    std::vector<QString> result_names;
    std::vector<mcp::ToolResult> results;
    for (const auto& tc : calls) {
        if (detail::cancel_requested())
            break;
        LOG_DEBUG(kLlmToolLoopTag, "Executing text-detected tool: " + tc.name);
        int sep_disp = tc.name.indexOf(QStringLiteral("__"));
        QString display = (sep_disp >= 0) ? tc.name.mid(sep_disp + 2) : tc.name;
        display.replace(QStringLiteral("-dot-"), QStringLiteral("."));
        detail::emit_tool_progress(display, tc.args);
        QElapsedTimer call_clock;
        call_clock.start();
        auto tr = mcp::McpService::instance().execute_openai_function(tc.name, tc.args);
        emit_tool_done(display, tc.args, tr, call_clock.elapsed());
        result_names.push_back(display);
        results.push_back(std::move(tr));
    }

    if (detail::cancel_requested()) {
        // Stop pressed while the tools ran: don't spend another request summarising.
        LlmResponse cancelled_resp;
        cancelled_resp.cancelled = true;
        cancelled_resp.error = "Request cancelled";
        return cancelled_resp;
    }

    const auto encoded_results = detail::encode_tool_results_for_llm(result_names, results);
    for (std::size_t k = 0; k < encoded_results.size(); ++k)
        tool_results += "\n**Tool: " + result_names[k] + "**\n" + encoded_results[k] + "\n";

    if (tool_results.isEmpty())
        return std::nullopt;

    // Build follow-up request asking the LLM to summarize the tool results
    LlmResponse resp;
    const QString summary_instruction =
        QStringLiteral("Provide a clear, concise answer to the user's request using the tool evidence below. "
                       "Do not emit tool calls or XML markup.");
    const QJsonArray summary_evidence{
        QJsonObject{{QStringLiteral("role"), QStringLiteral("user")},
                    {QStringLiteral("content"), user_message}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("assistant")},
                    {QStringLiteral("content"), content}},
        QJsonObject{{QStringLiteral("role"), QStringLiteral("tool")},
                    {QStringLiteral("content"), tool_results}},
    };
    const QString follow_prompt =
        bounded_tool_summary_prompt(summary_evidence, summary_instruction,
                                    detail::tool_loop_summary_context_max_bytes());
    const int summary_limit = std::min(resolved_max_tokens(), detail::tool_loop_summary_max_tokens());

    QJsonObject follow_body;
    if (provider_ == "anthropic") {
        QJsonArray msgs;
        msgs.append(QJsonObject{{"role", "user"}, {"content", follow_prompt}});
        follow_body["model"] = model_;
        follow_body["messages"] = msgs;
        follow_body["max_tokens"] = summary_limit;
        // Temperature intentionally omitted — Anthropic default.
        if (!system_prompt_.isEmpty())
            follow_body["system"] = system_prompt_;
    } else if (provider_ == "gemini" || provider_ == "google") {
        // `url` here is the Gemini generateContent endpoint, so the body must
        // be Gemini-shaped. It previously fell through to the OpenAI branch
        // below and posted {model, messages, max_tokens} at :generateContent,
        // which 400s — the user then saw the raw-tool-output fallback instead
        // of an answer.
        follow_body["contents"] =
            QJsonArray{QJsonObject{{"role", "user"}, {"parts", QJsonArray{QJsonObject{{"text", follow_prompt}}}}}};
        follow_body["generationConfig"] = QJsonObject{{"maxOutputTokens", summary_limit}};
        if (!system_prompt_.isEmpty())
            follow_body["systemInstruction"] = QJsonObject{{"parts", QJsonArray{QJsonObject{{"text", system_prompt_}}}}};
    } else {
        // OpenAI-compatible. (Fincept never reaches this function: do_request
        // hands it to fincept_async_request, which runs its own text-tool loop.)
        QJsonArray msgs;
        if (!system_prompt_.isEmpty())
            msgs.append(QJsonObject{{"role", "system"}, {"content", system_prompt_}});
        msgs.append(QJsonObject{{"role", "user"}, {"content", follow_prompt}});
        follow_body["model"] = model_;
        follow_body["messages"] = msgs;
        // Temperature intentionally omitted — provider default.
        apply_openai_token_limit(follow_body);
        if (follow_body.contains(QStringLiteral("max_completion_tokens")))
            follow_body[QStringLiteral("max_completion_tokens")] = summary_limit;
        else
            follow_body[QStringLiteral("max_tokens")] = summary_limit;
        if (provider_ == QLatin1String("deepseek"))
            apply_deepseek_thinking(follow_body, detail::t_reasoning_effort);
    }

    // Last-resort rendering when the summarisation turn can't be obtained.
    // Raw tool JSON is NOT an answer — label it so the user can tell the
    // difference, and log the reason instead of silently passing it off as one.
    auto raw_fallback = [&](const QString& reason) -> LlmResponse {
        const QString safe_reason = privacy::redact_text(reason).left(240);
        LOG_WARN(kLlmToolLoopTag,
                 "Text-tool follow-up unavailable (" + safe_reason + ") — returning raw tool output");
        resp.content = "*(could not summarise — " + safe_reason + ". Raw tool output below.)*\n" + tool_results;
        resp.success = true;
        return resp;
    };

    // No tools in follow-up to prevent infinite loop
    auto fu = blocking_post(url, follow_body, headers);
    note_provider_request(resp);
    if (!fu.success)
        return raw_fallback(fu.error.isEmpty() ? QStringLiteral("follow-up request failed") : fu.error);

    auto fu_doc = QJsonDocument::fromJson(fu.body);
    if (fu_doc.isNull())
        return raw_fallback(QStringLiteral("follow-up response was not JSON"));

    QJsonObject fu_rj = fu_doc.object();

    // Extract text from follow-up response (provider-aware)
    if (provider_ == "anthropic") {
        resp.content = extract_anthropic_content_text(fu_rj["content"].toArray());
    } else if (provider_ == "gemini" || provider_ == "google") {
        const QJsonArray cands = fu_rj["candidates"].toArray();
        if (!cands.isEmpty())
            resp.content = extract_gemini_parts_text(cands[0].toObject()["content"].toObject()["parts"].toArray());
    } else {
        QJsonArray choices = fu_rj["choices"].toArray();
        if (!choices.isEmpty())
            resp.content = extract_openai_message_text(choices[0].toObject()["message"].toObject());
    }

    if (resp.content.isEmpty())
        return raw_fallback(QStringLiteral("follow-up returned no text"));

    resp.success = true;
    parse_usage(resp, fu_rj, provider_);
    return resp;
}

} // namespace fincept::ai_chat
