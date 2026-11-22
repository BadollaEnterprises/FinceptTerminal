#pragma once

namespace fincept::workflow {
class NodeRegistry;

/// The picker classifies a request for a downstream MCP node. Executing tools
/// during classification would duplicate the downstream side effect.
inline constexpr bool tool_picker_request_uses_tools() {
    return false;
}

void register_agent_nodes(NodeRegistry& registry);
} // namespace fincept::workflow
