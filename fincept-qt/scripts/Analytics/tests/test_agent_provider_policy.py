"""The bundled agents must default to user-owned DeepSeek, never legacy backends."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE = Path(__file__).resolve().parents[2] / "agents" / "finagent_core" / "provider_policy.py"
DEEPAGENT_MODELS = Path(__file__).resolve().parents[2] / "agents" / "deepagents" / "models.py"
MODEL_REGISTRY = MODULE.parent / "registries" / "models_registry.py"
EMBEDDER_REGISTRY = MODULE.parent / "registries" / "embedder_registry.py"
FINCEPT_MODEL = MODULE.parent / "registries" / "fincept_model.py"
SUPER_AGENT = MODULE.parent / "super_agent.py"
CORE_AGENT = MODULE.parent / "core_agent.py"
AGENT_SERVICE = MODULE.parents[3] / "src" / "services" / "agents" / "AgentService.cpp"
LLM_SERVICE_HEADER = MODULE.parents[3] / "src" / "services" / "llm" / "LlmService.h"
LOCAL_AGENT_ENGINE = MODULE.parents[3] / "src" / "services" / "agent_tasks" / "LocalAgentEngine.cpp"
RDAGENT_CLI = MODULE.parent.parent / "rdagents" / "cli.py"
RDAGENT_LOOPS = RDAGENT_CLI.with_name("loops.py")


def load_module():
    if not MODULE.exists():
        raise AssertionError(f"agent provider policy is missing: {MODULE}")
    spec = importlib.util.spec_from_file_location("finagent_provider_policy", MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_deepagent_models():
    spec = importlib.util.spec_from_file_location("fincept_deepagent_models", DEEPAGENT_MODELS)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class AgentProviderPolicyTest(unittest.TestCase):
    def test_deepseek_wins_over_legacy_and_other_keys(self):
        policy = load_module()
        keys = {"fincept": "hosted", "OLLAMA_API_KEY": "local", "ANTHROPIC_API_KEY": "claude",
                "DEEPSEEK_API_KEY": "deepseek"}
        self.assertEqual(policy.resolve_byo_provider(keys), "deepseek")
        self.assertEqual(policy.resolve_byo_model_config(keys), {"provider": "deepseek"})

    def test_legacy_only_keys_fail_toward_visible_deepseek_setup(self):
        policy = load_module()
        keys = {"FINCEPT_API_KEY": "hosted", "OLLAMA_API_KEY": "local", "CODEX_API_KEY": "cli"}
        self.assertEqual(policy.resolve_byo_provider(keys), "deepseek")

    def test_other_user_owned_provider_remains_a_fallback(self):
        policy = load_module()
        keys = {"MISTRAL_API_KEY": "mistral", "MISTRAL_BASE_URL": "https://mistral.example/v1"}
        self.assertEqual(policy.resolve_byo_model_config(keys), {
            "provider": "mistral", "base_url": "https://mistral.example/v1",
        })

    def test_explicit_legacy_model_config_is_migrated_to_deepseek(self):
        policy = load_module()
        configured = {"provider": "ollama", "model_id": "hermes-aux:latest",
                      "base_url": "http://localhost:11434", "temperature": 0.2}
        self.assertEqual(
            policy.normalize_byo_model_config(configured, {"DEEPSEEK_API_KEY": "owned"}),
            {"provider": "deepseek", "temperature": 0.2},
        )

    def test_explicit_user_owned_model_config_is_preserved(self):
        policy = load_module()
        configured = {"provider": "anthropic", "model_id": "claude-sonnet", "temperature": 0.1}
        self.assertEqual(policy.normalize_byo_model_config(configured, {}), configured)

    def test_super_agent_normalizes_saved_legacy_model_before_llm_routing(self):
        source = SUPER_AGENT.read_text(encoding="utf-8")
        init = source[source.index("class SuperAgent") : source.index("def _setup_default_routes")]
        execute = source[source.index("def execute(") : source.index("def execute_multi(")]

        self.assertIn("normalize_byo_model_config", init)
        self.assertIn("normalize_byo_model_config", execute)
        self.assertLess(init.index("normalize_byo_model_config"), init.index("LLMRouter("))
        self.assertLess(execute.index("normalize_byo_model_config"),
                        execute.index("self.llm_router.model_config"))

    def test_separate_reasoning_model_cannot_restore_a_retired_provider(self):
        source = CORE_AGENT.read_text(encoding="utf-8")
        reasoning = source[source.index("# 7. Reasoning") : source.index("# 8. Structured Outputs")]

        self.assertIn("normalize_byo_model_config", reasoning)
        self.assertLess(reasoning.index("normalize_byo_model_config"),
                        reasoning.index("ModelsRegistry.create_model"))

    def test_legacy_ollama_embedder_moves_to_local_sentence_transformers(self):
        policy = load_module()
        self.assertEqual(policy.normalize_embedder_config("ollama", "nomic-embed-text"),
                         ("sentence_transformer", None))
        self.assertEqual(policy.normalize_embedder_config("mistral", "mistral-embed"),
                         ("mistral", "mistral-embed"))

    def test_local_knowledge_embedder_is_installed_with_the_agent_runtime(self):
        requirements = MODULE.parents[3] / "resources" / "requirements-numpy2.txt"
        text = requirements.read_text(encoding="utf-8").lower()
        self.assertIn("sentence-transformers", text)

    def test_deep_agents_reject_legacy_or_metered_backends_before_execution(self):
        models = load_deepagent_models()
        for provider in ("fincept", "ollama", "codex", "harness:codex"):
            error = models.provider_policy_error({"llm_provider": provider})
            self.assertIn("DeepSeek", error)
        self.assertIsNone(models.provider_policy_error({"llm_provider": "deepseek"}))

        cli = (DEEPAGENT_MODELS.parent / "cli.py").read_text(encoding="utf-8")
        execute = cli[cli.index("def cmd_execute_task"):cli.index("def _execute_with_deepagents")]
        self.assertIn("provider_policy_error", execute)
        self.assertNotIn("_execute_with_orchestrator", execute)

    def test_agent_registries_cannot_construct_retired_model_or_embedder_backends(self):
        models = load_path("finagent_models_registry", MODEL_REGISTRY).ModelsRegistry
        embedders = load_path("finagent_embedder_registry", EMBEDDER_REGISTRY).EmbedderRegistry

        for provider in ("fincept", "ollama", "codex"):
            self.assertNotIn(provider, models.MODEL_CATALOG)
            with self.assertRaisesRegex(ValueError, "disabled"):
                models.create_model(provider)

        deepseek = models.MODEL_CATALOG["deepseek"]
        self.assertEqual(deepseek["default_model"], "deepseek-flash")
        self.assertIn("deepseek-v4-pro", deepseek["models"])
        self.assertNotIn("deepseek-chat", deepseek["models"])
        self.assertNotIn("deepseek-reasoner", deepseek["models"])

        self.assertNotIn("ollama", embedders.EMBEDDER_CATALOG)
        with self.assertRaises(ValueError):
            embedders.create_embedder("ollama")

        retired_adapter = FINCEPT_MODEL.read_text(encoding="utf-8")
        self.assertNotIn("api.fincept.in", retired_adapter)
        self.assertNotIn("import httpx", retired_adapter)

    def test_rd_agent_rejects_retired_providers_before_applying_llm_config(self):
        cli = load_path("finagent_rdagent_cli", RDAGENT_CLI)
        check = getattr(cli, "_provider_policy_error", None)
        self.assertIsNotNone(check)
        for provider in ("fincept", "ollama", "codex", "harness:codex"):
            self.assertIn("DeepSeek", check({"llm_provider": provider}))
        self.assertIsNone(check({"llm_provider": "deepseek"}))

        source = RDAGENT_CLI.read_text(encoding="utf-8")
        run = source[source.index("def cmd_run_task") : source.index("def cmd_start_mcp_server")]
        self.assertLess(run.index("_provider_policy_error"), run.index("apply_llm_config"))

        loops = RDAGENT_LOOPS.read_text(encoding="utf-8")
        preflight = loops[loops.index("def preflight") : loops.index("def ", loops.index("def preflight") + 4)]
        self.assertIn("DEPRECATED_PROVIDERS", preflight)

    def test_agent_payload_uses_one_resolved_api_config_not_mixed_active_fields(self):
        service = AGENT_SERVICE.read_text(encoding="utf-8")
        payload = service[service.index("QJsonObject AgentService::build_payload") :
                          service.index("// Resolve user_id", service.index("QJsonObject AgentService::build_payload"))]
        header = LLM_SERVICE_HEADER.read_text(encoding="utf-8")

        self.assertIn("resolved_config_json", header)
        self.assertIn("resolved_config_json", payload)
        for stale_accessor in ("active_provider()", "active_model()", "active_api_key()", "active_base_url()"):
            self.assertNotIn(stale_accessor, payload)
        self.assertIn("kFeatureAgent", payload)

    def test_saved_agent_model_cannot_restore_a_retired_or_metered_backend(self):
        service = AGENT_SERVICE.read_text(encoding="utf-8")
        payload = service[service.index("QJsonObject AgentService::build_payload") :
                          service.index("// Resolve user_id", service.index("QJsonObject AgentService::build_payload"))]

        explicit_model = payload[payload.index("const QJsonObject configured_model") :
                                 payload.index("resolved_config_json")]
        self.assertIn("is_deprecated_llm_provider", explicit_model)
        self.assertIn("fincept_metered_services_enabled", explicit_model)
        self.assertIn("provider_is_harness", explicit_model)

    def test_local_agent_readiness_checks_the_agent_route_not_ai_chat(self):
        source = LOCAL_AGENT_ENGINE.read_text(encoding="utf-8")
        readiness = source[source.index("LocalAgentReadiness readiness() const override") :
                           source.index("QString start_task", source.index("LocalAgentReadiness readiness() const override"))]

        self.assertIn("kFeatureAgent", readiness)
        self.assertIn("resolved_config_json", readiness)
        self.assertNotIn("llm.is_configured()", readiness)
        self.assertNotIn("llm.active_provider()", readiness)
        self.assertNotIn("llm.active_model()", readiness)
        self.assertNotIn("cloud engine", readiness)
        self.assertIn("DeepSeek", readiness)


if __name__ == "__main__":
    unittest.main()
