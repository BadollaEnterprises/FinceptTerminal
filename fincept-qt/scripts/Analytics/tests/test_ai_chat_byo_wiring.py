import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
COMPOSER = ROOT / "src" / "screens" / "ai_chat" / "AiChatComposer.cpp"
PROVIDER_CATALOG = ROOT / "src" / "services" / "llm" / "ProviderCatalog.cpp"
REQUEST_BUILDERS = ROOT / "src" / "services" / "llm" / "LlmRequestBuilders.cpp"
CHAT_MODE_COMPOSER = ROOT / "src" / "screens" / "chat_mode" / "ChatComposer.cpp"
CHAT_MODE_SERVICE = ROOT / "src" / "screens" / "chat_mode" / "ChatModeService.cpp"


class AiChatByoWiringTest(unittest.TestCase):
    def test_effort_copy_uses_the_resolved_route_billing_policy(self):
        source = COMPOSER.read_text(encoding="utf-8")

        self.assertIn('"screens/ai_chat/AiChatEffortPolicy.h"', source)
        self.assertIn("effort_usage(", source)
        self.assertIn("kFeatureAiChat", source)
        self.assertIn("routes_changed", source)
        self.assertIn("more provider usage", source)
        self.assertIn("extra Fincept credits", source)

    def test_deepseek_catalog_and_request_use_the_current_v4_contract(self):
        catalog = PROVIDER_CATALOG.read_text(encoding="utf-8")
        fallback_models = catalog[catalog.index("QStringList ProviderCatalog::fallback_models") :]
        deepseek_fallback = fallback_models[fallback_models.index('if (p == "deepseek")'):
                                            fallback_models.index('if (p == "openrouter")')]
        builders = REQUEST_BUILDERS.read_text(encoding="utf-8")

        self.assertIn("deepseek-flash", deepseek_fallback)
        self.assertIn("deepseek-v4-pro", deepseek_fallback)
        self.assertNotIn("deepseek-chat", deepseek_fallback)
        self.assertNotIn("deepseek-reasoner", deepseek_fallback)
        self.assertIn("apply_deepseek_thinking", builders)

    def test_chat_mode_prompt_optimizer_is_visible_and_uses_the_byo_route(self):
        composer = CHAT_MODE_COMPOSER.read_text(encoding="utf-8")
        service = CHAT_MODE_SERVICE.read_text(encoding="utf-8")

        self.assertIn("optimize->setVisible(true)", composer)
        self.assertIn("configured BYO model", composer)
        self.assertNotIn("one request to the Fincept server", composer)
        self.assertIn("prompt_optimization_request", service)
        self.assertIn("kFeatureAgent", service)
        self.assertIn("use_tools=*/false", service)


if __name__ == "__main__":
    unittest.main()
