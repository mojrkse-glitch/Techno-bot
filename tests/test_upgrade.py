import json
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import admin_panel
import api
import checkout
import commerce_db as store
import cv_delivery
import cv_intake
import cv_pipeline
import database
import main
import notifications
import shop_ui
from telebot import types


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(database, "DB_PATH", Path(folder) / "store.db"))
        self.enterContext(patch.dict("os.environ", {"TEC_PAY_STARS_ENABLED": "1",
                                                    "TEC_PAY_MANUAL_WALLET_ENABLED": "1"}, clear=True))
        database.init_db()
        store.init()
        database.get_user(101)
        store.set_setting("stars:cv", "50", 999)
        self.bot = self.enterContext(patch.object(main, "bot"))
        self.bot.send_document.return_value = NS(message_id=987)
        self.cv = {
            "personal_info": {"full_name": "Example Person", "job_title": "Analyst", "location": "Example City",
                              "phone": "+12025550100", "email": "example@example.test"},
            "professional_summary": "Reviewed reports",
            "work_experience": [{"job_title": "Analyst", "company": "Example Firm", "start_date": "2020",
                                 "end_date": "2024", "responsibilities": ["Wrote 2 reports"]}],
            "education": [], "skills": [], "languages": [],
        }

    def payment(self, complete=True):
        # Use the real CV entry, invoice review, pre-checkout and Telegram payment handler.
        states = {}
        shop_ui.begin(self.bot, 101, None, 101, "cv", "en", states)
        self.assertEqual(states[101]["usd"], Decimal("5"))
        shop_ui.issue(self.bot, 101, None, 101, "en", states, states[101]["review_token"])
        arguments = self.bot.send_invoice.call_args.kwargs
        token = arguments["invoice_payload"]
        oid = store.find_invoice(token)["id"]
        self.assertIsNone(cv_intake.issue_token(101, oid))
        if complete:
            query = NS(invoice_payload=token, from_user=NS(id=101), currency="XTR", total_amount=50, id="query")
            checkout.precheck(self.bot, query)
            self.bot.answer_pre_checkout_query.assert_called_with("query", True, error_message=None)
            event = NS(chat=NS(id=101, type="private"), from_user=NS(id=101), refunded_payment=None,
                       successful_payment=NS(invoice_payload=token, currency="XTR", total_amount=50,
                                             telegram_payment_charge_id="synthetic-charge"))
            main.payment_result(event)
            main.payment_result(event)
            self.assertEqual(store.order(oid)["state"], "cv_details")
        return oid

    def submission(self):
        oid = self.payment()
        token = cv_intake.issue_token(101, oid)
        self.assertEqual(cv_intake.save(101, token, "en", self.cv), (oid, None))
        return oid

    def test_paid_notifications_card_and_intake(self):
        oid = self.payment()
        with patch.object(cv_intake, "webapp_url", return_value="https://example.invalid/form"):
            notifications.push(self.bot)
            offer = self.bot.send_message.call_args.kwargs["reply_markup"]
            self.assertIn(f"o={oid}", offer.keyboard[0][0]["web_app"]["url"])
            self.assertEqual(offer.keyboard[1][0]["text"], f"⬅️ Back · #{oid}")
            shop_ui.show_order(self.bot, 101, None, 101, oid, "ar")
            card = self.bot.send_message.call_args.kwargs["reply_markup"]
            self.assertEqual(card.keyboard[0][0].callback_data, f"cvapp|{oid}")
            self.assertIsNone(cv_intake.issue_token(102, oid))
            token = cv_intake.issue_token(101, oid, reuse=True)
        self.assertEqual(cv_intake.save(102, token, "en", self.cv), (None, "token"))
        payload = json.dumps({"token": token, "resume_language": "en", "cvData": self.cv})
        event = NS(chat=NS(id=101, type="private"), from_user=NS(id=101), web_app_data=NS(data=payload))
        cv_intake.handle(self.bot, event)
        cv_intake.handle(self.bot, event)
        self.assertEqual(json.loads(cv_intake.submission(oid)["payload"])["cvData"], self.cv)
        self.assertEqual(cv_intake.save(101, token, "en", self.cv), (None, "used"))
        self.assertIsNone(cv_intake.issue_token(101, oid))
        with self.assertRaises(cv_intake.Invalid):
            cv_intake.parse(json.dumps({"token": token, "resume_language": [], "cvData": self.cv}))

    def test_legacy_paid_cv_migrates_once_without_second_debit(self):
        with database.transaction(write=True) as conn:
            database.change_balance(conn, 101, Decimal("10"))
        old, created = database.create_cv_order(101, "synthetic-intent", "Legacy CV")
        self.assertTrue(created)
        database.change_cv_status(old["id"], "in_progress")
        store.init()
        store.init()
        row = store.legacy_cv_order(old["id"], 101)
        self.assertEqual(row["state"], "cv_work")
        self.assertIsNotNone(cv_intake.issue_token(101, row["id"]))
        self.assertIsNone(store.legacy_cv_order(old["id"], 102))
        self.assertEqual(database.get_user(101)["balance"], 5)
        self.assertFalse(database.change_cv_status(old["id"], "cancelled")[1])

    def test_pipeline_checkpoints_and_admin_delivery(self):
        oid = self.submission()
        mapping = {key: f"L_{key}" for key in cv_pipeline.template_values(self.cv)}
        env = {key: "fixture" for key in cv_pipeline.REQUIRED}
        env.update(SUPABASE_URL="https://example.invalid", CANVA_FIELD_MAP=json.dumps(mapping))
        stages = []
        canva_posts = []

        def service(method, url, headers, **kwargs):
            if url.endswith(":generateContent"):
                text = kwargs["json"]["contents"][0]["parts"][1]["text"]
                for value in self.cv["personal_info"].values():
                    self.assertNotIn(value, text)
                result = {"items": ["Reviewed reports.", "Wrote 2 reports."]}
                return Mock(json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps(result)}]}}]})
            if url.endswith("/rest/v1/techno_cv_jobs"):
                if method == "GET":
                    return Mock(json=lambda: [{"canva_job": "canva-1"}])
                stages.append(kwargs["json"]["stage"])
                return Mock()
            if url.endswith("/dataset"):
                return Mock(json=lambda: {"dataset": {name: {"type": "text"} for name in mapping.values()}})
            if url.endswith("/functions/v1/techno-cv-autofill"):
                canva_posts.append(kwargs["json"])
                self.assertEqual(kwargs["json"]["data"]["L_full_name"]["text"], "Example Person")
                raise cv_pipeline.requests.Timeout()
            if url.endswith("/autofills/canva-1"):
                return Mock(json=lambda: {"job": {"status": "success", "result": {"design": {"id": "design-1"}}}})
            if url.endswith("/exports"):
                return Mock(json=lambda: {"job": {"id": "export-1"}})
            if url.endswith("/exports/export-1"):
                return Mock(json=lambda: {"job": {"status": "success", "urls": ["https://export-download.canva.com/pdf"]}})
            self.fail(f"Unexpected mock endpoint: {url}")

        with (patch.dict("os.environ", env), patch.object(cv_pipeline, "request", side_effect=service),
              patch.object(cv_pipeline, "download", return_value=b"%PDF-test")):
            cv_pipeline.tick()
            cv_pipeline.tick()
            cv_pipeline.tick()
            self.assertEqual(cv_pipeline.status(oid)["error"], "uncertain")
            self.assertFalse(cv_pipeline.resume(oid))
            self.assertTrue(cv_pipeline.reconcile(oid))
            for _ in range(5):
                cv_pipeline.tick()
        self.assertEqual(len(canva_posts), 1)
        self.assertEqual(stages, ["autofill", "design", "export", "pdf", "ready"])
        self.assertEqual(cv_pipeline.status(oid)["stage"], "ready")
        self.assertEqual(self.bot.send_document.call_count, 0)
        call = NS(id="c", message=NS(message_id=10, chat=NS(type="private", id=999)), from_user=NS(id=998),
                  data=f"adm|cvsendpdfgo|{oid}")
        states = {}
        with patch.object(admin_panel, "ADMIN_ID", 999):
            admin_panel.handle_callback(self.bot, call, states)
            self.assertEqual(self.bot.send_document.call_count, 0)
            call.from_user.id = 999
            call.data = f"adm|cvpdf|{oid}"
            admin_panel.handle_callback(self.bot, call, states)
            self.assertEqual(self.bot.send_document.call_args.args[0], 999)
            self.bot.send_document.reset_mock()
            call.data = f"adm|cvsendpdf|{oid}"
            admin_panel.handle_callback(self.bot, call, states)
            self.assertEqual(self.bot.send_document.call_count, 0)
            call.data = f"adm|cvsendpdfgo|{oid}"
            admin_panel.handle_callback(self.bot, call, states)
            admin_panel.handle_callback(self.bot, call, states)
        self.assertEqual(self.bot.send_document.call_count, 1)
        self.assertEqual(self.bot.send_document.call_args.args[0], 101)
        self.assertEqual(store.order(oid)["state"], "delivered")

    def test_failed_integration_retains_input_and_resumes(self):
        oid = self.submission()
        env = {key: "fixture" for key in cv_pipeline.REQUIRED}
        with patch.dict("os.environ", env), patch.object(cv_pipeline, "rewrite", side_effect=ValueError):
            for now in (1000, 1100, 1200):
                with patch.object(cv_pipeline.time, "time", return_value=now):
                    cv_pipeline.tick()
        self.assertEqual(cv_pipeline.status(oid)["error"], "failed")
        self.assertIsNotNone(cv_intake.submission(oid))
        self.assertTrue(cv_pipeline.resume(oid))
        self.assertEqual(cv_pipeline.status(oid)["attempts"], 0)

    def test_refund_stops_processing_and_manual_delivery_still_works(self):
        oid = self.submission()
        store.transition(oid, ("cv_details",), "refund_requested", 999)
        env = {key: "fixture" for key in cv_pipeline.REQUIRED}
        with patch.dict("os.environ", env), patch.object(cv_pipeline, "request") as request:
            cv_pipeline.tick()
        request.assert_not_called()
        self.assertEqual(cv_delivery.send(self.bot, oid, 999, "fixture", "fixture", "cv.pdf")[0], "order")
        store.transition(oid, ("refund_requested",), "cv_work", 999)
        self.assertEqual(cv_delivery.send(self.bot, oid, 999, "fixture", "fixture", "cv.pdf")[0], "sent")

    def test_commands_and_back_while_searching(self):
        oid = self.payment()
        self.bot.reset_mock()
        user = {"id": 101, "is_bot": False, "first_name": "Example", "language_code": "en"}

        def event(text):
            return types.Message.de_json({"message_id": 123, "date": 0, "text": text,
                                          "chat": {"id": 101, "type": "private"}, "from": user})

        for code, command in [("en", "/help"), ("ar", "/help"), ("ar", "/language"), ("en", "/start")]:
            self.bot.reset_mock()
            database.set_lang(101, code)
            with database.transaction(write=True) as conn:
                conn.execute("INSERT OR REPLACE INTO user_sessions VALUES(?,?,unixepoch()+60)",
                             (101, '{"mode":"search"}'))
            with patch.object(main, "is_subscribed", return_value=True):
                # Dispatch from a real TeleBot, using the handlers registered by main.
                self.dispatch(event(command))
            self.assertIn(101, [call.args[0] for call in self.bot.send_message.call_args_list])
            self.assertNotIn(101, main.user_states)
        click = NS(id="lang", message=NS(message_id=10, chat=NS(type="private", id=101)),
                   from_user=NS(id=101, first_name="Example"), data="setlang_ar")
        with patch.object(main, "is_subscribed", return_value=False):
            main.callbacks(click)
        self.assertEqual(database.get_user(101)["lang"], "ar")
        self.dispatch(event(f"⬅️ رجوع · #{oid}"))
        self.assertEqual(store.order(oid)["state"], "cv_details")

    def dispatch(self, message):
        # Dispatch through the real pyTelegramBotAPI matching code.
        real = types.Update.de_json({"update_id": 123})
        real.message = message
        REGISTERED_BOT.threaded = False
        REGISTERED_BOT.process_new_updates([real])

    def test_checkout_requires_fresh_price_and_availability(self):
        state = {"mode": "checkout", "pid": "plan", "qty": 1, "name": "Plan", "cost": Decimal("2"),
                 "usd": Decimal("2.40"), "stars": None, "review_token": "presented", "syp_rate": None}
        self.assertEqual(api.calc_price(Decimal("2"), "plan"), Decimal("2.40"))
        for fresh in (Decimal("3"), None):
            with (patch.object(shop_ui, "product", return_value={"id": "plan"}),
                  patch.object(checkout, "supplier_quote", return_value=fresh),
                  patch.object(store, "wallet_purchase") as purchase):
                shop_ui.issue(self.bot, 101, None, 101, "en", {101: state.copy()}, "presented", "wallet")
            purchase.assert_not_called()

    def test_catalog_backoff_and_local_edits(self):
        with (patch.object(api, "CACHE", {"en": (0, [{"id": "test", "name": "Test",
                                                      "smart_cat": "other", "icon": "x"}])}),
              patch.object(api, "RETRY_AFTER", {}),
              patch.object(api, "jrk_request", return_value=(503, {})) as supplier):
            self.assertEqual(len(api.listing_products("en")), 1)
            store.edit_product("test", "hidden", True, 999)
            self.assertEqual(api.listing_products("en"), [])
            self.assertEqual(supplier.call_count, 1)


REGISTERED_BOT = main.bot
