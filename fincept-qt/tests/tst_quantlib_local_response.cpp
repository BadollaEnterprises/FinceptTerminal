#include "services/quantlib/QuantLibLocalResponse.h"

#include <QtTest>

using namespace fincept::services;

class TestQuantLibLocalResponse : public QObject {
    Q_OBJECT
  private slots:
    void validation_error_is_not_a_successful_blank_result();
    void unsupported_route_is_explicitly_local_only();
    void successful_payload_keeps_local_metadata();
    void malformed_success_envelope_is_rejected();
};

void TestQuantLibLocalResponse::validation_error_is_not_a_successful_blank_result() {
    const auto result = parse_local_quantlib_response(
        QJsonObject{{QStringLiteral("supported"), true},
                    {QStringLiteral("error"), QStringLiteral("rate must be positive")},
                    {QStringLiteral("meta"),
                     QJsonObject{{QStringLiteral("fincept_credits"), 0},
                                 {QStringLiteral("llm_used"), false}}}});

    QVERIFY(!result.success);
    QCOMPARE(result.error, QStringLiteral("rate must be positive"));
    QCOMPARE(result.data.toObject().value(QStringLiteral("error_code")).toString(),
             QStringLiteral("LOCAL_INPUT_ERROR"));
    QCOMPARE(result.data.toObject().value(QStringLiteral("local_only")).toBool(), true);
}

void TestQuantLibLocalResponse::unsupported_route_is_explicitly_local_only() {
    const auto result = parse_local_quantlib_response(
        QJsonObject{{QStringLiteral("supported"), false},
                    {QStringLiteral("message"), QStringLiteral("No local implementation")}});

    QVERIFY(!result.success);
    QCOMPARE(result.error, QStringLiteral("No local implementation"));
    QCOMPARE(result.data.toObject().value(QStringLiteral("error_code")).toString(),
             QStringLiteral("LOCAL_UNSUPPORTED"));
    QCOMPARE(result.data.toObject().value(QStringLiteral("local_only")).toBool(), true);
}

void TestQuantLibLocalResponse::successful_payload_keeps_local_metadata() {
    const auto result = parse_local_quantlib_response(
        QJsonObject{{QStringLiteral("supported"), true},
                    {QStringLiteral("data"), QJsonObject{{QStringLiteral("npv"), 12.5}}},
                    {QStringLiteral("meta"),
                     QJsonObject{{QStringLiteral("cost_route"), QStringLiteral("local_deterministic")},
                                 {QStringLiteral("fincept_credits"), 0}}}});

    QVERIFY(result.success);
    QCOMPARE(result.data.toObject().value(QStringLiteral("npv")).toDouble(), 12.5);
    QCOMPARE(result.data.toObject()
                 .value(QStringLiteral("_local"))
                 .toObject()
                 .value(QStringLiteral("fincept_credits"))
                 .toInt(),
             0);
}

void TestQuantLibLocalResponse::malformed_success_envelope_is_rejected() {
    const auto result = parse_local_quantlib_response(QJsonObject{{QStringLiteral("supported"), true}});

    QVERIFY(!result.success);
    QCOMPARE(result.data.toObject().value(QStringLiteral("error_code")).toString(),
             QStringLiteral("LOCAL_INVALID_RESPONSE"));
}

QTEST_GUILESS_MAIN(TestQuantLibLocalResponse)
#include "tst_quantlib_local_response.moc"
