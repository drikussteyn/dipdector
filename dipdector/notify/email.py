"""
Email delivery.

Two free routes, both behind the same interface:

  SMTPSender    — any SMTP server. With Gmail this needs an App Password, not
                  your account password. Free, no third-party signup, and your
                  credentials never leave your own accounts.
  ResendSender  — Resend's HTTP API, free tier. Easier to set up from a CI
                  runner because it is one API key and no SMTP ports, which
                  some hosts block.
  ConsoleSender — prints instead of sending. The default, so a misconfigured
                  job never silently does nothing.

ON EMAIL HTML, which is not like web HTML:

Gmail and Outlook strip <details>/<summary>, external stylesheets, web fonts,
CSS variables and most modern selectors. The report's whole design — progressive
disclosure behind collapsible panels — therefore cannot survive in an email
body. Rather than ship something that degrades into an unreadable wall, the
email is a deliberately small summary using inline styles and tables only, with
the full report attached as an .html file you open in a browser.
"""

from __future__ import annotations

import datetime as dt
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Dict, List, Optional, Protocol

from .. import narrative

LEVEL_COLOR = {"MAJOR_EVENT": "#9E2B21", "INVESTIGATE": "#86580A",
               "WATCH": "#1E5C4F", "NONE": "#565B62"}
LEVEL_WORD = {"MAJOR_EVENT": "Major event", "INVESTIGATE": "Investigate",
              "WATCH": "Watch", "NONE": "Normal"}


def _env(*names: str) -> Optional[str]:
    """First of `names` that is set and non-empty."""
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


@dataclass
class Message:
    subject: str
    text: str
    html: str
    attachments: List[tuple] = None   # (filename, mimetype, bytes)


class EmailSender(Protocol):
    name: str

    def send(self, to: str, message: Message) -> None:
        ...


class ConsoleSender:
    """Prints the email. Used when nothing is configured, and for --dry-run."""

    name = "console"

    def send(self, to: str, message: Message) -> None:
        print("─" * 68)
        print(f"To:      {to}")
        print(f"Subject: {message.subject}")
        print("─" * 68)
        print(message.text)
        if message.attachments:
            for fn, _, data in message.attachments:
                print(f"[attachment: {fn}, {len(data):,} bytes]")
        print("─" * 68)


class SMTPSender:
    """
    Works with Gmail, Fastmail, Proton Bridge, or any SMTP host.

    For Gmail: turn on 2-step verification, then create an App Password at
    myaccount.google.com/apppasswords. Your normal password will not work and
    should never be put in a config file anyway.
    """

    name = "smtp"

    def __init__(self, host: Optional[str] = None, port: int = 587,
                 user: Optional[str] = None, password: Optional[str] = None,
                 from_addr: Optional[str] = None):
        self.host = host or os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(port or os.environ.get("SMTP_PORT", 587))
        # Both spellings are accepted. .env.example and the workflow have
        # always said SMTP_USERNAME / SMTP_SENDER while this class read
        # SMTP_USER / SMTP_FROM, so a correctly-followed setup guide produced
        # a sender that refused to start.
        self.user = user or _env("SMTP_USERNAME", "SMTP_USER")
        self.password = password or os.environ.get("SMTP_PASSWORD")
        self.from_addr = (from_addr or _env("SMTP_SENDER", "SMTP_FROM")
                          or self.user)
        if not self.user or not self.password:
            raise RuntimeError(
                "SMTP_USERNAME and SMTP_PASSWORD must be set. For Gmail, "
                "SMTP_PASSWORD is an App Password, not your login password.")

    def send(self, to: str, message: Message) -> None:
        msg = EmailMessage()
        msg["Subject"] = message.subject
        msg["From"] = self.from_addr
        msg["To"] = to
        msg.set_content(message.text)
        msg.add_alternative(message.html, subtype="html")

        for filename, mimetype, data in (message.attachments or []):
            maintype, _, subtype = mimetype.partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=filename)

        context = ssl.create_default_context()
        with smtplib.SMTP(self.host, self.port, timeout=30) as s:
            s.starttls(context=context)
            s.login(self.user, self.password)
            s.send_message(msg)


class ResendSender:
    """
    Resend's HTTP API. Preferred over SMTP: one key, no app password, no
    ports for a host to block, and the mail carries the tool's own identity
    rather than a personal account's.

    On the free tier without a verified domain you send from
    onboarding@resend.dev, which may only deliver to the address registered
    on the Resend account. That is not a constraint here — this tool emails
    exactly one person, its owner — but it does mean ALERT_EMAIL and the
    Resend account address have to match.
    """

    name = "resend"

    def __init__(self, api_key: Optional[str] = None,
                 from_addr: Optional[str] = None):
        self.api_key = api_key or os.environ.get("RESEND_API_KEY")
        self.from_addr = (from_addr or os.environ.get("RESEND_FROM")
                          or "DipDector <onboarding@resend.dev>")
        if not self.api_key:
            raise RuntimeError("RESEND_API_KEY is not set.")

    def send(self, to: str, message: Message) -> None:
        import base64

        import requests

        payload = {"from": self.from_addr, "to": [to],
                   "subject": message.subject, "html": message.html,
                   "text": message.text}
        if message.attachments:
            payload["attachments"] = [
                {"filename": fn, "content": base64.b64encode(data).decode()}
                for fn, _, data in message.attachments]

        r = requests.post("https://api.resend.com/emails", timeout=30,
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"},
                          json=payload)
        if r.status_code == 403 and "resend.dev" in self.from_addr:
            # By far the likeliest misconfiguration, and the API's own message
            # does not say which two addresses have to match.
            raise RuntimeError(
                f"Resend refused to deliver to {to}. Sending from "
                f"{self.from_addr} works only for the address registered on "
                f"the Resend account, so ALERT_EMAIL must be that same "
                f"address. Either point ALERT_EMAIL at it, or verify a domain "
                f"in Resend and set RESEND_FROM to an address on it. "
                f"API said: {r.text[:200]}")
        if r.status_code >= 300:
            raise RuntimeError(f"Resend rejected the message: "
                               f"{r.status_code} {r.text[:300]}")


def get_sender(kind: str = "auto") -> EmailSender:
    """
    Pick a sender. 'auto' defers to EMAIL_PROVIDER, then to whatever
    credentials are present.

    EMAIL_PROVIDER is honoured because .env.example and the setup guide both
    tell you to start on `console` and switch to `smtp` once the text looks
    right. If nothing read it, that instruction would be actively unsafe: you
    would set console, paste in working SMTP credentials, and the very first
    run would mail for real.
    """
    if kind == "auto":
        kind = os.environ.get("EMAIL_PROVIDER") or "auto"
    if kind == "console":
        return ConsoleSender()
    if kind == "smtp":
        return SMTPSender()
    if kind == "resend":
        return ResendSender()
    if os.environ.get("RESEND_API_KEY"):
        return ResendSender()
    if _env("SMTP_USERNAME", "SMTP_USER") and os.environ.get("SMTP_PASSWORD"):
        return SMTPSender()
    return ConsoleSender()


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------

def _button(url: Optional[str]) -> str:
    """
    A table-cell button, because Outlook ignores padding on <a>.

    Returns nothing at all when there is no URL, so an unpublished run degrades
    to the plain summary rather than rendering a link to nowhere.
    """
    if not url:
        return ""
    return (
        '<tr><td style="padding-top:16px">'
        '<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
        f'<td bgcolor="#16181B" style="border-radius:4px">'
        f'<a href="{_esc(url)}" style="display:inline-block;padding:11px 20px;'
        f'font:500 14px/1 -apple-system,Segoe UI,Helvetica,sans-serif;'
        f'color:#F7F7F3;text-decoration:none">Read the full report &rarr;</a>'
        '</td></tr></table></td></tr>')


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def compose(alerts: List[dict], as_of: dt.date, synthetic: bool,
            report_html: Optional[str] = None,
            links: Optional[Dict[str, str]] = None) -> Message:
    """
    Build the alert email. Inline styles and tables only.

    Deliberately short. If the email tries to reproduce the report you will skim
    it; if it gives you the verdict and one line of evidence per industry, you
    will read it and follow the link when it matters.

    `links` maps industry name to the published report URL. When it is present
    the email carries links and no attachment: the hosted page keeps the
    progressive disclosure that an inbox would strip, and the reader gets the
    real report instead of a download. Without it the report is attached, so
    a local or unconfigured run still delivers something readable.
    """
    links = links or {}
    top = max(alerts, key=lambda a: a["assessment"].score)
    ta = top["assessment"]
    n = len(alerts)

    subject = (f"DipDector: {ta.metrics.industry} "
               f"{ta.metrics.median_return:+.1%}"
               + (f" (+{n - 1} more)" if n > 1 else ""))
    if synthetic:
        subject = "[SYNTHETIC] " + subject

    text_parts, html_parts = [], []

    if synthetic:
        text_parts.append("*** SYNTHETIC DATA — not a real market observation ***\n")
        html_parts.append(
            '<div style="border:1px solid #9E2B21;color:#9E2B21;padding:10px 12px;'
            'font:14px -apple-system,Segoe UI,sans-serif;margin-bottom:20px">'
            '<b>Synthetic data.</b> Not a real market observation.</div>')

    for al in sorted(alerts, key=lambda a: -a["assessment"].score):
        a = al["assessment"]
        m = a.metrics
        colour = LEVEL_COLOR.get(a.level.value, "#565B62")
        word = LEVEL_WORD.get(a.level.value, a.level.value)
        action, rationale = narrative.suggestion(a)

        text_parts.append(
            f"{word.upper()} — {narrative.headline(a)}\n"
            f"{narrative.subhead(a)}\n"
            f"{narrative.unusualness(a)}\n\n"
            f"  {action}\n  {rationale}\n\n"
            f"  Score {a.score:.0f}/100 · "
            f"{m.n_declining}/{m.n_members} falling · "
            f"{m.relative_to_market:+.1%} vs S&P 500"
            + (f" · {m.benchmark_ticker} {m.benchmark_return:+.1%}"
               if m.benchmark_return is not None else "")
            + f"\n  Why: {al.get('reason', '')}\n"
            + (f"\n  Full report: {links[a.industry]}\n"
               if links.get(a.industry) else ""))

        html_parts.append(f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="margin-bottom:26px;font-family:-apple-system,Segoe UI,Helvetica,sans-serif">
<tr><td style="padding-bottom:6px">
  <span style="font:600 11px/1 monospace;letter-spacing:.12em;color:{colour}">
    &#9679; {_esc(word.upper())}</span></td></tr>
<tr><td style="font:400 22px/1.3 Georgia,serif;color:#16181B;padding-bottom:8px">
  {_esc(narrative.headline(a))}</td></tr>
<tr><td style="font:400 15px/1.5 -apple-system,Segoe UI,sans-serif;color:#565B62;
  padding-bottom:14px">{_esc(narrative.subhead(a))}
  {_esc(narrative.unusualness(a))}</td></tr>
<tr><td style="background:#ECECE6;border-left:3px solid {colour};padding:14px 16px">
  <div style="font:400 18px/1.3 Georgia,serif;color:{colour};padding-bottom:6px">
    {_esc(action)}</div>
  <div style="font:400 14px/1.55 -apple-system,Segoe UI,sans-serif;color:#16181B">
    {_esc(rationale)}</div></td></tr>
<tr><td style="padding-top:12px;font:400 13px/1.6 monospace;color:#565B62">
  Score {a.score:.0f}/100 &middot; {m.n_declining}/{m.n_members} falling &middot;
  {m.relative_to_market:+.1%} vs S&amp;P 500{
    f" &middot; {_esc(m.benchmark_ticker)} {m.benchmark_return:+.1%}"
    if m.benchmark_return is not None else ""}</td></tr>
{_button(links.get(a.industry))}
</table>""")

    if links:
        where = ("Full reports are linked above."
                 if len(alerts) > 1 else "The full report is linked above.")
    else:
        where = "The full report is attached — open it in a browser."
    footer_text = (
        f"\nAs of {as_of}. {where}\n"
        f"Research output only. A detected shock is not a reason to buy anything.\n")
    footer_html = (
        f'<hr style="border:none;border-top:1px solid #D8D8D1;margin:8px 0 14px">'
        f'<div style="font:400 12px/1.6 -apple-system,Segoe UI,sans-serif;color:#565B62">'
        f'As of {_esc(as_of)}. {_esc(where)} Email clients strip the '
        f'interactive sections, so the detail lives on the page rather than '
        f'in this message.<br>'
        f'Research output only. A detected shock is not a reason to buy anything.'
        f'</div>')

    html = (f'<body style="margin:0;padding:24px;background:#F7F7F3">'
            f'<div style="max-width:600px;margin:0 auto">'
            f'{"".join(html_parts)}{footer_html}</div></body>')

    attachments = []
    if report_html and not links:
        attachments.append((f"dipdector-{as_of}.html", "text/html",
                            report_html.encode("utf-8")))

    return Message(subject=subject, text="\n".join(text_parts) + footer_text,
                   html=html, attachments=attachments)


def compose_heartbeat(as_of: dt.date, days_quiet: int, state_summary: List[str],
                      universe_size: int) -> Message:
    """
    Periodic "still running, nothing fired" note.

    Worth sending. A detector that alerts once a year is indistinguishable from
    a detector that broke silently in March, and you would not find out until
    you needed it.
    """
    lines = [f"DipDector ran on {as_of}. Nothing met the trigger conditions.",
             f"{days_quiet} runs since the last alert. "
             f"{universe_size} companies monitored.", ""]
    if state_summary:
        lines.append("Most recent alert per industry:")
        lines += [f"  {s}" for s in state_summary]
    lines.append("\nThis note exists so silence tells you the job is alive "
                 "rather than broken.")
    text = "\n".join(lines)
    html = (f'<body style="margin:0;padding:24px;background:#F7F7F3">'
            f'<div style="max-width:600px;margin:0 auto;font:400 14px/1.6 '
            f'-apple-system,Segoe UI,sans-serif;color:#16181B">'
            f'<pre style="font:400 13px/1.6 monospace;white-space:pre-wrap;'
            f'margin:0">{_esc(text)}</pre></div></body>')
    return Message(subject=f"DipDector: all quiet ({as_of})", text=text, html=html)
