import os
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from helpdesk import settings as helpdesk_settings
from helpdesk.models import FollowUp, Queue, Ticket, UserSettings


@override_settings(
    LANGUAGE_CODE="en-us",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
)
class StaffTicketReplyTests(StaticLiveServerTestCase):
    fixtures: ClassVar[list[str]] = ["emailtemplate.json"]

    @classmethod
    def setUpClass(cls):
        # Updates must not call external or leftover webhook endpoints.
        cls.enterClassContext(
            patch.dict(
                os.environ,
                {
                    "HELPDESK_NEW_TICKET_WEBHOOK_URLS": "",
                    "HELPDESK_FOLLOWUP_WEBHOOK_URLS": "",
                },
            )
        )
        cls.enterClassContext(
            patch.object(helpdesk_settings, "HELPDESK_UPDATE_PUBLIC_DEFAULT", False)
        )
        super().setUpClass()

    def setUp(self):
        super().setUp()
        self.password = "staff-test-password"
        self.staff = get_user_model().objects.create_user(
            username="support-agent",
            email="agent@example.com",
            password=self.password,
            is_staff=True,
        )
        user_settings = self.staff.usersettings_helpdesk
        user_settings.ticket_respond_layout = UserSettings.RESPOND_LAYOUT_TABS
        user_settings.save(update_fields=["ticket_respond_layout"])
        queue = Queue.objects.create(
            title="Customer Support",
            slug="customer-support",
            allow_public_submission=True,
        )
        self.ticket = Ticket.objects.create(
            queue=queue,
            title="Payment fails at checkout",
            description="The payment page displays an error after confirmation.",
            submitter_email="customer@example.com",
        )
        self.staff_url = self.live_server_url + self.ticket.get_absolute_url()
        self.public_url = self.live_server_url + self.ticket.ticket_url

    def test_customer_sees_staff_public_reply_after_reload(self):
        reply = "Please try again; we have restored the payment service."

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                staff_context = browser.new_context(locale="en-US")
                staff_page = staff_context.new_page()
                # Following the protected URL exercises the real login redirect.
                staff_page.goto(self.staff_url)
                staff_page.get_by_label("Username", exact=True).fill(
                    self.staff.username
                )
                staff_page.get_by_label("Password", exact=True).fill(self.password)
                staff_page.get_by_role("button", name="Log in", exact=True).click()
                expect(staff_page).to_have_url(self.staff_url)

                staff_page.get_by_role("tab", name="Respond", exact=True).click()
                form = staff_page.locator("#ticket-respond-form")
                form.get_by_label("Comment / Resolution", exact=True).fill(reply)
                form.get_by_label(
                    "Is this update public? (Optional)", exact=True
                ).check()
                form.get_by_role("button", name="Save", exact=True).click()
                saved_reply = staff_page.locator("#followup-list").get_by_text(
                    reply, exact=True
                )
                expect(saved_reply).to_be_visible()

                # A separate context cannot reuse the employee's authenticated session.
                customer_context = browser.new_context(locale="en-US")
                customer_page = customer_context.new_page()
                customer_page.goto(self.public_url)
                expect(
                    customer_page.get_by_role(
                        "heading", name=self.ticket.title, exact=True
                    )
                ).to_be_visible()
                customer_reply = customer_page.locator("#followup-list").get_by_text(
                    reply, exact=True
                )
                expect(customer_reply).to_be_visible()
                customer_page.reload()
                expect(customer_reply).to_be_visible()
            finally:
                browser.close()

        # ORM checks run after Playwright's synchronous API has stopped.
        followup = self.ticket.followup_set.get()
        self.assertEqual(followup.comment, reply)
        self.assertEqual(followup.user_id, self.staff.pk)
        self.assertTrue(followup.public)

    def test_customer_cannot_see_staff_private_reply_after_reload(self):
        private_reply = "Internal note: ask the payment team to review the transaction."
        public_reply = "We are investigating the payment issue."
        FollowUp.objects.create(
            ticket=self.ticket,
            user=self.staff,
            title="Public update",
            comment=public_reply,
            public=True,
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                staff_context = browser.new_context(locale="en-US")
                staff_page = staff_context.new_page()
                staff_page.goto(self.staff_url)
                staff_page.get_by_label("Username", exact=True).fill(
                    self.staff.username
                )
                staff_page.get_by_label("Password", exact=True).fill(self.password)
                staff_page.get_by_role("button", name="Log in", exact=True).click()
                expect(staff_page).to_have_url(self.staff_url)

                staff_page.get_by_role("tab", name="Respond", exact=True).click()
                form = staff_page.locator("#ticket-respond-form")
                form.get_by_label("Comment / Resolution", exact=True).fill(
                    private_reply
                )
                form.get_by_label(
                    "Is this update public? (Optional)", exact=True
                ).uncheck()
                form.get_by_role("button", name="Save", exact=True).click()
                saved_reply = staff_page.locator("#followup-list").get_by_text(
                    private_reply, exact=True
                )
                expect(saved_reply).to_be_visible()
                staff_page.reload()
                expect(saved_reply).to_be_visible()

                customer_context = browser.new_context(locale="en-US")
                customer_page = customer_context.new_page()
                response = customer_page.goto(self.public_url)
                ticket_heading = customer_page.get_by_role(
                    "heading", name=self.ticket.title, exact=True
                )
                # Confirm public replies render before checking private text is absent.
                expect(ticket_heading).to_be_visible()
                customer_public_reply = customer_page.locator(
                    "#followup-list"
                ).get_by_text(public_reply, exact=True)
                expect(customer_public_reply).to_be_visible()
                expect(customer_page.locator("body")).not_to_contain_text(private_reply)
                self.assertIsNotNone(response)
                self.assertNotIn(private_reply, response.text())
                response = customer_page.reload()
                expect(ticket_heading).to_be_visible()
                expect(customer_public_reply).to_be_visible()
                expect(customer_page.locator("body")).not_to_contain_text(private_reply)
                self.assertIsNotNone(response)
                self.assertNotIn(private_reply, response.text())
            finally:
                browser.close()

        followup = self.ticket.followup_set.get(comment=private_reply)
        self.assertEqual(followup.user_id, self.staff.pk)
        self.assertFalse(followup.public)
