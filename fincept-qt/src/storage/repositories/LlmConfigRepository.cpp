#include "storage/repositories/LlmConfigRepository.h"

#include "storage/secure/SecureStorage.h"

namespace fincept {

namespace {
// Encrypt LLM API keys at rest: keep them in SecureStorage (AES-256-GCM,
// machine-bound key) instead of the plaintext SQL column. Reads fall back to
// the column so pre-existing plaintext rows keep working and no key is ever
// lost; writes move the key into SecureStorage and blank the column only when
// the encrypted copy was written AND read back equal (store_verified) —
// otherwise the plaintext value is kept and the move is retried on the next save.
// The column is only ever non-empty when it is the newer copy (legacy row, or a
// save made while the store failed), so a non-empty column wins on read.
QString provider_secret_key(const QString& provider) {
    return QStringLiteral("llm:apikey:provider:") + provider;
}
QString model_secret_key(const QString& id) {
    return QStringLiteral("llm:apikey:model:") + id;
}

Result<void> hydrate_provider_key(LlmConfig& c) {
    if (!c.api_key.isEmpty())
        return Result<void>::ok(); // legacy plaintext already present in the column
    const SecretRead sr = SecureStorage::instance().read(provider_secret_key(c.provider));
    if (sr.failed())
        return Result<void>::err(QString("Could not read secure storage for LLM provider '%1': %2")
                                     .arg(c.provider, sr.reason())
                                     .toStdString());
    if (sr.found())
        c.api_key = sr.value;
    return Result<void>::ok();
}

Result<void> hydrate_model_key(LlmModelConfig& m) {
    if (!m.api_key.isEmpty())
        return Result<void>::ok();
    const SecretRead sr = SecureStorage::instance().read(model_secret_key(m.id)); // see hydrate_provider_key
    if (sr.failed())
        return Result<void>::err(QString("Could not read secure storage for LLM model '%1': %2")
                                     .arg(m.id, sr.reason())
                                     .toStdString());
    if (sr.found())
        m.api_key = sr.value;
    return Result<void>::ok();
}
} // namespace

LlmConfigRepository& LlmConfigRepository::instance() {
    static LlmConfigRepository s;
    return s;
}

LlmConfig LlmConfigRepository::map_config(QSqlQuery& q) {
    return {q.value(0).toString(), q.value(1).toString(), q.value(2).toString(), q.value(3).toString(),
            q.value(4).toBool(),   q.value(5).toBool(),   q.value(6).toString(), q.value(7).toString()};
}

LlmModelConfig LlmConfigRepository::map_model(QSqlQuery& q) {
    return {q.value(0).toString(), q.value(1).toString(), q.value(2).toString(), q.value(3).toString(),
            q.value(4).toString(), q.value(5).toString(), q.value(6).toBool(),   q.value(7).toBool(),
            q.value(8).toString(), q.value(9).toString()};
}

Result<QVector<LlmConfig>> LlmConfigRepository::list_providers() {
    auto r = query_list("SELECT provider, api_key, base_url, model, is_active, tools_enabled, created_at, updated_at "
                        "FROM llm_configs ORDER BY provider",
                        {}, map_config);
    if (r.is_err())
        return r;
    QVector<LlmConfig> out = r.value();
    for (auto& c : out) {
        const auto hydrated = hydrate_provider_key(c);
        if (hydrated.is_err())
            return Result<QVector<LlmConfig>>::err(hydrated.error());
    }
    return Result<QVector<LlmConfig>>::ok(std::move(out));
}

Result<LlmConfig> LlmConfigRepository::get_active_provider() {
    auto r = query_one("SELECT provider, api_key, base_url, model, is_active, tools_enabled, created_at, updated_at "
                       "FROM llm_configs WHERE is_active = 1 LIMIT 1",
                       {}, map_config);
    if (r.is_ok()) {
        LlmConfig c = r.value();
        const auto hydrated = hydrate_provider_key(c);
        if (hydrated.is_err())
            return Result<LlmConfig>::err(hydrated.error());
        return Result<LlmConfig>::ok(std::move(c));
    }
    return r;
}

Result<void> LlmConfigRepository::save_provider(const LlmConfig& c) {
    // Persist the recoverable row copy before changing the encrypted key. Once
    // the secure write is verified, scrub the plaintext column.
    auto saved = exec_write(
        "INSERT INTO llm_configs (provider, api_key, base_url, model, is_active, tools_enabled, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(provider) DO UPDATE SET "
        "api_key = excluded.api_key, base_url = excluded.base_url, model = excluded.model, "
        "is_active = excluded.is_active, tools_enabled = excluded.tools_enabled, updated_at = datetime('now')",
        {c.provider, c.api_key, c.base_url, c.model, c.is_active ? 1 : 0, c.tools_enabled ? 1 : 0});
    if (saved.is_err())
        return saved;
    if (c.api_key.isEmpty())
        return saved;
    const auto stored = SecureStorage::instance().store_verified(provider_secret_key(c.provider), c.api_key);
    if (stored.is_err())
        return Result<void>::err("Provider was saved, but its API key could not be secured: " + stored.error());
    auto scrubbed = exec_write("UPDATE llm_configs SET api_key = '' WHERE provider = ?", {c.provider});
    if (scrubbed.is_err())
        return Result<void>::err("Provider was saved, but its plaintext API key could not be removed: " +
                                 scrubbed.error());
    return saved;
}

Result<void> LlmConfigRepository::set_active(const QString& provider) {
    // One statement, so the swap is atomic: "clear all, then set one" as two writes
    // left no active provider if the second failed — or if `provider` did not exist
    // (an unknown name from an MCP tool call), because the first write had already
    // cleared the current one. The EXISTS guard leaves an unknown name alone;
    // affected-row verification makes that no-op visible to every caller.
    auto updated = db().execute("UPDATE llm_configs SET is_active = CASE WHEN provider = ? THEN 1 ELSE 0 END "
                                "WHERE EXISTS (SELECT 1 FROM llm_configs WHERE provider = ?)",
                                {provider, provider});
    if (updated.is_err())
        return Result<void>::err(updated.error());
    if (updated.value().numRowsAffected() == 0)
        return Result<void>::err("Provider not found");
    return Result<void>::ok();
}

Result<void> LlmConfigRepository::delete_provider(const QString& provider) {
    // Never erase the only usable key while the config row may remain. The SQL
    // database and encrypted credential store cannot share a transaction, so a
    // failed SQL delete must leave both parts of the provider usable.
    auto deleted = exec_write("DELETE FROM llm_configs WHERE provider = ?", {provider});
    if (deleted.is_err())
        return deleted;
    const auto cleaned = SecureStorage::instance().remove(provider_secret_key(provider));
    if (cleaned.is_err())
        return Result<void>::err(std::string(SecureStorage::kCleanupFailed) +
                                 " Provider was deleted, but its stored API key could not be removed: " +
                                 cleaned.error());
    return deleted;
}

Result<LlmGlobalSettings> LlmConfigRepository::get_global_settings() {
    auto r = db().execute("SELECT temperature, max_tokens, system_prompt, max_tool_rounds "
                          "FROM llm_global_settings WHERE id = 1");
    if (r.is_err())
        return Result<LlmGlobalSettings>::err(r.error());
    auto& q = r.value();
    if (!q.next())
        return Result<LlmGlobalSettings>::ok(LlmGlobalSettings{});
    LlmGlobalSettings gs;
    gs.temperature = q.value(0).toDouble();
    gs.max_tokens = q.value(1).toInt();
    gs.system_prompt = q.value(2).toString();
    // NULL on rows written before v030 — keep struct default (40).
    const QVariant v = q.value(3);
    if (!v.isNull())
        gs.max_tool_rounds = v.toInt();
    return Result<LlmGlobalSettings>::ok(gs);
}

Result<void> LlmConfigRepository::save_global_settings(const LlmGlobalSettings& s) {
    return exec_write("INSERT OR REPLACE INTO llm_global_settings "
                      "(id, temperature, max_tokens, system_prompt, max_tool_rounds) "
                      "VALUES (1, ?, ?, ?, ?)",
                      {s.temperature, s.max_tokens, s.system_prompt, s.max_tool_rounds});
}

Result<QVector<LlmModelConfig>> LlmConfigRepository::list_models(const QString& provider) {
    if (provider.isEmpty()) {
        auto r = db().execute(
            "SELECT id, provider, model_id, display_name, api_key, base_url, "
            "is_enabled, is_default, created_at, updated_at FROM llm_model_configs ORDER BY provider, display_name");
        if (r.is_err())
            return Result<QVector<LlmModelConfig>>::err(r.error());
        QVector<LlmModelConfig> result;
        auto& q = r.value();
        while (q.next()) {
            LlmModelConfig m = map_model(q);
            const auto hydrated = hydrate_model_key(m);
            if (hydrated.is_err())
                return Result<QVector<LlmModelConfig>>::err(hydrated.error());
            result.append(m);
        }
        return Result<QVector<LlmModelConfig>>::ok(std::move(result));
    }
    auto r = db().execute("SELECT id, provider, model_id, display_name, api_key, base_url, "
                          "is_enabled, is_default, created_at, updated_at FROM llm_model_configs "
                          "WHERE provider = ? ORDER BY display_name",
                          {provider});
    if (r.is_err())
        return Result<QVector<LlmModelConfig>>::err(r.error());
    QVector<LlmModelConfig> result;
    auto& q = r.value();
    while (q.next()) {
        // Same as the unfiltered list: a key held in SecureStorage is not "no key".
        LlmModelConfig m = map_model(q);
        const auto hydrated = hydrate_model_key(m);
        if (hydrated.is_err())
            return Result<QVector<LlmModelConfig>>::err(hydrated.error());
        result.append(m);
    }
    return Result<QVector<LlmModelConfig>>::ok(std::move(result));
}

Result<void> LlmConfigRepository::save_model(const LlmModelConfig& m) {
    auto saved = exec_write(
        "INSERT INTO llm_model_configs "
        "(id, provider, model_id, display_name, api_key, base_url, is_enabled, is_default, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "provider = excluded.provider, model_id = excluded.model_id, display_name = excluded.display_name, "
        "api_key = excluded.api_key, base_url = excluded.base_url, is_enabled = excluded.is_enabled, "
        "is_default = excluded.is_default, updated_at = datetime('now')",
        {m.id, m.provider, m.model_id, m.display_name, m.api_key, m.base_url, m.is_enabled ? 1 : 0,
         m.is_default ? 1 : 0});
    if (saved.is_err())
        return saved;
    if (m.api_key.isEmpty())
        return saved;
    const auto stored = SecureStorage::instance().store_verified(model_secret_key(m.id), m.api_key);
    if (stored.is_err())
        return Result<void>::err("Model was saved, but its API key could not be secured: " + stored.error());
    auto scrubbed = exec_write("UPDATE llm_model_configs SET api_key = '' WHERE id = ?", {m.id});
    if (scrubbed.is_err())
        return Result<void>::err("Model was saved, but its plaintext API key could not be removed: " +
                                 scrubbed.error());
    return saved;
}

Result<void> LlmConfigRepository::delete_model(const QString& id) {
    // Match provider deletion: preserve the encrypted key whenever the model
    // row could not be removed.
    auto deleted = exec_write("DELETE FROM llm_model_configs WHERE id = ?", {id});
    if (deleted.is_err())
        return deleted;
    const auto cleaned = SecureStorage::instance().remove(model_secret_key(id));
    if (cleaned.is_err())
        return Result<void>::err(std::string(SecureStorage::kCleanupFailed) +
                                 " Model was deleted, but its stored API key could not be removed: " +
                                 cleaned.error());
    return deleted;
}

} // namespace fincept
