import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
SERVICE = ROOT / "src" / "screens" / "chat_mode" / "ChatModeService.cpp"
SCREEN = ROOT / "src" / "screens" / "chat_mode" / "ChatModeScreen.cpp"


class LocalChatPersistenceWiringTest(unittest.TestCase):
    def test_community_chat_service_uses_the_device_local_repository(self):
        source = SERVICE.read_text(encoding="utf-8")

        self.assertIn('"storage/repositories/ChatRepository.h"', source)
        for method in ("create_session", "list_sessions_page", "load_session", "delete_session",
                       "rename_session", "get_stats", "search_messages", "export_sessions"):
            start = source.index(f"ChatModeService::{method}")
            end = source.find("\nvoid ChatModeService::", start + 1)
            block = source[start:end if end >= 0 else None]
            self.assertIn("fincept_chat_api_enabled", block, method)
            self.assertIn("ChatRepository::instance()", block, method)

    def test_community_screen_creates_and_restores_real_local_sessions(self):
        source = SCREEN.read_text(encoding="utf-8")
        show = source[source.index("void ChatModeScreen::showEvent"):
                      source.index("void ChatModeScreen::hideEvent")]
        recent = source[source.index("void ChatModeScreen::load_recent_session"):
                        source.index("void ChatModeScreen::refresh_stats")]
        ensure = source[source.index("void ChatModeScreen::ensure_active_session"):
                        source.index("// ── Runs")]

        self.assertLess(show.index("session_panel_->refresh_sessions()"),
                        show.index("if (!policy::fincept_chat_api_enabled())"))
        self.assertNotIn("show_start_page();\n        return;", recent)
        self.assertNotIn('QStringLiteral("local-")', ensure)
        self.assertIn("ChatModeService::instance().create_session", ensure)

    def test_local_run_messages_are_persisted_once_with_stable_ids(self):
        source = SCREEN.read_text(encoding="utf-8")

        self.assertIn('"storage/repositories/ChatRepository.h"', source)
        self.assertIn('run_id + QStringLiteral(":user")', source)
        self.assertIn('run_id + QStringLiteral(":assistant")', source)
        self.assertGreaterEqual(source.count("add_message_once"), 2)
        self.assertIn("message_panel_->set_session_title(run->title", source)

    def test_local_followups_receive_saved_history_without_polluting_the_transcript(self):
        source = SCREEN.read_text(encoding="utf-8")
        start = source[source.index("void ChatModeScreen::start_chat_run"):]

        self.assertIn('#include "screens/chat_mode/LocalChatHistory.h"', source)
        self.assertIn("get_messages(session_uuid)", start)
        self.assertIn("compose_local_chat_prompt(history.value(), visible_prompt)", start)
        self.assertLess(start.index("compose_local_chat_prompt"), start.index("start_run(request"))
        message = start[start.index("add_message_once"):
                        start.index("const auto session", start.index("add_message_once"))]
        self.assertIn("visible_prompt", message)
        self.assertNotIn("request.prompt", message)

    def test_plain_local_chat_is_direct_byo_while_task_mode_keeps_the_python_agent(self):
        bar = (ROOT / "src/screens/chat_mode/ComposerContextBar.cpp").read_text(encoding="utf-8")
        run_types = (ROOT / "src/services/agent_tasks/TaskRunTypes.cpp").read_text(encoding="utf-8")

        self.assertIn("bool local_task = false", bar)
        self.assertIn("choice == CcbChoice::Local ? local_task", bar)
        self.assertIn("d_->choice == CcbChoice::Local ? d_->local_task", bar)
        self.assertIn("plan_first ? kExecutorLocal : kExecutorByoChat", run_types)


if __name__ == "__main__":
    unittest.main()
