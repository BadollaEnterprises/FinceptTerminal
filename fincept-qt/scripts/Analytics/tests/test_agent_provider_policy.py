"""The bundled agents must default to user-owned DeepSeek, never legacy backends."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE = Path(__file__).resolve().parents[2] / "agents" / "finagent_core" / "provider_policy.py"
DEEPAGENT_MODELS = Path(__file__).resolve().parents[2] / "agents" / "deepagents" / "models.py"
MODEL_REGISTRY = MODULE.parent / "registries" / "models_registry.py"
EMBEDDER_REGISTRY = MODULE.parent / "registries" / "embedder_registry.py"
FINCEPT_MODEL = MODULE.parent / "registries" / "fincept_model.py"
SUPER_AGENT = MODULE.parent / "super_agent.py"
CORE_AGENT = MODULE.parent / "core_agent.py"
AGENT_SERVICE = MODULE.parents[3] / "src" / "services" / "agents" / "AgentService.cpp"
AGENT_SERVICE_HEADER = AGENT_SERVICE.with_suffix(".h")
AGENT_SERVICE_DISCOVERY = AGENT_SERVICE.with_name("AgentService_Discovery.cpp")
LLM_SERVICE_HEADER = MODULE.parents[3] / "src" / "services" / "llm" / "LlmService.h"
LLM_SERVICE_SOURCE = LLM_SERVICE_HEADER.with_suffix(".cpp")
AGENT_MAIN = MODULE.with_name("main.py")
AGENT_BUDGET = MODULE.parent / "agentic" / "budget.py"
LOCAL_AGENT_ENGINE = MODULE.parents[3] / "src" / "services" / "agent_tasks" / "LocalAgentEngine.cpp"
AGENTS_PANEL = MODULE.parents[3] / "src" / "screens" / "agent_config" / "AgentsViewPanel.cpp"
TEAMS_PANEL = MODULE.parents[3] / "src" / "screens" / "agent_config" / "TeamsViewPanel.cpp"
AGENT_NODES = MODULE.parents[3] / "src" / "services" / "workflow" / "nodes" / "AgentNodes.cpp"
CREATE_AGENT = MODULE.parents[3] / "src" / "screens" / "agent_config" / "CreateAgentPanel.cpp"
CREATE_AGENT_DATA = CREATE_AGENT.with_name("CreateAgentPanel_DataOps.cpp")
WORKFLOWS_PANEL = CREATE_AGENT.with_name("WorkflowsViewPanel.cpp")
PLANNER_PANEL = CREATE_AGENT.with_name("PlannerViewPanel.cpp")
AGENT_CHAT_PANEL = CREATE_AGENT.with_name("AgentChatPanel.cpp")
AGENT_PROFILE_ROWS = CREATE_AGENT.with_name("AgentProfileRows.h")
LLM_PROFILE_HEADER = MODULE.parents[3] / "src" / "storage" / "repositories" / "LlmProfileRepository.h"
RDAGENT_CLI = MODULE.parent.parent / "rdagents" / "cli.py"
RDAGENT_LOOPS = RDAGENT_CLI.with_name("loops.py")
RDAGENT_CONFIG = RDAGENT_CLI.with_name("config.py")


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


def load_agent_main():
    return load_path("finagent_main_budget_test", AGENT_MAIN)


class AgentProviderPolicyTest(unittest.TestCase):
    def test_agent_requests_share_the_terminal_input_budget(self):
        main = load_agent_main()
        self.assertIsNone(main._input_budget_error({"query": "short"}, {}, 48_000))
        self.assertIsNone(main._input_budget_error({"query": "x" * 50_000}, {}, 0))
        self.assertIn(
            "No provider request was sent",
            main._input_budget_error({"query": "x" * 48_001}, {}, 48_000),
        )
        self.assertIn(
            "No provider request was sent",
            main._input_budget_error(
                {"query": "x" * 30_000}, {"instructions": ["y" * 20_000]}, 48_000
            ),
        )

        service = AGENT_SERVICE.read_text(encoding="utf-8")
        payload = service[service.index("QJsonObject AgentService::build_payload"):]
        self.assertIn('payload["input_max_chars"]', payload)
        self.assertIn("kLlmHistoryCharsKey", payload)

    def test_agentic_tasks_have_cost_conscious_implicit_budgets(self):
        budget = load_path("finagent_budget_defaults", AGENT_BUDGET)
        guard = budget.BudgetGuard.from_config({"model": {"model_id": "deepseek-chat"}})
        self.assertEqual(guard.max_tokens, 250_000)
        self.assertEqual(guard.max_cost_usd, 1.0)
        self.assertEqual(guard.max_wall_s, 900)
        self.assertEqual(guard.max_steps, 20)

    def test_agentic_task_budgets_reject_non_finite_and_bound_extremes(self):
        budget = load_path("finagent_budget_bounds", AGENT_BUDGET)
        invalid = budget.BudgetGuard.from_config({"budget": {
            "max_tokens": -1,
            "max_cost_usd": "nan",
            "max_wall_s": "invalid",
            "max_steps": 0,
        }})
        self.assertEqual(invalid.max_tokens, budget.DEFAULT_MAX_TOKENS)
        self.assertEqual(invalid.max_cost_usd, budget.DEFAULT_MAX_COST_USD)
        self.assertEqual(invalid.max_wall_s, budget.DEFAULT_MAX_WALL_S)
        self.assertEqual(invalid.max_steps, budget.DEFAULT_MAX_STEPS)
        malformed = budget.BudgetGuard.from_config({"budget": [], "model": "bad"})
        self.assertEqual(malformed.max_tokens, budget.DEFAULT_MAX_TOKENS)

        bounded = budget.BudgetGuard.from_config({"budget": {
            "max_tokens": 10**12,
            "max_cost_usd": "inf",
            "max_wall_s": 10**12,
            "max_steps": 10**12,
        }})
        self.assertEqual(bounded.max_tokens, budget.MAX_MAX_TOKENS)
        self.assertEqual(bounded.max_cost_usd, budget.DEFAULT_MAX_COST_USD)
        self.assertEqual(bounded.max_wall_s, budget.MAX_MAX_WALL_S)
        self.assertEqual(bounded.max_steps, budget.MAX_MAX_STEPS)

    def test_agentic_usage_metrics_cannot_reduce_spend(self):
        budget = load_path("finagent_budget_normalization", AGENT_BUDGET)
        guard = budget.BudgetGuard.from_config({"model": {"model_id": "deepseek-chat"}})

        class Response:
            metrics = {"input_tokens": -500, "output_tokens": -20}

        guard.tokens_used = 100
        guard.cost_used = 0.25
        guard.record_llm_call("prompt", Response())
        self.assertGreaterEqual(guard.tokens_used, 100)
        self.assertGreaterEqual(guard.cost_used, 0.25)

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

    def test_routing_configuration_errors_block_agent_provider_inference(self):
        policy = load_module()
        marker = {"configuration_error": "Could not read LLM model assignments"}
        self.assertEqual(policy.resolved_configuration_error(marker), marker["configuration_error"])
        self.assertIsNone(policy.resolved_configuration_error({"provider": "deepseek"}))

        service = LLM_SERVICE_SOURCE.read_text(encoding="utf-8")
        resolved = service[service.index("QJsonObject LlmService::resolved_config_json"):
                           service.index("int LlmService::active_max_tool_rounds")]
        self.assertIn('out[QStringLiteral("configuration_error")]', resolved)
        self.assertIn("privacy::redact_text(cfg.config_error)", resolved)

        main = AGENT_MAIN.read_text(encoding="utf-8")
        parsed = main[main.index('active_llm = payload.get("active_llm", {})'):
                      main.index("# Enable streaming in params")]
        self.assertIn("resolved_configuration_error(active_llm)", parsed)
        self.assertLess(parsed.index("resolved_configuration_error(active_llm)"),
                        parsed.index("if active_llm:"))

    def test_explicit_profile_read_errors_cannot_fall_back_to_another_provider(self):
        service = AGENT_SERVICE.read_text(encoding="utf-8")
        expand = service[service.index("QJsonObject agent_svc_expand_config"):
                         service.index("QString agent_svc_run_token")]
        self.assertIn("!resolved.config_error.isEmpty()", expand)
        self.assertIn("LlmService::profile_to_json(resolved)", expand)

        selected = service[service.index("QJsonObject AgentService::model_config_for_profile"):
                           service.index("void AgentService::run_python_light")]
        self.assertIn("resolve_profile_id(profile_id)", selected)
        self.assertIn("resolved.config_error", selected)
        self.assertLess(selected.index("resolved.config_error"), selected.index("resolve_profile(context_type"))

        payload = service[service.index("QJsonObject AgentService::build_payload"):
                          service.index("// Resolve user_id")]
        self.assertIn('configured_model.value(QStringLiteral("configuration_error"))', payload)
        self.assertLess(payload.index("configured_configuration_error"),
                        payload.index("configured_model_allowed"))
        self.assertLess(payload.index("!configured_configuration_error.isEmpty()"),
                        payload.index("if (configured_model_allowed)"))

    def test_agent_execution_surfaces_selected_profile_read_errors(self):
        agents = AGENTS_PANEL.read_text(encoding="utf-8")
        build_start = agents.index("QJsonObject AgentsViewPanel::build_config_from_editor")
        build = agents[build_start:agents.index("// ── Actions", build_start)]
        self.assertIn("resolve_profile_id(profile_id)", build)
        self.assertIn("resolved.config_error", build)
        self.assertIn("resolved.config_error.isEmpty()", build)

        nodes = AGENT_NODES.read_text(encoding="utf-8")
        resolve = nodes[nodes.index("// Resolve LLM profile"):
                        nodes.index("// Build config", nodes.index("// Resolve LLM profile"))]
        self.assertIn("resolve_profile_id(profile_id)", resolve)
        self.assertIn("resolved.config_error.isEmpty()", resolve)

        teams = TEAMS_PANEL.read_text(encoding="utf-8")
        run = teams[teams.index("void TeamsViewPanel::run_team"):
                    teams.index("void TeamsViewPanel::", teams.index("void TeamsViewPanel::run_team") + 1)]
        self.assertIn("resolve_profile_id(coord_profile_id)", run)
        self.assertIn("coord.config_error.isEmpty()", run)
        self.assertIn("member_profile.config_error.isEmpty()", run)

    def test_agent_profile_labels_and_editor_use_checked_profile_resolution(self):
        for path, profile_var in (
            (AGENTS_PANEL, "profile_id"),
            (TEAMS_PANEL, "profile_id"),
            (CREATE_AGENT, "profile_id"),
            (CREATE_AGENT_DATA, "profile_id"),
            (WORKFLOWS_PANEL, "pid"),
        ):
            with self.subTest(source=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn(f"resolve_profile_id({profile_var})", source)
                self.assertIn("config_error", source)

        data = CREATE_AGENT_DATA.read_text(encoding="utf-8")
        self.assertIn("resolved.provider.isEmpty() && resolved.config_error.isEmpty()", data)

    def test_agent_config_and_profile_assignment_are_saved_atomically(self):
        header = AGENT_SERVICE_HEADER.read_text(encoding="utf-8")
        self.assertIn("Result<void> save_config(", header)
        self.assertIn("std::optional<QString> profile_id", header)

        service = AGENT_SERVICE_DISCOVERY.read_text(encoding="utf-8")
        save = service[service.index("Result<void> AgentService::save_config"):
                       service.index("AgentService::delete_config")]
        self.assertIn("begin_transaction()", save)
        self.assertIn("rollback()", save)
        self.assertIn("commit()", save)
        self.assertIn("assign_profile", save)
        self.assertIn("remove_assignment", save)
        self.assertLess(save.index("begin_transaction()"), save.index("AgentConfigRepository::instance().save"))
        self.assertLess(save.index("AgentConfigRepository::instance().save"), save.index("commit()"))

        create = CREATE_AGENT_DATA.read_text(encoding="utf-8")
        create_save = create[create.index("void CreateAgentPanel::save_agent"):
                             create.index("void CreateAgentPanel::delete_agent")]
        self.assertIn("save_config(db, profile_id)", create_save)
        self.assertIn("if (saved.is_err())", create_save)
        self.assertLess(create_save.index("if (saved.is_err())"), create_save.index("editing_id_ = db.id"))
        self.assertNotIn("repo.assign_profile", create_save)

        agents = AGENTS_PANEL.read_text(encoding="utf-8")
        panel_save = agents[agents.index("void AgentsViewPanel::save_current_config"):
                            agents.index("void AgentsViewPanel::delete_current_config")]
        self.assertIn("save_config(db, profile_id)", panel_save)
        self.assertIn("if (saved.is_err())", panel_save)
        self.assertNotIn("repo.assign_profile", panel_save)
        self.assertNotIn("repo.remove_assignment", panel_save)

    def test_agent_delete_removes_profile_assignment_atomically(self):
        header = AGENT_SERVICE_HEADER.read_text(encoding="utf-8")
        self.assertIn("Result<void> delete_config(const QString& id)", header)

        service = AGENT_SERVICE_DISCOVERY.read_text(encoding="utf-8")
        start = service.index("AgentService::delete_config")
        delete = service[service.rfind("Result<void>", 0, start):service.index("// ── Structured output", start)]
        self.assertIn("begin_transaction()", delete)
        self.assertIn("remove_assignment", delete)
        self.assertIn("AgentConfigRepository::instance().remove", delete)
        self.assertIn("rollback()", delete)
        self.assertIn("commit()", delete)
        self.assertLess(delete.index("remove_assignment"), delete.index("AgentConfigRepository::instance().remove"))

        for path, method, end in (
            (CREATE_AGENT_DATA, "void CreateAgentPanel::delete_agent", "void CreateAgentPanel::test_agent"),
            (AGENTS_PANEL, "void AgentsViewPanel::delete_current_config", "void AgentsViewPanel::run_query"),
        ):
            with self.subTest(source=path.name):
                source = path.read_text(encoding="utf-8")
                body = source[source.index(method):source.index(end)]
                self.assertIn("const auto deleted = services::AgentService::instance().delete_config", body)
                self.assertIn("if (deleted.is_err())", body)

    def test_agent_assignment_reads_fail_closed_in_editors_and_chat(self):
        header = LLM_PROFILE_HEADER.read_text(encoding="utf-8")
        self.assertIn("Result<QString> read_assignment", header)
        public_assignment = header[header.index("// ── Assignment CRUD"):
                                   header.index("// ── Resolution")]
        self.assertIn("Result<QString> read_assignment", public_assignment)
        self.assertNotIn("QString get_assignment", header)

        create = CREATE_AGENT_DATA.read_text(encoding="utf-8")
        load_create = create[create.index("void CreateAgentPanel::load_agent_into_form"):
                             create.index("void CreateAgentPanel::start_new_agent")]
        save_create = create[create.index("void CreateAgentPanel::save_agent"):
                             create.index("void CreateAgentPanel::delete_agent")]
        self.assertIn("read_assignment", load_create)
        self.assertIn("assignment.is_err()", load_create)
        self.assertIn("select_idx = -1", load_create)
        self.assertIn("llm_profile_combo_->currentIndex() < 0", save_create)

        agents = AGENTS_PANEL.read_text(encoding="utf-8")
        load_agent = agents[agents.index("void AgentsViewPanel::load_agent_into_editor"):
                            agents.index("QJsonObject AgentsViewPanel::build_config_from_editor")]
        save_agent = agents[agents.index("void AgentsViewPanel::save_current_config"):
                            agents.index("void AgentsViewPanel::delete_current_config")]
        self.assertIn("read_assignment", load_agent)
        self.assertIn("assignment.is_err()", load_agent)
        self.assertIn("select_idx = -1", load_agent)
        self.assertIn("llm_profile_combo_->currentIndex() < 0", save_agent)

        chat = AGENT_CHAT_PANEL.read_text(encoding="utf-8")
        model = chat[chat.index("AgentChatPanel::AnsweringModel AgentChatPanel::answering_model"):
                     chat.index("void AgentChatPanel::set_model_pill_style")]
        self.assertIn("r.config_error", model)
        self.assertLess(model.index("r.config_error"), model.index("r.provider.isEmpty()"))
        self.assertGreaterEqual(model.count("read_assignment"), 2)
        self.assertGreaterEqual(model.count("is_err()"), 2)
        self.assertNotIn("get_assignment", model)

    def test_agent_profile_pickers_filter_retired_rows_and_surface_read_errors(self):
        rows = AGENT_PROFILE_ROWS.read_text(encoding="utf-8")
        add = rows[rows.index("inline ", rows.index("/// Appends")):
                   rows.index("inline bool is_selectable_row")]
        self.assertIn("inline QString add_profile_rows", add)
        self.assertIn("list_profiles()", add)
        self.assertIn("list_harness_profiles()", add)
        self.assertIn("usable_result.is_err()", add)
        self.assertIn("harness_result.is_err()", add)
        self.assertIn("combo->setEnabled(false)", add)
        self.assertIn("combo->setCurrentIndex(-1)", add)
        self.assertIn("profile_rows_unavailable", add)
        self.assertNotIn("(p.is_harness() ? harness : usable).append(p)", add)

        for path, loader in (
            (AGENTS_PANEL, "void AgentsViewPanel::load_profile_combo"),
            (CREATE_AGENT, "void CreateAgentPanel::load_profile_combo"),
            (TEAMS_PANEL, "void TeamsViewPanel::load_team_profile_combo"),
        ):
            with self.subTest(source=path.name):
                source = path.read_text(encoding="utf-8")
                start = source.index(loader)
                body = source[start:source.index("\nvoid ", start + len(loader))]
                self.assertIn("const QString profile_error", body)
                self.assertIn("agent_profiles::add_profile_rows", body)
                self.assertIn("profile_error.isEmpty() ? 0 : -1", body)

        teams = TEAMS_PANEL.read_text(encoding="utf-8")
        for method, end in (("void TeamsViewPanel::run_team", "void TeamsViewPanel::save_team"),
                            ("void TeamsViewPanel::save_team", "void TeamsViewPanel::load_team")):
            start = teams.index(method)
            body = teams[start:teams.index(end, start + len(method))]
            self.assertIn("team_profile_combo_->currentIndex() < 0", body)

        workflows = WORKFLOWS_PANEL.read_text(encoding="utf-8")
        run = workflows[workflows.index("void WorkflowsViewPanel::run_current_workflow"):
                        workflows.index("void WorkflowsViewPanel::showEvent")]
        self.assertIn("llm_profile_combo_->currentIndex() < 0", run)

        planner = PLANNER_PANEL.read_text(encoding="utf-8")
        generate = planner[planner.index("void PlannerViewPanel::generate_plan"):
                           planner.index("void PlannerViewPanel::execute_plan")]
        execute = planner[planner.index("void PlannerViewPanel::execute_plan"):
                          planner.index("void PlannerViewPanel::populate_plan")]
        self.assertIn("llm_profile_combo_->currentIndex() < 0", generate)
        self.assertIn("llm_profile_combo_->currentIndex() < 0", execute)

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

    def test_deep_agents_reject_insecure_or_blocked_key_endpoints(self):
        models = load_deepagent_models()
        self.assertIsNone(models.provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "https://proxy.example/v1",
        }))
        self.assertIsNone(models.provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "http://127.0.0.2:8080/v1",
        }))
        self.assertIsNotNone(models.provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "http://proxy.example/v1",
        }))
        self.assertIsNotNone(models.provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "https://api.atlascloud.ai/v1",
        }))
        with self.assertRaisesRegex(ValueError, "insecure or blocked"):
            models.create_model({
                "llm_provider": "deepseek",
                "llm_api_key": "owned",
                "llm_base_url": "http://proxy.example/v1",
            })

    def test_deep_agents_apply_bounded_output_tokens_to_every_adapter(self):
        models = load_deepagent_models()

        class FakeModel:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        cases = [
            ("deepseek", "langchain_openai", "ChatOpenAI", "max_tokens"),
            ("openai", "langchain_openai", "ChatOpenAI", "max_tokens"),
            ("anthropic", "langchain_anthropic", "ChatAnthropic", "max_tokens"),
            ("google", "langchain_google_genai", "ChatGoogleGenerativeAI", "max_output_tokens"),
            ("groq", "langchain_groq", "ChatGroq", "max_tokens"),
        ]
        for provider, module_name, class_name, limit_key in cases:
            fake_module = types.ModuleType(module_name)
            setattr(fake_module, class_name, FakeModel)
            config = {
                "llm_provider": provider,
                "llm_api_key": "owned",
                "llm_model": "model",
                "llm_max_tokens": 777,
            }
            with self.subTest(provider=provider), mock.patch.dict(sys.modules, {module_name: fake_module}):
                model = models.create_model(config)
                self.assertEqual(model.kwargs[limit_key], 777)

    def test_deep_agents_reject_adapters_without_an_enforceable_output_ceiling(self):
        models = load_deepagent_models()
        self.assertFalse(models.supports_tool_calling({"llm_provider": "cohere"}))
        with self.assertRaisesRegex(ValueError, "Unknown llm_provider 'cohere'"):
            models.create_model({
                "llm_provider": "cohere",
                "llm_api_key": "owned",
                "llm_model": "command-r-plus",
                "llm_max_tokens": 777,
            })

    def test_deep_agent_cost_limits_have_safe_defaults_and_bounds(self):
        models = load_deepagent_models()
        self.assertEqual(models.output_token_limit({}), 4096)
        self.assertEqual(models.output_token_limit({"llm_max_tokens": 0}), 4096)
        self.assertEqual(models.output_token_limit({"llm_max_tokens": 3000000}), 2000000)
        self.assertEqual(models.recursion_limit({}), 64)
        self.assertEqual(models.recursion_limit({"recursion_limit": 1}), 8)
        self.assertEqual(models.recursion_limit({"recursion_limit": 1000}), 200)

        cli = (DEEPAGENT_MODELS.parent / "cli.py").read_text(encoding="utf-8")
        execute = cli[cli.index("def _execute_with_deepagents") : cli.index("def _format_result")]
        self.assertIn('"recursion_limit": recursion_limit(config)', execute)
        self.assertNotIn('"recursion_limit": 1000', execute)

    def test_ai_quant_lab_forwards_profile_output_limit_to_deep_agents(self):
        service = (MODULE.parents[3] / "src/services/ai_quant_lab/AIQuantLabService.cpp").read_text(encoding="utf-8")
        panel = (MODULE.parents[3] / "src/screens/ai_quant_lab/QuantModulePanel.cpp").read_text(encoding="utf-8")
        resolver = service[service.index("QJsonObject AIQuantLabService::agent_llm_config") :]
        picker = panel[panel.index("QJsonObject QuantModulePanel::llm_config_from_combo") :]
        self.assertIn('QStringLiteral("llm_max_tokens")', resolver[:7000])
        self.assertIn('cfg["llm_max_tokens"]', picker[:1800])

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

    def test_agent_model_registry_applies_a_bounded_output_allowance(self):
        models = load_path("finagent_models_registry_limits", MODEL_REGISTRY).ModelsRegistry
        self.assertEqual(models.output_token_limit(None), 4096)
        self.assertEqual(models.output_token_limit(0), 4096)
        self.assertEqual(models.output_token_limit("bad"), 4096)
        self.assertEqual(models.output_token_limit(777), 777)
        self.assertEqual(models.output_token_limit(10**12), 2_000_000)

        source = MODEL_REGISTRY.read_text(encoding="utf-8")
        create = source[source.index("def create_model(") :]
        self.assertIn('max_tok = cls.output_token_limit(kwargs.pop("max_tokens", None))', create)

    def test_agent_model_registry_rejects_insecure_or_blocked_key_endpoints(self):
        models = load_path("finagent_models_registry_endpoints", MODEL_REGISTRY).ModelsRegistry
        self.assertTrue(models.endpoint_allowed("deepseek", "https://proxy.example/v1"))
        self.assertTrue(models.endpoint_allowed("deepseek", "http://localhost:8080/v1"))
        self.assertTrue(models.endpoint_allowed("deepseek", "http://127.9.8.7:8080/v1"))
        self.assertFalse(models.endpoint_allowed("deepseek", "http://proxy.example/v1"))
        self.assertFalse(models.endpoint_allowed("deepseek", "ftp://proxy.example/v1"))
        self.assertFalse(models.endpoint_allowed("deepseek", "https://api.atlascloud.ai/v1"))
        self.assertFalse(models.endpoint_allowed("atlascloud", "https://proxy.example/v1"))
        with self.assertRaisesRegex(ValueError, "insecure or blocked"):
            models.create_model("deepseek", api_key="owned", base_url="http://proxy.example/v1")

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

    def test_rd_agent_bounds_iterations_and_protects_key_endpoints(self):
        cli = load_path("finagent_rdagent_limits", RDAGENT_CLI)
        self.assertEqual(cli._bounded_iterations(None), 10)
        self.assertEqual(cli._bounded_iterations("bad"), 10)
        self.assertEqual(cli._bounded_iterations(0), 1)
        self.assertEqual(cli._bounded_iterations(17), 17)
        self.assertEqual(cli._bounded_iterations(10**12), 100)
        self.assertIsNone(cli._provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "https://proxy.example/v1",
        }))
        self.assertIsNone(cli._provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "http://127.0.0.9:8080/v1",
        }))
        self.assertIsNotNone(cli._provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "http://proxy.example/v1",
        }))
        self.assertIsNotNone(cli._provider_policy_error({
            "llm_provider": "deepseek", "llm_base_url": "https://atlascloud.ai/v1",
        }))
        service = (MODULE.parents[3] / "src/services/ai_quant_lab/AIQuantLabService.cpp").read_text(encoding="utf-8")
        rd_start = service[service.index("void AIQuantLabService::rd_start") :
                           service.index("bool AIQuantLabService::rd_spawn")]
        self.assertIn("std::clamp", rd_start)
        self.assertIn("kMaxRdIterations", rd_start)

    def test_rd_agent_forwards_profile_output_and_transport_settings(self):
        config = load_path("finagent_rdagent_config", RDAGENT_CONFIG)
        with mock.patch.dict(os.environ, {}, clear=True):
            values = config.apply_llm_config({
                "llm_provider": "deepseek",
                "llm_api_key": "owned",
                "llm_model": "deepseek-flash",
                "llm_base_url": "https://proxy.example/v1",
                "llm_max_tokens": 777,
            })
            self.assertEqual(values["CHAT_MAX_TOKENS"], "777")
            self.assertEqual(values["LITELLM_CHAT_MAX_TOKENS"], "777")
            self.assertEqual(values["OPENAI_API_BASE"], "https://proxy.example/v1")
            self.assertEqual(values["DEEPSEEK_API_KEY"], "owned")
            config.clear_llm_config()
            self.assertNotIn("DEEPSEEK_API_KEY", os.environ)
            self.assertNotIn("OPENAI_API_BASE", os.environ)
            self.assertNotIn("CHAT_MAX_TOKENS", os.environ)

        with mock.patch.dict(os.environ, {}, clear=True):
            values = config.apply_llm_config({
                "llm_provider": "deepseek", "llm_api_key": "owned", "llm_max_tokens": 10**12,
            })
            self.assertEqual(values["CHAT_MAX_TOKENS"], "2000000")

        with mock.patch.dict(os.environ, {}, clear=True):
            values = config.apply_llm_config({
                "llm_provider": "deepseek", "llm_api_key": "owned", "llm_max_tokens": "bad",
            })
            self.assertEqual(values["CHAT_MAX_TOKENS"], "4096")

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
