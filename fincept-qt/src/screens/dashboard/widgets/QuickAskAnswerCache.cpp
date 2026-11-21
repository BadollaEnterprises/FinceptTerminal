#include "screens/dashboard/widgets/QuickAskAnswerCache.h"

#include <QCryptographicHash>

namespace fincept::screens::widgets {

QString quick_ask_answer_cache_key(const QString& question, const QString& provider,
                                   const QString& model, int prompt_version) {
    QByteArray material = QByteArray::number(prompt_version);
    material += '\0';
    material += provider.trimmed().toLower().toUtf8();
    material += '\0';
    material += model.trimmed().toUtf8();
    material += '\0';
    material += question.simplified().toUtf8();
    return QString::fromLatin1(QCryptographicHash::hash(material, QCryptographicHash::Sha256).toHex());
}

bool quick_ask_response_is_cacheable(bool success, bool cancelled, const QString& answer,
                                     const QString& fallback_notice) {
    return success && !cancelled && !answer.trimmed().isEmpty() && fallback_notice.isEmpty();
}

} // namespace fincept::screens::widgets
