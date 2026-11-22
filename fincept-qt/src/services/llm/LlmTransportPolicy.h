#pragma once

namespace fincept::ai_chat {

/// Structured tool calls cannot be continued from the current SSE parser: it
/// would have to abort the streamed generation and resend the whole turn.
/// Route tool-enabled turns through the bounded non-streaming loop from the
/// start so one user action produces only one initial provider request.
inline bool should_stream_initial_request(bool tools_enabled) {
    return !tools_enabled;
}

} // namespace fincept::ai_chat
